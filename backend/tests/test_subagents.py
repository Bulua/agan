from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.engine.chat_runner import ChatRunManager, ChatRunSpec
from app.engine.llm.chat_client import OpenAICompatChatClient
from app.engine.llm.types import LLMMessage, LLMModelConfig, TokenUsage, ToolCallRequest
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.memory.store import build_shared_blackboard_pin, read_memory_file
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.subagents.manager import SUBAGENT_TOOL_NAMES, SubagentCoordinator
from app.engine.subagents.tools import attach_subagent_tools_to_run
from app.engine.tools.base import ToolExecutionContext
from app.engine.tools.builtin.sandbox_tools import ReadFileTool, WriteFileTool
from app.engine.tools.registry import ToolRegistry
from app.models.chat import ChatConversation


@pytest.mark.asyncio
async def test_parallel_subagents_shared_blackboard_and_followup_messaging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ws_dir = tmp_path / "ws"
    shared_dir = ws_dir / ".agan" / "shared"
    mem_root = tmp_path / "memories"

    sbx = SimulatedSandbox("multi-1", workspace_host_dir=ws_dir, seed_demo_if_empty=False)
    await sbx.write_file("/workspace/service.conf", "PORT=8443\nSECRET=alpha-99\n")

    parent_reg = ToolRegistry()
    parent_reg.register(ReadFileTool(), enabled=True)
    parent_reg.register(WriteFileTool(), enabled=True)

    updates: list[dict[str, Any]] = []
    coord = SubagentCoordinator(
        parent_session_id="conv-multi-1",
        parent_agent_id="parent-1",
        parent_agent_name="Lead Coordinator",
        parent_runtime_cfg=LLMModelConfig(
            name="Mock",
            base_url="http://mock",
            api_key="sk-test",
            model_id="mock-gpt",
        ),
        parent_tool_registry=parent_reg,
        sandbox=sbx,
        shared_memory_dir=shared_dir,
        memories_root=mem_root,
        on_update=lambda s: updates.append(s),
    )
    prompt_block = attach_subagent_tools_to_run(
        parent_reg,
        coord,
        available_agents=[{"name": "Security Auditor", "description": "Audits code"}],
    )
    assert "spawn_subagent" in prompt_block
    assert "Security Auditor" in prompt_block

    async def _fake_stream_chat(
        self: OpenAICompatChatClient,
        messages: list[LLMMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        sys_text = messages[0].content if messages else ""
        tool_names = {t["function"]["name"] for t in (tools or [])}
        # Verify recursion guard: subagents do NOT receive subagent-spawning tools
        assert not (tool_names & SUBAGENT_TOOL_NAMES)

        # Follow-up message turn on subagent 1
        if any("Double check PORT" in (m.content or "") for m in messages):
            yield {"type": "content_delta", "delta": "Confirmed: PORT is 8443 in /workspace/service.conf."}
            yield {
                "type": "done",
                "model": "mock-gpt",
                "usage": TokenUsage(prompt_tokens=80, completion_tokens=15, total_tokens=95).model_dump(),
                "tool_calls": [],
            }
            return

        if "Config Analyst" in sys_text:
            if not any(m.role == "tool" and m.name == "read_file" for m in messages):
                yield {
                    "type": "done",
                    "model": "mock-gpt",
                    "usage": TokenUsage(prompt_tokens=60, completion_tokens=20, total_tokens=80).model_dump(),
                    "tool_calls": [
                        ToolCallRequest(
                            id="s1_r", name="read_file", arguments={"path": "/workspace/service.conf"}
                        ).model_dump()
                    ],
                }
            elif not any(m.role == "tool" and m.name == "memory_write" for m in messages):
                yield {
                    "type": "done",
                    "model": "mock-gpt",
                    "usage": TokenUsage(prompt_tokens=70, completion_tokens=20, total_tokens=90).model_dump(),
                    "tool_calls": [
                        ToolCallRequest(
                            id="s1_m",
                            name="memory_write",
                            arguments={
                                "path": "service-intel.md",
                                "scope": "shared",
                                "title": "Service Config Intel",
                                "memory_type": "finding",
                                "content": "PORT=8443, SECRET=alpha-99 found in /workspace/service.conf.",
                            },
                        ).model_dump()
                    ],
                }
            else:
                yield {"type": "content_delta", "delta": "Published service intel to shared/service-intel.md."}
                yield {
                    "type": "done",
                    "model": "mock-gpt",
                    "usage": TokenUsage(prompt_tokens=80, completion_tokens=15, total_tokens=95).model_dump(),
                    "tool_calls": [],
                }
            return

        # Subagent 2: Artifact Writer
        if not any(m.role == "tool" and m.name == "write_file" for m in messages):
            yield {
                "type": "done",
                "model": "mock-gpt",
                "usage": TokenUsage(prompt_tokens=60, completion_tokens=20, total_tokens=80).model_dump(),
                "tool_calls": [
                    ToolCallRequest(
                        id="s2_w",
                        name="write_file",
                        arguments={"path": "report.txt", "content": "Generated by Subagent 2\n"},
                    ).model_dump()
                ],
            }
        else:
            yield {"type": "content_delta", "delta": "Created /workspace/report.txt."}
            yield {
                "type": "done",
                "model": "mock-gpt",
                "usage": TokenUsage(prompt_tokens=70, completion_tokens=15, total_tokens=85).model_dump(),
                "tool_calls": [],
            }

    monkeypatch.setattr(OpenAICompatChatClient, "stream_chat", _fake_stream_chat)

    tool_ctx = ToolExecutionContext(
        session_id="conv-multi-1",
        sandbox=sbx,
        scratchpad=WorkingScratchpad(),
    )

    # 1. Spawn 2 subagents in parallel (wait=False)
    sp1 = await parent_reg.execute(
        "spawn_subagent",
        {"role": "Config Analyst", "task": "Inspect service.conf and publish findings.", "wait": False},
        tool_ctx,
    )
    sp2 = await parent_reg.execute(
        "spawn_subagent",
        {"role": "Artifact Writer", "task": "Create report.txt in workspace.", "wait": False},
        tool_ctx,
    )
    assert sp1.success is True
    assert sp2.success is True
    sub1_id = sp1.metadata["subagent_id"]
    sub2_id = sp2.metadata["subagent_id"]

    # 2. Wait for both parallel subagents to complete
    waited = await parent_reg.execute("wait_subagents", {"wait": True}, tool_ctx)
    assert waited.success is True
    assert waited.metadata["count"] == 2
    assert waited.metadata["done_count"] == 2
    assert "shared/service-intel.md" in waited.output
    assert "report.txt" in waited.output

    # 3. Verify shared workspace & shared blackboard artifacts were persisted cleanly
    assert (ws_dir / "report.txt").is_file()
    shared_note = read_memory_file(shared_dir, "service-intel.md")
    assert "alpha-99" in shared_note["content"]
    assert "Config Analyst" in shared_note["author"]
    assert "service-intel.md" in build_shared_blackboard_pin(shared_dir)

    # 4. Follow-up message wakes completed Subagent 1 in its preserved isolated context
    follow = await parent_reg.execute(
        "send_agent_message",
        {"subagent_id": sub1_id, "message": "Double check PORT value.", "wait": True},
        tool_ctx,
    )
    assert follow.success is True
    assert "Confirmed: PORT is 8443" in follow.output
    assert sub2_id != sub1_id
    assert len(updates) >= 4

    await coord.shutdown_all()


@pytest.mark.asyncio
async def test_chat_run_manager_spawns_subagent_and_streams_updates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import app.engine.chat_runner as chat_runner_mod

    db_file = tmp_path / "runner_subagent.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    import app.models.agent  # noqa: F401
    import app.models.capability  # noqa: F401
    import app.models.chat  # noqa: F401
    import app.models.llm  # noqa: F401

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(chat_runner_mod, "async_session_factory", session_maker)

    async with session_maker() as db:
        conv = ChatConversation(title="Subagent Stream Test")
        db.add(conv)
        await db.commit()
        conv_id = conv.id

    async def _fake_stream_chat(
        self: OpenAICompatChatClient,
        messages: list[LLMMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        sys_text = messages[0].content if messages else ""
        # Subagent branch
        if "specialist subagent with role: **Quick Checker**" in sys_text:
            yield {"type": "content_delta", "delta": "Quick Checker verified everything is OK."}
            yield {
                "type": "done",
                "model": "mock-gpt",
                "usage": TokenUsage(prompt_tokens=50, completion_tokens=10, total_tokens=60).model_dump(),
                "tool_calls": [],
            }
            return

        # Parent coordinator branch
        if not any(m.role == "tool" and m.name == "spawn_subagent" for m in messages):
            yield {
                "type": "done",
                "model": "mock-gpt",
                "usage": TokenUsage(prompt_tokens=90, completion_tokens=20, total_tokens=110).model_dump(),
                "tool_calls": [
                    ToolCallRequest(
                        id="parent_sp_1",
                        name="spawn_subagent",
                        arguments={
                            "role": "Quick Checker",
                            "task": "Verify system health.",
                            "wait": True,
                        },
                    ).model_dump()
                ],
            }
        else:
            yield {"type": "content_delta", "delta": "Subagent confirmed system health is OK."}
            yield {
                "type": "done",
                "model": "mock-gpt",
                "usage": TokenUsage(prompt_tokens=120, completion_tokens=15, total_tokens=135).model_dump(),
                "tool_calls": [],
            }

    monkeypatch.setattr(OpenAICompatChatClient, "stream_chat", _fake_stream_chat)

    reg = ToolRegistry()
    reg.register(ReadFileTool(), enabled=True)

    mgr = ChatRunManager()
    spec = ChatRunSpec(
        conversation_id=conv_id,
        title="Subagent Stream Test",
        agent_name="Coordinator",
        model_db_id="m1",
        runtime_cfg=LLMModelConfig(
            name="Mock",
            base_url="http://mock",
            api_key="sk-test",
            model_id="mock-gpt",
        ),
        llm_messages=[
            LLMMessage(role="system", content="Coordinate work."),
            LLMMessage(role="user", content="Delegate a quick check."),
        ],
        tool_registry=reg,
        use_agent_tools=True,
        sandbox_mode="simulated",
        sandbox_image="agan-ctf-sandbox:latest",
        max_tool_rounds=6,
        user_message=None,
        shared_memory_dir=tmp_path / "shared",
        enable_subagents=True,
    )

    run, q = mgr.start_run(spec)
    events: list[dict[str, Any]] = []
    async for evt in mgr.stream_from_queue(run, q):
        events.append(evt)

    assert any(e.get("type") == "subagent_update" for e in events)
    done_evts = [e for e in events if e.get("type") == "done"]
    assert len(done_evts) == 1
    assert "Subagent confirmed" in done_evts[0]["assistant_message"]["content"]
    tool_calls = done_evts[0]["assistant_message"]["tool_calls_json"]
    assert any(tc["tool_name"] == "spawn_subagent" and tc["success"] is True for tc in tool_calls)

    await test_engine.dispose()
