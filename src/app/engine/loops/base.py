from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.core.events import SessionEventBus, TraceEventType, TraceUsage
from app.engine.guardrails.compactor import ContextCompactor
from app.engine.guardrails.limits import BudgetTracker, GuardrailConfig
from app.engine.guardrails.loop_detector import DeadLoopDetector
from app.engine.llm.chat_client import BaseChatClient
from app.engine.llm.types import LLMMessage, LLMResponse, TokenUsage
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.sandbox.base import BaseSandbox
from app.engine.tools.base import ToolExecutionContext
from app.engine.tools.registry import ToolRegistry

DEFAULT_CTF_SYSTEM_PROMPT = """You are AGAN, an elite autonomous CTF (Capture The Flag) security researcher operating inside an isolated Ubuntu 22.04 sandbox container.

## Operating Guidelines
1. Inspect `/workspace/challenge` first if attachments exist. Keep working scripts under `/workspace`.
2. Think systematically before every tool call: state your hypothesis and why the chosen command tests it.
3. Use `scratchpad_update` whenever you uncover verified clues, open ports/endpoints, vulnerabilities, offsets, credentials, or ruled-out dead ends — the Working Scratchpad is preserved even if earlier turns are compacted.
4. Never repeat the same failing command blindly. If an approach fails, analyze the error, record the dead end, and pivot.
5. For large files or truncated outputs, inspect targeted sections using `grep`, `head`, `tail`, `sed`, or `read_file` with line ranges.
6. As soon as you recover a valid flag matching the challenge's format, call `submit_flag` immediately."""


class LoopStatus(StrEnum):
    SOLVED = "solved"
    FAILED = "failed"
    STOPPED = "stopped"
    GUARDRAIL_TRIPPED = "guardrail_tripped"


class ChallengeBrief(BaseModel):
    title: str
    category: str = "misc"
    description: str = ""
    target_info: dict[str, Any] = Field(default_factory=dict)
    attachments: list[str] = Field(default_factory=list)
    flag_regex: str = r"flag\{[^}\s]+\}"


class LoopResult(BaseModel):
    status: LoopStatus
    detected_flag: str | None = None
    steps_executed: int = 0
    total_usage: TokenUsage = Field(default_factory=TokenUsage)
    summary: str = ""
    error: str | None = None


@dataclass
class LoopContext:
    """Shared execution context for any switchable reasoning loop engine."""

    session_id: str
    challenge: ChallengeBrief
    sandbox: BaseSandbox
    llm_client: BaseChatClient
    tool_registry: ToolRegistry
    event_bus: SessionEventBus
    scratchpad: WorkingScratchpad = field(default_factory=WorkingScratchpad)
    guardrails: GuardrailConfig = field(default_factory=GuardrailConfig)
    agent_name: str = "main"
    system_prompt: str = DEFAULT_CTF_SYSTEM_PROMPT
    allowed_tools: set[str] | None = None
    window_name: str = "ctf_main"
    # Human-in-the-loop (HITL) control primitives
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    steering_queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    on_flag_submitted: Callable[[str], Awaitable[dict[str, Any]]] | None = None

    def __post_init__(self) -> None:
        # Unpaused (running) by default
        self.pause_event.set()

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        # Unblock if paused so the loop exits immediately
        self.pause_event.set()

    def inject_steering(self, guidance: str, *, resume_if_paused: bool = False) -> None:
        self.steering_queue.put_nowait(guidance)
        if resume_if_paused:
            self.pause_event.set()


class BaseLoopEngine(ABC):
    """Strategy base class for switchable CTF reasoning loops."""

    engine_name: str = "base"

    @abstractmethod
    async def run(self, ctx: LoopContext) -> LoopResult:
        """Run the reasoning loop until solved, stopped, or guardrail tripped."""

    def _build_initial_messages(self, ctx: LoopContext) -> list[LLMMessage]:
        challenge = ctx.challenge
        target_lines = (
            "\n".join(f"- {k}: {v}" for k, v in challenge.target_info.items())
            if challenge.target_info
            else "- (Local/offline challenge — no remote target specified)"
        )
        attachment_lines = (
            "\n".join(f"- /workspace/challenge/{name}" for name in challenge.attachments)
            if challenge.attachments
            else "- (No attachments listed; check `/workspace/challenge` to verify)"
        )
        brief_md = (
            f"# CTF Challenge: {challenge.title}\n"
            f"- **Category**: {challenge.category}\n"
            f"- **Expected Flag Regex**: `{challenge.flag_regex}`\n\n"
            f"## Description\n{challenge.description or '(No description provided)'}\n\n"
            f"## Target Connection Info\n{target_lines}\n\n"
            f"## Attachments\n{attachment_lines}\n\n"
            f"{ctx.scratchpad.to_prompt_block()}"
        )
        return [
            LLMMessage(role="system", content=ctx.system_prompt),
            LLMMessage(role="user", content=brief_md),
        ]

    @staticmethod
    def _scan_for_flag(text: str, flag_regex: str) -> str | None:
        if not text or not flag_regex:
            return None
        match = re.search(flag_regex, text)
        return match.group(0) if match else None

    async def _check_hitl_and_steering(
        self,
        ctx: LoopContext,
        messages: list[LLMMessage],
        step_index: int,
    ) -> bool:
        """Handle pause/resume, stop signal, and drain human steering messages.

        Returns False if execution should terminate immediately (stopped).
        """
        if not ctx.pause_event.is_set():
            await ctx.event_bus.emit(
                ctx.session_id,
                TraceEventType.STATUS,
                {"status": "paused", "message": "Session paused awaiting human operator."},
                step_index=step_index,
                agent_name=ctx.agent_name,
            )
            await ctx.pause_event.wait()
            if not ctx.stop_event.is_set():
                await ctx.event_bus.emit(
                    ctx.session_id,
                    TraceEventType.STATUS,
                    {"status": "running", "message": "Session resumed."},
                    step_index=step_index,
                    agent_name=ctx.agent_name,
                )

        if ctx.stop_event.is_set():
            return False

        # Drain any queued human steering prompts
        steerings: list[str] = []
        while not ctx.steering_queue.empty():
            try:
                steerings.append(ctx.steering_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        for guidance in steerings:
            await ctx.event_bus.emit(
                ctx.session_id,
                TraceEventType.HUMAN_STEERING,
                {"message": guidance},
                step_index=step_index,
                agent_name=ctx.agent_name,
            )
            messages.append(
                LLMMessage(
                    role="user",
                    content=(
                        "[HUMAN OPERATOR STEERING — HIGH PRIORITY]\n"
                        f"{guidance}\n\n"
                        "Incorporate this guidance immediately in your next step."
                    ),
                )
            )
        return True

    async def _maybe_compact_context(
        self,
        ctx: LoopContext,
        compactor: ContextCompactor,
        messages: list[LLMMessage],
        last_prompt_tokens: int,
        step_index: int,
    ) -> list[LLMMessage]:
        if compactor.needs_compaction(messages, last_prompt_tokens):
            before_count = len(messages)
            messages = await compactor.compact(messages, ctx.scratchpad, ctx.llm_client)
            await ctx.event_bus.emit(
                ctx.session_id,
                TraceEventType.GUARDRAIL,
                {
                    "kind": "context_compacted",
                    "messages_before": before_count,
                    "messages_after": len(messages),
                },
                step_index=step_index,
                agent_name=ctx.agent_name,
            )
        return messages

    async def _invoke_llm(
        self,
        ctx: LoopContext,
        messages: list[LLMMessage],
        budget: BudgetTracker,
        step_index: int,
        *,
        use_tools: bool = True,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        tools = (
            ctx.tool_registry.get_openai_schemas(ctx.allowed_tools)
            if use_tools
            else None
        )
        resp = await asyncio.wait_for(
            ctx.llm_client.chat(
                messages,
                tools=tools,
                response_format=response_format,
            ),
            timeout=ctx.guardrails.step_timeout_sec,
        )
        budget.record_usage(resp.usage)

        await ctx.event_bus.emit(
            ctx.session_id,
            TraceEventType.TOKEN_USAGE,
            {
                "step_usage": resp.usage.model_dump(),
                "cumulative_usage": budget.usage.model_dump(),
                "model": resp.model,
            },
            step_index=step_index,
            agent_name=ctx.agent_name,
            usage=TraceUsage(
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                total_tokens=resp.usage.total_tokens,
                cost_usd=resp.usage.cost_usd,
                latency_ms=resp.latency_ms,
            ),
        )
        return resp

    async def _execute_tool_calls(
        self,
        ctx: LoopContext,
        resp: LLMResponse,
        messages: list[LLMMessage],
        detector: DeadLoopDetector,
        step_index: int,
    ) -> tuple[str | None, list[str]]:
        """Execute all tool calls in `resp`, emit trace events, and detect flags or dead loops."""
        tool_ctx = ToolExecutionContext(
            session_id=ctx.session_id,
            sandbox=ctx.sandbox,
            scratchpad=ctx.scratchpad,
            window_name=ctx.window_name,
            flag_regex=ctx.challenge.flag_regex,
            on_flag_submitted=ctx.on_flag_submitted,
        )

        accepted_flag: str | None = None
        interventions: list[str] = []

        for tc in resp.tool_calls:
            await ctx.event_bus.emit(
                ctx.session_id,
                TraceEventType.TOOL_CALL,
                {
                    "tool_call_id": tc.id,
                    "tool_name": tc.name,
                    "arguments": tc.arguments,
                },
                step_index=step_index,
                agent_name=ctx.agent_name,
            )

            loop_check = detector.record_and_check(tc.name, tc.arguments)
            if loop_check.detected:
                await ctx.event_bus.emit(
                    ctx.session_id,
                    TraceEventType.GUARDRAIL,
                    {
                        "kind": "dead_loop_detected",
                        "repeat_count": loop_check.repeat_count,
                        "signature": loop_check.matched_signature,
                    },
                    step_index=step_index,
                    agent_name=ctx.agent_name,
                )
                interventions.append(loop_check.reflexion_prompt)
                messages.append(
                    LLMMessage(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=(
                            f"[BLOCKED BY DEAD-LOOP GUARDRAIL]\n{loop_check.reflexion_prompt}"
                        ),
                    )
                )
                continue

            try:
                result = await asyncio.wait_for(
                    ctx.tool_registry.execute(tc.name, tc.arguments, tool_ctx),
                    timeout=ctx.guardrails.step_timeout_sec,
                )
            except TimeoutError:
                result_text = (
                    f"[TOOL EXECUTION TIMED OUT after {ctx.guardrails.step_timeout_sec}s]"
                )
                await ctx.event_bus.emit(
                    ctx.session_id,
                    TraceEventType.TOOL_RESULT,
                    {
                        "tool_call_id": tc.id,
                        "tool_name": tc.name,
                        "success": False,
                        "output": result_text,
                        "error": "timeout",
                    },
                    step_index=step_index,
                    agent_name=ctx.agent_name,
                )
                messages.append(
                    LLMMessage(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=result_text,
                    )
                )
                continue

            rendered_output = result.output if result.output else (result.error or "(empty)")
            await ctx.event_bus.emit(
                ctx.session_id,
                TraceEventType.TOOL_RESULT,
                {
                    "tool_call_id": tc.id,
                    "tool_name": tc.name,
                    "success": result.success,
                    "output": rendered_output,
                    "error": result.error,
                    "metadata": result.metadata,
                },
                step_index=step_index,
                agent_name=ctx.agent_name,
            )

            if tc.name == "scratchpad_update" and result.success:
                await ctx.event_bus.emit(
                    ctx.session_id,
                    TraceEventType.SCRATCHPAD_UPDATE,
                    {"scratchpad": ctx.scratchpad.model_dump()},
                    step_index=step_index,
                    agent_name=ctx.agent_name,
                )

            messages.append(
                LLMMessage(
                    role="tool",
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=rendered_output,
                )
            )

            # Check explicit accepted submit_flag
            if tc.name == "submit_flag" and result.success and result.metadata.get("accepted"):
                accepted_flag = str(result.metadata.get("flag"))
                await ctx.event_bus.emit(
                    ctx.session_id,
                    TraceEventType.FLAG_DETECTED,
                    {"flag": accepted_flag, "source": "submit_flag", "accepted": True},
                    step_index=step_index,
                    agent_name=ctx.agent_name,
                )
                break

            # Passive regex scan on tool output (notify agent to verify & submit)
            if tc.name != "submit_flag":
                candidate = self._scan_for_flag(rendered_output, ctx.challenge.flag_regex)
                if candidate:
                    await ctx.event_bus.emit(
                        ctx.session_id,
                        TraceEventType.FLAG_DETECTED,
                        {"flag": candidate, "source": f"tool:{tc.name}", "accepted": False},
                        step_index=step_index,
                        agent_name=ctx.agent_name,
                    )
                    if ctx.on_flag_submitted is not None:
                        sub_res = await ctx.on_flag_submitted(candidate)
                        if sub_res.get("accepted"):
                            accepted_flag = candidate
                            break
                    else:
                        # Auto-accept candidate flag when no external validator rejects it
                        interventions.append(
                            f"[SYSTEM NOTICE] Potential flag `{candidate}` detected in tool output! "
                            f"Verify it and call `submit_flag(flag=\"{candidate}\")` immediately."
                        )

        return accepted_flag, interventions
