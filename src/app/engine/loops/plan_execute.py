from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.core.events import TraceEventType
from app.engine.guardrails.compactor import ContextCompactor
from app.engine.guardrails.limits import BudgetTracker, GuardrailViolation
from app.engine.guardrails.loop_detector import DeadLoopDetector
from app.engine.llm.types import LLMMessage
from app.engine.loops.base import BaseLoopEngine, LoopContext, LoopResult, LoopStatus


class PlanStep(BaseModel):
    id: int
    title: str
    goal: str
    status: Literal["pending", "in_progress", "done", "skipped", "failed"] = "pending"
    result_summary: str = ""


class PlanPayload(BaseModel):
    strategy_overview: str = ""
    steps: list[PlanStep] = Field(default_factory=list)


PLANNER_INSTRUCTION = """You are the Strategic CTF Planner.
Analyze the challenge brief and produce a concrete, actionable 3-to-6 step solving plan.
Return ONLY valid JSON matching this schema:
{
  "strategy_overview": "High-level hypothesis and approach",
  "steps": [
    {"id": 1, "title": "Short step title", "goal": "Concrete verification/exploit goal"}
  ]
}"""

REPLANNER_INSTRUCTION = """You are the Strategic CTF Replanner.
Review the current plan progress, Working Scratchpad, and the latest step outcome.
Decide whether to continue with the remaining steps or revise them based on new clues.
Return ONLY valid JSON matching this schema:
{
  "action": "continue" | "replan" | "give_up",
  "reason": "Why this decision was made",
  "remaining_steps": [
    {"id": 3, "title": "Updated step title", "goal": "Updated concrete goal"}
  ]
}"""


def _extract_json_object(text: str) -> dict:
    """Robustly parse JSON from an LLM response (handles ```json fences and prose wrappers)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]
    return json.loads(candidate)


class PlanAndExecuteEngine(BaseLoopEngine):
    """Structured Plan -> Execute (bounded sub-ReAct per step) -> Dynamic Replan engine."""

    engine_name = "plan_execute"

    def __init__(self, max_sub_steps_per_plan_item: int = 6) -> None:
        self.max_sub_steps_per_plan_item = max(2, max_sub_steps_per_plan_item)

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

        await ctx.event_bus.emit(
            ctx.session_id,
            TraceEventType.STATUS,
            {"status": "running", "engine": self.engine_name},
            step_index=0,
            agent_name=ctx.agent_name,
        )

        try:
            # --- Phase 1: Initial Planning ---
            if not await self._check_hitl_and_steering(ctx, messages, budget.steps):
                return LoopResult(
                    status=LoopStatus.STOPPED,
                    steps_executed=budget.steps,
                    total_usage=budget.usage,
                    summary="Session stopped before planning.",
                )

            budget.check_limits()
            plan_step_idx = budget.record_step()
            plan = await self._create_initial_plan(ctx, messages, budget, plan_step_idx)
            await self._emit_plan(ctx, plan, plan_step_idx, reason="initial_plan")

            # --- Phase 2 & 3: Execute Step -> Replan Loop ---
            while True:
                next_item = next((s for s in plan.steps if s.status == "pending"), None)
                if next_item is None:
                    break

                next_item.status = "in_progress"
                await self._emit_plan(
                    ctx, plan, budget.steps, reason=f"start_step_{next_item.id}"
                )

                messages.append(
                    LLMMessage(
                        role="user",
                        content=(
                            f"## Current Plan Step #{next_item.id}: {next_item.title}\n"
                            f"**Goal**: {next_item.goal}\n\n"
                            f"{ctx.scratchpad.to_prompt_block()}\n\n"
                            "Execute tools to accomplish this step. Once this step's goal is "
                            "completed (or clearly blocked), summarize your findings concisely "
                            "without calling more tools so the planner can advance. "
                            "If you discover the flag at any point, call `submit_flag` immediately."
                        ),
                    )
                )

                step_summary = ""
                step_failed = False

                for _ in range(self.max_sub_steps_per_plan_item):
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

                    resp = await self._invoke_llm(
                        ctx, messages, budget, step_index, use_tools=True
                    )
                    last_prompt_tokens = resp.usage.prompt_tokens

                    if resp.content or resp.reasoning_content:
                        await ctx.event_bus.emit(
                            ctx.session_id,
                            TraceEventType.THOUGHT,
                            {
                                "content": resp.content,
                                "reasoning_content": resp.reasoning_content,
                                "plan_step_id": next_item.id,
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
                        # Check if assistant reported flag in text
                        candidate = self._scan_for_flag(resp.content, ctx.challenge.flag_regex)
                        if candidate:
                            accepted = True
                            if ctx.on_flag_submitted is not None:
                                sub = await ctx.on_flag_submitted(candidate)
                                accepted = bool(sub.get("accepted"))
                            if accepted:
                                next_item.status = "done"
                                next_item.result_summary = f"Recovered flag {candidate}"
                                await self._emit_plan(ctx, plan, step_index, reason="solved")
                                return LoopResult(
                                    status=LoopStatus.SOLVED,
                                    detected_flag=candidate,
                                    steps_executed=budget.steps,
                                    total_usage=budget.usage,
                                    summary=resp.content,
                                )
                        step_summary = resp.content or "Step completed."
                        break

                    accepted_flag, interventions = await self._execute_tool_calls(
                        ctx, resp, messages, detector, step_index
                    )
                    if accepted_flag:
                        next_item.status = "done"
                        next_item.result_summary = f"Recovered flag {accepted_flag}"
                        await self._emit_plan(ctx, plan, step_index, reason="solved")
                        return LoopResult(
                            status=LoopStatus.SOLVED,
                            detected_flag=accepted_flag,
                            steps_executed=budget.steps,
                            total_usage=budget.usage,
                            summary=f"Solved challenge '{ctx.challenge.title}' with flag {accepted_flag}.",
                        )

                    for hint in interventions:
                        messages.append(LLMMessage(role="user", content=hint))
                else:
                    step_summary = (
                        f"Reached sub-step cap ({self.max_sub_steps_per_plan_item}) for step "
                        f"#{next_item.id}; handing back to Replanner."
                    )
                    step_failed = True

                next_item.status = "failed" if step_failed else "done"
                next_item.result_summary = step_summary[:500]

                # --- Phase 3: Dynamic Replan ---
                if not await self._check_hitl_and_steering(ctx, messages, budget.steps):
                    return LoopResult(
                        status=LoopStatus.STOPPED,
                        steps_executed=budget.steps,
                        total_usage=budget.usage,
                        summary="Session stopped by operator.",
                    )

                budget.check_limits()
                replan_step_idx = budget.record_step()
                should_continue = await self._replan(ctx, plan, budget, replan_step_idx)
                await self._emit_plan(ctx, plan, replan_step_idx, reason="replanned")
                if not should_continue:
                    break

            return LoopResult(
                status=LoopStatus.FAILED,
                steps_executed=budget.steps,
                total_usage=budget.usage,
                summary="Plan exhausted without recovering a valid flag.",
            )

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

    async def _create_initial_plan(
        self,
        ctx: LoopContext,
        base_messages: list[LLMMessage],
        budget: BudgetTracker,
        step_index: int,
    ) -> PlanPayload:
        planner_msgs = [
            LLMMessage(role="system", content=PLANNER_INSTRUCTION),
            base_messages[-1],
        ]
        resp = await self._invoke_llm(ctx, planner_msgs, budget, step_index, use_tools=False)
        try:
            data = _extract_json_object(resp.content)
            raw_steps = data.get("steps") or []
            steps = [
                PlanStep(
                    id=int(item.get("id", idx + 1)),
                    title=str(item.get("title", f"Step {idx + 1}")),
                    goal=str(item.get("goal", "")),
                )
                for idx, item in enumerate(raw_steps)
            ]
            if steps:
                return PlanPayload(
                    strategy_overview=str(data.get("strategy_overview", "")),
                    steps=steps,
                )
        except Exception:
            pass

        # Safe fallback plan if model returned non-JSON prose
        return PlanPayload(
            strategy_overview=resp.content[:300] or "Standard CTF workflow",
            steps=[
                PlanStep(
                    id=1,
                    title="Reconnaissance & Inspection",
                    goal="Inspect /workspace/challenge attachments and probe target service.",
                ),
                PlanStep(
                    id=2,
                    title="Vulnerability / Logic Analysis",
                    goal="Identify the core weakness, crypto flaw, or reverse-engineering target.",
                ),
                PlanStep(
                    id=3,
                    title="Exploitation & Flag Recovery",
                    goal="Write exploit script, recover the flag, and call submit_flag.",
                ),
            ],
        )

    async def _replan(
        self,
        ctx: LoopContext,
        plan: PlanPayload,
        budget: BudgetTracker,
        step_index: int,
    ) -> bool:
        plan_json = json.dumps(plan.model_dump(), ensure_ascii=False, indent=2)
        replan_msgs = [
            LLMMessage(role="system", content=REPLANNER_INSTRUCTION),
            LLMMessage(
                role="user",
                content=(
                    f"## Challenge: {ctx.challenge.title} ({ctx.challenge.category})\n\n"
                    f"## Current Plan State\n```json\n{plan_json}\n```\n\n"
                    f"{ctx.scratchpad.to_prompt_block()}"
                ),
            ),
        ]
        resp = await self._invoke_llm(ctx, replan_msgs, budget, step_index, use_tools=False)
        try:
            data = _extract_json_object(resp.content)
            action = str(data.get("action", "continue")).lower()
            if action == "give_up":
                return False
            if action == "replan":
                completed = [s for s in plan.steps if s.status in ("done", "failed", "skipped")]
                next_id = max((s.id for s in completed), default=0) + 1
                new_steps: list[PlanStep] = []
                for idx, item in enumerate(data.get("remaining_steps") or []):
                    new_steps.append(
                        PlanStep(
                            id=next_id + idx,
                            title=str(item.get("title", f"Step {next_id + idx}")),
                            goal=str(item.get("goal", "")),
                            status="pending",
                        )
                    )
                if new_steps:
                    plan.steps = [*completed, *new_steps]
        except Exception:
            # Keep existing pending steps on parse failure
            pass

        return any(s.status == "pending" for s in plan.steps)

    @staticmethod
    async def _emit_plan(
        ctx: LoopContext,
        plan: PlanPayload,
        step_index: int,
        *,
        reason: str,
    ) -> None:
        await ctx.event_bus.emit(
            ctx.session_id,
            TraceEventType.PLAN_UPDATE,
            {
                "reason": reason,
                "strategy_overview": plan.strategy_overview,
                "steps": [s.model_dump() for s in plan.steps],
            },
            step_index=step_index,
            agent_name=ctx.agent_name,
        )
