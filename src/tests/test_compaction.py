from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.engine.chat_runner import ChatRunManager, ChatRunSpec
from app.engine.guardrails.compactor import MICRO_CLEARED_PREFIX, ContextCompactor
from app.engine.llm.chat_client import BaseChatClient, OpenAICompatChatClient
from app.engine.llm.types import (
    LLMMessage,
    LLMModelConfig,
    LLMResponse,
    TokenUsage,
    ToolCallRequest,
)
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult
from app.engine.tools.registry import ToolRegistry
from app.main import create_app
from app.models.chat import ChatConversation, ChatMessage


class _FakeSummaryClient(BaseChatClient):
    def __init__(self, reply: str = "### Current Goal\nKeep going.\n\n### Completed Work & Verified Facts\n- Checked config.py") -> None:
        self.calls: list[list[LLMMessage]] = []
        self.reply = reply

    async def chat(self, messages: list[LLMMessage], **_kwargs: Any) -> LLMResponse:
        self.calls.append(list(messages))
        return LLMResponse(content=self.reply)


def test_micro_compact_clears_old_large_tool_outputs_and_is_idempotent() -> None:
    compactor = ContextCompactor(
        context_window=128_000,
        keep_recent_tool_results=2,
        micro_min_chars=200,
    )
    big_blob = "X" * 1200
    messages = [
        LLMMessage(role="system", content="You are an agent."),
        LLMMessage(role="user", content="Inspect files"),
        LLMMessage(role="assistant", content="", tool_calls=[ToolCallRequest(id="t1", name="read_file", arguments={"path": "a.py"})]),
        LLMMessage(role="tool", tool_call_id="t1", name="read_file", content=big_blob),
        LLMMessage(role="assistant", content="", tool_calls=[ToolCallRequest(id="t2", name="grep_search", arguments={"pattern": "foo"})]),
        LLMMessage(role="tool", tool_call_id="t2", name="grep_search", content=big_blob),
        LLMMessage(role="assistant", content="", tool_calls=[ToolCallRequest(id="t3", name="read_file", arguments={"path": "b.py"})]),
        LLMMessage(role="tool", tool_call_id="t3", name="read_file", content=big_blob),
        LLMMessage(role="assistant", content="", tool_calls=[ToolCallRequest(id="t4", name="read_file", arguments={"path": "c.py"})]),
        LLMMessage(role="tool", tool_call_id="t4", name="read_file", content=big_blob),
    ]

    first_out, stats1 = compactor.micro_compact(messages)
    assert stats1.stage == "micro"
    assert stats1.cleared_tool_outputs == 2
    assert stats1.tokens_saved > 500
    # Oldest 2 cleared, newest 2 preserved verbatim
    assert first_out[3].content.startswith(MICRO_CLEARED_PREFIX)
    assert first_out[5].content.startswith(MICRO_CLEARED_PREFIX)
    assert first_out[7].content == big_blob
    assert first_out[9].content == big_blob

    # Idempotent on second call
    second_out, stats2 = compactor.micro_compact(first_out)
    assert stats2.stage == "none"
    assert stats2.cleared_tool_outputs == 0
    assert second_out[3].content == first_out[3].content


@pytest.mark.asyncio
async def test_two_stage_auto_compact_skips_llm_if_micro_suffices_and_triggers_when_needed() -> None:
    fake_llm = _FakeSummaryClient()
    compactor = ContextCompactor(
        context_window=4096,
        compact_ratio=0.5,  # threshold = 2048 tokens
        keep_recent=4,
        keep_recent_tool_results=1,
        micro_min_chars=200,
    )

    # Case 1: Large old tool outputs push over 2048 tokens, but micro-compact alone brings it well below
    msgs_micro_only = [
        LLMMessage(role="system", content="System prompt"),
        LLMMessage(role="user", content="Start"),
        LLMMessage(role="assistant", content="Step 1", tool_calls=[ToolCallRequest(id="1", name="read_file", arguments={})]),
        LLMMessage(role="tool", tool_call_id="1", name="read_file", content="A" * 8000),
        LLMMessage(role="assistant", content="Step 2", tool_calls=[ToolCallRequest(id="2", name="read_file", arguments={})]),
        LLMMessage(role="tool", tool_call_id="2", name="read_file", content="short recent output"),
    ]
    out1, stats1 = await compactor.compact_with_stats(msgs_micro_only, llm_client=fake_llm)
    assert stats1.stage == "micro"
    assert stats1.cleared_tool_outputs == 1
    assert len(fake_llm.calls) == 0  # Zero LLM calls needed!
    assert out1[3].content.startswith(MICRO_CLEARED_PREFIX)

    # Case 2: Many dense assistant/user turns stay over 2048 tokens -> triggers Stage 2 LLM summary
    msgs_dense = [LLMMessage(role="system", content="System prompt")]
    for i in range(14):
        msgs_dense.append(LLMMessage(role="user", content=f"Turn {i}: " + ("detail " * 80)))
        msgs_dense.append(LLMMessage(role="assistant", content=f"Reply {i}: " + ("analysis " * 80)))

    pad = WorkingScratchpad(known_clues=["PORT_9000_OPEN"], ruled_out=["NO_SQLI"])
    out2, stats2 = await compactor.compact_with_stats(
        msgs_dense,
        scratchpad=pad,
        pinned_blocks=["## Shared Team Note\nToken found in /workspace/env.txt"],
        llm_client=fake_llm,
        extra_instructions="Preserve port numbers",
    )
    assert stats2.stage == "auto"
    assert len(fake_llm.calls) == 1
    assert "Preserve port numbers" in fake_llm.calls[0][1].content
    assert stats2.tokens_saved > 0
    assert stats2.summarized_messages > 0
    joined = "\n".join(m.content for m in out2)
    assert "PORT_9000_OPEN" in joined
    assert "Shared Team Note" in joined
    assert "Checked config.py" in joined


class _VerboseTool(BaseTool):
    name = "dump_log"
    requires_sandbox = False
    description = "Return a large log snippet."
    parameters_schema = {"type": "object", "properties": {"id": {"type": "string"}}}

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        tag = arguments.get("id", "0")
        return ToolResult(success=True, output=f"LOG-{tag}: " + ("abcdef0123 " * 400))


@pytest.mark.asyncio
async def test_chat_run_manager_auto_compacts_mid_tool_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import app.engine.chat_runner as chat_runner_mod

    db_file = tmp_path / "runner_compact.db"
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
        conv = ChatConversation(title="Auto Compact Run")
        db.add(conv)
        await db.commit()
        conv_id = conv.id

    round_counter = {"n": 0}

    async def _fake_stream_chat(
        self: OpenAICompatChatClient,
        messages: list[LLMMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        round_counter["n"] += 1
        r = round_counter["n"]
        if r <= 5:
            yield {
                "type": "done",
                "model": "mock-gpt",
                "usage": TokenUsage(prompt_tokens=1800, completion_tokens=40, total_tokens=1840).model_dump(),
                "tool_calls": [
                    ToolCallRequest(
                        id=f"call_{r}",
                        name="dump_log",
                        arguments={"id": str(r)},
                    ).model_dump()
                ],
            }
        else:
            yield {"type": "content_delta", "delta": "All logs reviewed after compaction."}
            yield {
                "type": "done",
                "model": "mock-gpt",
                "usage": TokenUsage(prompt_tokens=600, completion_tokens=20, total_tokens=620).model_dump(),
                "tool_calls": [],
            }

    async def _fake_chat(
        self: OpenAICompatChatClient,
        messages: list[LLMMessage],
        **kwargs: Any,
    ) -> LLMResponse:
        return LLMResponse(content="### Completed Work & Verified Facts\n- Reviewed logs 1..4")

    monkeypatch.setattr(OpenAICompatChatClient, "stream_chat", _fake_stream_chat)
    monkeypatch.setattr(OpenAICompatChatClient, "chat", _fake_chat)

    reg = ToolRegistry()
    reg.register(_VerboseTool(), enabled=True)

    mgr = ChatRunManager()
    spec = ChatRunSpec(
        conversation_id=conv_id,
        title="Auto Compact Run",
        agent_name="Generalist",
        model_db_id="m1",
        runtime_cfg=LLMModelConfig(
            name="Mock",
            base_url="http://mock",
            api_key="sk-test",
            model_id="mock-gpt",
            context_window=4096,
        ),
        llm_messages=[
            LLMMessage(role="system", content="You are helpful."),
            LLMMessage(role="user", content="Dump and inspect logs."),
        ],
        tool_registry=reg,
        use_agent_tools=True,
        sandbox_mode="simulated",
        sandbox_image="agan-ctf-sandbox:latest",
        max_tool_rounds=8,
        user_message=None,
        compact_ratio=0.5,
        keep_recent_messages=4,
    )

    run, q = mgr.start_run(spec)
    events: list[dict[str, Any]] = []
    async for evt in mgr.stream_from_queue(run, q):
        events.append(evt)

    assert any(e.get("type") == "context_compacted" for e in events)
    assert any(
        e.get("type") == "tool_result" and e.get("tool", {}).get("tool_name") == "__context_compact__"
        for e in events
    )
    done_evts = [e for e in events if e.get("type") == "done"]
    assert len(done_evts) == 1
    assert "All logs reviewed" in done_evts[0]["assistant_message"]["content"]

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_manual_compact_api_persists_summary_and_slices_next_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db_file = tmp_path / "manual_compact_api.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    import app.models.agent  # noqa: F401
    import app.models.capability  # noqa: F401
    import app.models.chat  # noqa: F401
    import app.models.llm  # noqa: F401

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _fake_chat(
        self: OpenAICompatChatClient,
        messages: list[LLMMessage],
        **kwargs: Any,
    ) -> LLMResponse:
        return LLMResponse(content="### Current Goal\nFinish Phase 2 compaction.\n\n### Completed Work & Verified Facts\n- Built micro & auto compact")

    monkeypatch.setattr(OpenAICompatChatClient, "chat", _fake_chat)

    app = create_app()

    async def override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Seed a Chat LLM + Agent + Conversation with 8 verbose messages
        m_res = await client.post(
            "/api/v1/models",
            json={
                "name": "Compact LLM",
                "model_type": "chat",
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test",
                "model_id": "gpt-4o",
                "context_window": 64000,
            },
        )
        model_id = m_res.json()["id"]
        a_res = await client.post(
            "/api/v1/agents",
            json={
                "name": "Compact Agent",
                "chat_model_id": model_id,
                "loop_engine": "direct_chat",
            },
        )
        agent_id = a_res.json()["id"]
        c_res = await client.post(
            "/api/v1/chat/conversations",
            json={"title": "Long Thread", "agent_id": agent_id},
        )
        conv_id = c_res.json()["id"]

        async with session_maker() as db:
            for i in range(4):
                db.add(
                    ChatMessage(
                        conversation_id=conv_id,
                        role="user",
                        content=f"User question {i}: " + ("context payload " * 60),
                    )
                )
                db.add(
                    ChatMessage(
                        conversation_id=conv_id,
                        role="assistant",
                        content=f"Assistant answer {i}: " + ("detailed response " * 60),
                    )
                )
            conv_row = await db.get(ChatConversation, conv_id)
            assert conv_row is not None
            conv_row.message_count = 8
            await db.commit()

        before_detail = (await client.get(f"/api/v1/chat/conversations/{conv_id}")).json()
        assert before_detail["has_compacted_summary"] is False
        assert before_detail["context_window"] == 64000
        assert before_detail["context_tokens_estimate"] > 1000

        # Call manual /compact
        comp_res = await client.post(
            f"/api/v1/chat/conversations/{conv_id}/compact",
            json={"instructions": "focus on Phase 2", "keep_recent": 2},
        )
        assert comp_res.status_code == 200
        body = comp_res.json()
        assert body["compacted"] is True
        assert body["summarized_messages"] == 6
        assert body["tokens_saved"] > 0
        assert body["compact_message"]["role"] == "compact"
        assert "Finish Phase 2 compaction." in body["summary"]

        # Verify GET returns persisted visual marker & reduced active context estimate
        after_detail = (await client.get(f"/api/v1/chat/conversations/{conv_id}")).json()
        assert after_detail["has_compacted_summary"] is True
        assert after_detail["context_tokens_estimate"] < before_detail["context_tokens_estimate"]
        assert after_detail["messages"][-1]["role"] == "compact"
        # All 8 original messages remain preserved for user scrollback + 1 compact marker
        assert len(after_detail["messages"]) == 9

        # Verify DB persisted checkpoint fields
        async with session_maker() as db:
            conv_db = await db.scalar(select(ChatConversation).where(ChatConversation.id == conv_id))
            assert conv_db is not None
            assert "Finish Phase 2 compaction." in conv_db.compacted_summary
            assert conv_db.compacted_before_message_id is not None

    await test_engine.dispose()
