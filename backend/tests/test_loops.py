from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.core.events import SessionEventBus, TraceEventType
from app.engine.guardrails.limits import GuardrailConfig
from app.engine.llm.chat_client import BaseChatClient
from app.engine.llm.types import (
    LLMMessage,
    LLMModelConfig,
    LLMResponse,
    TokenUsage,
    ToolCallRequest,
)
from app.engine.loops.base import ChallengeBrief, LoopContext, LoopStatus
from app.engine.loops.factory import LoopEngineFactory, create_loop_engine
from app.engine.sandbox.base import BaseSandbox, ExecResult
from app.engine.tools.registry import create_default_tool_registry


class MockSandbox(BaseSandbox):
    """Lightweight in-memory sandbox for deterministic engine unit tests."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.sandbox_id = "mock-sbx"
        self.files: dict[str, str] = dict(files or {})
        self.commands_run: list[str] = []
        self.cwd = "/workspace"

    async def exec_command(
        self, command: str, *, timeout: int | None = None, window_name: str = "ctf_main"
    ) -> ExecResult:
        self.commands_run.append(command)
        if "ls" in command:
            out = "\n".join(sorted(self.files.keys()))
            return ExecResult(command=command, output=out, exit_code=0, cwd=self.cwd)
        if "cat /workspace/challenge/secret.txt" in command:
            out = self.files.get("/workspace/challenge/secret.txt", "")
            return ExecResult(command=command, output=out, exit_code=0, cwd=self.cwd)
        return ExecResult(command=command, output=f"executed: {command}", exit_code=0, cwd=self.cwd)

    async def read_file(
        self, path: str, *, start_line: int | None = None, end_line: int | None = None
    ) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_file(self, path: str, content: str) -> str:
        self.files[path] = content
        return path

    async def get_status(self) -> dict[str, Any]:
        return {"sandbox_id": self.sandbox_id, "status": "running"}

    async def close(self, *, remove: bool = True) -> None:
        return None


class ScriptedChatClient(BaseChatClient):
    """Deterministic scripted LLM that records received messages and replays responses."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.config = LLMModelConfig(model_id="mock-gpt", context_window=32_000)
        self._responses = list(responses)
        self.received_history: list[list[LLMMessage]] = []

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.received_history.append(list(messages))
        if not self._responses:
            return LLMResponse(content="Done.", usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15))
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_react_engine_solves_and_updates_scratchpad() -> None:
    sandbox = MockSandbox(
        files={"/workspace/challenge/secret.txt": "Congrats! Here is flag{react_mvp_ok}"}
    )
    llm = ScriptedChatClient(
        [
            LLMResponse(
                content="Let's inspect the challenge file first.",
                tool_calls=[
                    ToolCallRequest(
                        id="tc_1",
                        name="bash_exec",
                        arguments={"command": "cat /workspace/challenge/secret.txt"},
                    ),
                    ToolCallRequest(
                        id="tc_2",
                        name="scratchpad_update",
                        arguments={"add_clues": ["Found secret.txt in /workspace/challenge"]},
                    ),
                ],
                usage=TokenUsage(prompt_tokens=100, completion_tokens=40, total_tokens=140),
            ),
            LLMResponse(
                content="I found the flag, submitting now.",
                tool_calls=[
                    ToolCallRequest(
                        id="tc_3",
                        name="submit_flag",
                        arguments={"flag": "flag{react_mvp_ok}", "explanation": "Read secret.txt"},
                    )
                ],
                usage=TokenUsage(prompt_tokens=180, completion_tokens=30, total_tokens=210),
            ),
        ]
    )

    bus = SessionEventBus()
    ctx = LoopContext(
        session_id="sess-react-1",
        challenge=ChallengeBrief(title="Warmup", category="misc", attachments=["secret.txt"]),
        sandbox=sandbox,
        llm_client=llm,
        tool_registry=create_default_tool_registry(),
        event_bus=bus,
    )

    engine = create_loop_engine("react")
    result = await engine.run(ctx)

    assert result.status == LoopStatus.SOLVED
    assert result.detected_flag == "flag{react_mvp_ok}"
    assert result.steps_executed == 2
    assert result.total_usage.total_tokens == 350
    assert "Found secret.txt in /workspace/challenge" in ctx.scratchpad.known_clues

    event_types = [e.event_type for e in bus.get_history("sess-react-1")]
    assert TraceEventType.THOUGHT in event_types
    assert TraceEventType.TOOL_CALL in event_types
    assert TraceEventType.TOOL_RESULT in event_types
    assert TraceEventType.SCRATCHPAD_UPDATE in event_types
    assert TraceEventType.FLAG_DETECTED in event_types


@pytest.mark.asyncio
async def test_react_dead_loop_intervention_blocks_repeated_command() -> None:
    sandbox = MockSandbox()
    repeated_call = ToolCallRequest(
        id="tc_rep", name="bash_exec", arguments={"command": "nmap -p 80 target"}
    )
    llm = ScriptedChatClient(
        [
            LLMResponse(content="Try 1", tool_calls=[repeated_call]),
            LLMResponse(content="Try 2", tool_calls=[repeated_call]),
            LLMResponse(content="Try 3", tool_calls=[repeated_call]),
            LLMResponse(
                content="Pivoting and submitting flag.",
                tool_calls=[
                    ToolCallRequest(
                        id="tc_flag", name="submit_flag", arguments={"flag": "flag{pivoted_123}"}
                    )
                ],
            ),
        ]
    )
    bus = SessionEventBus()
    ctx = LoopContext(
        session_id="sess-loop-1",
        challenge=ChallengeBrief(title="LoopTest", category="web"),
        sandbox=sandbox,
        llm_client=llm,
        tool_registry=create_default_tool_registry(),
        event_bus=bus,
        guardrails=GuardrailConfig(repeat_threshold=3),
    )

    result = await create_loop_engine("react").run(ctx)
    assert result.status == LoopStatus.SOLVED
    # The 3rd repeated command should be blocked before reaching sandbox
    assert len(sandbox.commands_run) == 2
    guardrail_events = [
        e for e in bus.get_history("sess-loop-1") if e.event_type == TraceEventType.GUARDRAIL
    ]
    assert any(e.payload.get("kind") == "dead_loop_detected" for e in guardrail_events)


@pytest.mark.asyncio
async def test_react_hitl_pause_and_steering_injection() -> None:
    sandbox = MockSandbox()
    llm = ScriptedChatClient(
        [
            LLMResponse(
                content="Checking directory",
                tool_calls=[
                    ToolCallRequest(id="tc_1", name="bash_exec", arguments={"command": "ls"})
                ],
            ),
            LLMResponse(
                content="Following human guidance!",
                tool_calls=[
                    ToolCallRequest(
                        id="tc_2", name="submit_flag", arguments={"flag": "flag{steered_ok}"}
                    )
                ],
            ),
        ]
    )
    bus = SessionEventBus()
    ctx = LoopContext(
        session_id="sess-hitl-1",
        challenge=ChallengeBrief(title="HITL", category="web"),
        sandbox=sandbox,
        llm_client=llm,
        tool_registry=create_default_tool_registry(),
        event_bus=bus,
    )

    # Pause before start, then inject steering & resume from another coroutine
    ctx.pause()

    async def operator() -> None:
        await asyncio.sleep(0.05)
        ctx.inject_steering("Focus on /robots.txt instead!", resume_if_paused=True)

    op_task = asyncio.create_task(operator())
    result = await create_loop_engine("react").run(ctx)
    await op_task

    assert result.status == LoopStatus.SOLVED
    # First LLM call must have received the injected steering message
    first_prompt_texts = " ".join(m.content or "" for m in llm.received_history[0])
    assert "Focus on /robots.txt instead!" in first_prompt_texts
    assert any(
        e.event_type == TraceEventType.HUMAN_STEERING
        for e in bus.get_history("sess-hitl-1")
    )


@pytest.mark.asyncio
async def test_plan_and_execute_engine_plans_executes_replans_and_solves() -> None:
    sandbox = MockSandbox(
        files={"/workspace/challenge/secret.txt": "flag{plan_execute_win}"}
    )
    initial_plan = json.dumps(
        {
            "strategy_overview": "Inspect files then extract flag",
            "steps": [
                {"id": 1, "title": "List files", "goal": "Check files in challenge dir"},
                {"id": 2, "title": "Read flag", "goal": "Read flag from target file"},
            ],
        }
    )
    replan_resp = json.dumps(
        {
            "action": "replan",
            "reason": "Found secret.txt, directly read and submit it.",
            "remaining_steps": [
                {"id": 2, "title": "Extract secret.txt", "goal": "Cat secret.txt and submit flag"}
            ],
        }
    )

    llm = ScriptedChatClient(
        [
            # 1. Initial Planner response
            LLMResponse(content=initial_plan),
            # 2. Step 1 executor calls bash_exec `ls`
            LLMResponse(
                content="Listing files",
                tool_calls=[
                    ToolCallRequest(id="p1", name="bash_exec", arguments={"command": "ls"})
                ],
            ),
            # 3. Step 1 executor finishes step with summary (no tool calls)
            LLMResponse(content="Found /workspace/challenge/secret.txt."),
            # 4. Replanner revises remaining steps
            LLMResponse(content=replan_resp),
            # 5. Step 2 executor reads secret.txt
            LLMResponse(
                content="Reading secret.txt",
                tool_calls=[
                    ToolCallRequest(
                        id="p2",
                        name="bash_exec",
                        arguments={"command": "cat /workspace/challenge/secret.txt"},
                    )
                ],
            ),
            # 6. Step 2 executor submits flag
            LLMResponse(
                content="Submitting discovered flag.",
                tool_calls=[
                    ToolCallRequest(
                        id="p3",
                        name="submit_flag",
                        arguments={"flag": "flag{plan_execute_win}"},
                    )
                ],
            ),
        ]
    )

    bus = SessionEventBus()
    ctx = LoopContext(
        session_id="sess-pe-1",
        challenge=ChallengeBrief(title="PlanTest", category="misc", attachments=["secret.txt"]),
        sandbox=sandbox,
        llm_client=llm,
        tool_registry=create_default_tool_registry(),
        event_bus=bus,
    )

    assert set(LoopEngineFactory.list_engines()) >= {"react", "plan_execute"}
    engine = create_loop_engine("plan_execute")
    result = await engine.run(ctx)

    assert result.status == LoopStatus.SOLVED
    assert result.detected_flag == "flag{plan_execute_win}"
    plan_events = [
        e for e in bus.get_history("sess-pe-1") if e.event_type == TraceEventType.PLAN_UPDATE
    ]
    reasons = [e.payload.get("reason") for e in plan_events]
    assert "initial_plan" in reasons
    assert "replanned" in reasons
    assert "solved" in reasons
