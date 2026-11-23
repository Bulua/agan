from __future__ import annotations

from app.core.events import TraceEventType
from app.engine.guardrails.compactor import ContextCompactor
from app.engine.guardrails.limits import BudgetTracker, GuardrailViolation
from app.engine.guardrails.loop_detector import DeadLoopDetector
from app.engine.llm.types import LLMMessage
from app.engine.loops.base import BaseLoopEngine, LoopContext, LoopResult, LoopStatus


class ReActEngine(BaseLoopEngine):
    """Classic Thought -> Action (Tool Call) -> Observation loop with guardrails & HITL."""

    engine_name = "react"

    async def run(self, ctx: LoopContext) -> LoopResult:
        budget = BudgetTracker(ctx.guardrails)
        detector = DeadLoopDetector(
            threshold=ctx.guardrails.repeat_threshold,
            similarity_threshold=ctx.guardrails.repeat_similarity,
        )
        compactor = ContextCompactor(
            context_window=ctx.llm_client.config.context_window,
            compact_ratio=ctx.guardrails.context_compact_ratio,
            keep_recent=ctx.guardrails.keep_recent_messages,
        )

        messages = self._build_initial_messages(ctx)
        last_prompt_tokens = 0
        consecutive_no_tool_turns = 0

        await ctx.event_bus.emit(
            ctx.session_id,
            TraceEventType.STATUS,
            {"status": "running", "engine": self.engine_name},
            step_index=0,
            agent_name=ctx.agent_name,
        )

        try:
            while True:
                if not await self._check_hitl_and_steering(ctx, messages, budget.steps):
                    return LoopResult(
                        status=LoopStatus.STOPPED,
                        steps_executed=budget.steps,
                        total_usage=budget.usage,
                        summary="Session stopped by operator.",
                    )

                budget.check_limits()
                step_index = budget.record_step()

                messages = await self._maybe_compact_context(
                    ctx, compactor, messages, last_prompt_tokens, step_index
                )

                resp = await self._invoke_llm(ctx, messages, budget, step_index, use_tools=True)
                last_prompt_tokens = resp.usage.prompt_tokens

                if resp.content or resp.reasoning_content:
                    await ctx.event_bus.emit(
                        ctx.session_id,
                        TraceEventType.THOUGHT,
                        {
                            "content": resp.content,
                            "reasoning_content": resp.reasoning_content,
                        },
                        step_index=step_index,
                        agent_name=ctx.agent_name,
                    )

                messages.append(
                    LLMMessage(
                        role="assistant",
                        content=resp.content,
                        tool_calls=resp.tool_calls,
                    )
                )

                # If LLM directly wrote a flag in text without calling submit_flag
                if not resp.tool_calls:
                    candidate = self._scan_for_flag(resp.content, ctx.challenge.flag_regex)
                    if candidate:
                        accepted = True
                        if ctx.on_flag_submitted is not None:
                            sub = await ctx.on_flag_submitted(candidate)
                            accepted = bool(sub.get("accepted"))
                        await ctx.event_bus.emit(
                            ctx.session_id,
                            TraceEventType.FLAG_DETECTED,
                            {"flag": candidate, "source": "assistant_text", "accepted": accepted},
                            step_index=step_index,
                            agent_name=ctx.agent_name,
                        )
                        if accepted:
                            return LoopResult(
                                status=LoopStatus.SOLVED,
                                detected_flag=candidate,
                                steps_executed=budget.steps,
                                total_usage=budget.usage,
                                summary=resp.content,
                            )

                    consecutive_no_tool_turns += 1
                    if consecutive_no_tool_turns >= 2:
                        return LoopResult(
                            status=LoopStatus.FAILED,
                            steps_executed=budget.steps,
                            total_usage=budget.usage,
                            summary=resp.content or "Agent concluded without recovering flag.",
                        )
                    messages.append(
                        LLMMessage(
                            role="user",
                            content=(
                                "You did not call any tool. Continue investigating using your "
                                "sandbox tools, or call `submit_flag` if you have found the flag."
                            ),
                        )
                    )
                    continue

                consecutive_no_tool_turns = 0
                accepted_flag, interventions = await self._execute_tool_calls(
                    ctx, resp, messages, detector, step_index
                )

                if accepted_flag:
                    return LoopResult(
                        status=LoopStatus.SOLVED,
                        detected_flag=accepted_flag,
                        steps_executed=budget.steps,
                        total_usage=budget.usage,
                        summary=f"Solved challenge '{ctx.challenge.title}' with flag {accepted_flag}.",
                    )

                for hint in interventions:
                    messages.append(LLMMessage(role="user", content=hint))

        except GuardrailViolation as exc:
            await ctx.event_bus.emit(
                ctx.session_id,
                TraceEventType.GUARDRAIL,
                {"kind": exc.code, "reason": exc.reason},
                step_index=budget.steps,
                agent_name=ctx.agent_name,
            )
            return LoopResult(
                status=LoopStatus.GUARDRAIL_TRIPPED,
                steps_executed=budget.steps,
                total_usage=budget.usage,
                error=exc.reason,
                summary=exc.reason,
            )
        except Exception as exc:
            await ctx.event_bus.emit(
                ctx.session_id,
                TraceEventType.ERROR,
                {"error_type": type(exc).__name__, "message": str(exc)},
                step_index=budget.steps,
                agent_name=ctx.agent_name,
            )
            return LoopResult(
                status=LoopStatus.FAILED,
                steps_executed=budget.steps,
                total_usage=budget.usage,
                error=f"{type(exc).__name__}: {exc}",
                summary=f"Execution error: {exc}",
            )
