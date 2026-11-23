from __future__ import annotations

from app.core.events import TraceEventType
from app.engine.guardrails.compactor import ContextCompactor
from app.engine.guardrails.limits import BudgetTracker, GuardrailViolation
from app.engine.guardrails.loop_detector import DeadLoopDetector
from app.engine.llm.types import LLMMessage
from app.engine.loops.base import BaseLoopEngine, LoopContext, LoopResult, LoopStatus

REFLEXION_CRITIQUE_PROMPT = """You are entering a mandatory SELF-REFLEXION checkpoint because recent actions failed or stalled.
Analyze what just happened concisely:
1. **Root Cause**: Why did the last command(s) fail, return empty output, or not advance toward the flag?
2. **Ruled-Out Hypothesis**: What assumption should be discarded?
3. **Revised Attack Vector**: What concrete, different command or tool will you run next?

Keep your critique sharp and actionable (under 150 words)."""


class ReflexionEngine(BaseLoopEngine):
    """Execution loop with automatic failure-triggered self-reflection and strategy revision."""

    engine_name = "reflexion"

    def __init__(self, failure_streak_trigger: int = 2) -> None:
        self.failure_streak_trigger = max(1, failure_streak_trigger)

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
        consecutive_failures = 0
        consecutive_no_tool = 0

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

                    consecutive_no_tool += 1
                    if consecutive_no_tool >= 2:
                        return LoopResult(
                            status=LoopStatus.FAILED,
                            steps_executed=budget.steps,
                            total_usage=budget.usage,
                            summary=resp.content or "Agent stopped without recovering flag.",
                        )
                    await self._run_reflexion(
                        ctx,
                        messages,
                        budget,
                        step_index,
                        trigger="no_tool_call",
                    )
                    continue

                consecutive_no_tool = 0
                before_events_len = len(ctx.event_bus.get_history(ctx.session_id))
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

                # Inspect tool outcomes in this step to detect failure streaks
                recent_events = ctx.event_bus.get_history(ctx.session_id)[before_events_len:]
                step_tool_results = [
                    e for e in recent_events if e.event_type == TraceEventType.TOOL_RESULT
                ]
                had_failure = any(not e.payload.get("success", True) for e in step_tool_results)
                had_dead_loop = any(
                    e.event_type == TraceEventType.GUARDRAIL
                    and e.payload.get("kind") == "dead_loop_detected"
                    for e in recent_events
                )

                if had_failure or had_dead_loop:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if had_dead_loop or consecutive_failures >= self.failure_streak_trigger:
                    trigger_reason = (
                        "dead_loop_detected"
                        if had_dead_loop
                        else f"consecutive_failures_{consecutive_failures}"
                    )
                    budget.check_limits()
                    ref_step = budget.record_step()
                    await self._run_reflexion(
                        ctx,
                        messages,
                        budget,
                        ref_step,
                        trigger=trigger_reason,
                    )
                    consecutive_failures = 0

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

    async def _run_reflexion(
        self,
        ctx: LoopContext,
        messages: list[LLMMessage],
        budget: BudgetTracker,
        step_index: int,
        *,
        trigger: str,
    ) -> None:
        critique_messages = [
            *messages,
            LLMMessage(
                role="user",
                content=f"{REFLEXION_CRITIQUE_PROMPT}\n\n{ctx.scratchpad.to_prompt_block()}",
            ),
        ]
        resp = await self._invoke_llm(
            ctx, critique_messages, budget, step_index, use_tools=False
        )
        critique = resp.content.strip() or "Switching strategy after repeated failures."
        await ctx.event_bus.emit(
            ctx.session_id,
            TraceEventType.REFLEXION,
            {"trigger": trigger, "critique": critique},
            step_index=step_index,
            agent_name=ctx.agent_name,
        )
        messages.append(
            LLMMessage(
                role="user",
                content=(
                    f"[SELF-REFLEXION LESSON ({trigger})]\n{critique}\n\n"
                    "Now execute the revised strategy with your tools (and record ruled-out "
                    "paths via `scratchpad_update`)."
                ),
            )
        )
