from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import create_app


@pytest.fixture
async def api_client(tmp_path):
    db_file = tmp_path / "test_chat.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    import app.models.agent  # noqa: F401
    import app.models.capability  # noqa: F401
    import app.models.chat  # noqa: F401
    import app.models.llm  # noqa: F401

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
        yield client

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_chat_conversations_crud_and_pagination(api_client: AsyncClient) -> None:
    model_resp = await api_client.post(
        "/api/v1/models",
        json={
            "name": "Chat Test Model",
            "model_type": "chat",
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "model_id": "gpt-4o",
        },
    )
    assert model_resp.status_code == 201
    mid = model_resp.json()["id"]

    agent_resp = await api_client.post(
        "/api/v1/agents",
        json={
            "name": "General Security Agent",
            "role": "general",
            "chat_model_id": mid,
            "loop_engine": "direct_chat",
        },
    )
    assert agent_resp.status_code == 201
    aid = agent_resp.json()["id"]

    # Create two conversations bound to Agent
    c1 = (
        await api_client.post(
            "/api/v1/chat/conversations",
            json={"title": "Flask SSTI Analysis", "agent_id": aid},
        )
    ).json()
    c2 = (
        await api_client.post(
            "/api/v1/chat/conversations",
            json={"title": "RSA Wiener Attack"},
        )
    ).json()
    assert c1["agent_id"] == aid
    assert c2["agent_id"] == aid  # auto-picked default agent

    # Rename c1
    upd = await api_client.patch(
        f"/api/v1/chat/conversations/{c1['id']}",
        json={"title": "Flask Jinja2 SSTI Notes", "system_prompt": "Be concise."},
    )
    assert upd.status_code == 200
    assert upd.json()["title"] == "Flask Jinja2 SSTI Notes"

    # Paginated list & search
    page1 = (await api_client.get("/api/v1/chat/conversations?page=1&page_size=1")).json()
    assert page1["total"] == 2
    assert page1["total_pages"] == 2
    assert len(page1["items"]) == 1

    searched = (await api_client.get("/api/v1/chat/conversations?search=Wiener")).json()
    assert searched["total"] == 1
    assert searched["items"][0]["id"] == c2["id"]

    # Detail
    detail = (await api_client.get(f"/api/v1/chat/conversations/{c1['id']}")).json()
    assert detail["messages"] == []
    assert detail["system_prompt"] == "Be concise."

    # Delete
    d = await api_client.delete(f"/api/v1/chat/conversations/{c2['id']}")
    assert d.status_code == 204
    assert (await api_client.get(f"/api/v1/chat/conversations/{c2['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_stream_chat_assembles_tool_calls_and_streams_deltas() -> None:
    """Verify OpenAICompatChatClient.stream_chat streams reasoning/content & assembles split tool_calls."""
    from types import SimpleNamespace

    from app.engine.llm.chat_client import OpenAICompatChatClient
    from app.engine.llm.types import LLMMessage, LLMModelConfig

    cfg = LLMModelConfig(
        name="Mock",
        base_url="http://localhost:9999/v1",
        api_key="sk-mock",
        model_id="mock-reasoner",
    )
    client = OpenAICompatChatClient(cfg)

    fake_chunks = [
        SimpleNamespace(
            model="mock-reasoner",
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        reasoning_content="Let me ",
                        content=None,
                        tool_calls=None,
                    ),
                )
            ],
        ),
        SimpleNamespace(
            model="mock-reasoner",
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        reasoning_content="read the file.",
                        content="Checking file...",
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call_1",
                                function=SimpleNamespace(name="read_", arguments='{"path": '),
                            )
                        ],
                    ),
                )
            ],
        ),
        SimpleNamespace(
            model="mock-reasoner",
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(name="file", arguments='"notes.txt"}'),
                            )
                        ],
                    ),
                )
            ],
        ),
    ]

    async def fake_stream():
        for c in fake_chunks:
            yield c

    async def fake_create(**kwargs):
        assert kwargs.get("stream") is True
        assert kwargs.get("tools") is not None
        return fake_stream()

    client._client.chat.completions.create = fake_create  # type: ignore[method-assign]

    events = []
    async for ev in client.stream_chat(
        [LLMMessage(role="user", content="read notes.txt")],
        tools=[{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    ):
        events.append(ev)

    assert [e["delta"] for e in events if e["type"] == "reasoning_delta"] == [
        "Let me ",
        "read the file.",
    ]
    assert [e["delta"] for e in events if e["type"] == "content_delta"] == ["Checking file..."]
    done = next(e for e in events if e["type"] == "done")
    assert len(done["tool_calls"]) == 1
    assert done["tool_calls"][0]["id"] == "call_1"
    assert done["tool_calls"][0]["name"] == "read_file"
    assert done["tool_calls"][0]["arguments"] == {"path": "notes.txt"}
    assert done["usage"]["total_tokens"] == 15
    await client.close()


@pytest.mark.asyncio
async def test_chat_run_manager_persists_across_disconnect_and_reattaches(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Verify detaching SSE never kills background ChatRun and reattaching replays snapshot + live deltas."""
    import asyncio

    from app.engine import chat_runner
    from app.engine.chat_runner import ChatRunManager, ChatRunSpec
    from app.engine.llm.types import LLMMessage, LLMModelConfig
    from app.engine.tools.registry import ToolRegistry

    mgr = ChatRunManager()
    gate = asyncio.Event()

    async def fake_stream_chat(self, messages, **kwargs):
        yield {"type": "reasoning_delta", "delta": "step1 "}
        yield {"type": "content_delta", "delta": "Hello "}
        await gate.wait()
        yield {"type": "content_delta", "delta": "World!"}
        yield {
            "type": "done",
            "model": "mock-model",
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10, "cost_usd": 0.0},
            "tool_calls": [],
        }

    async def fake_save(**kwargs):
        from datetime import UTC, datetime

        from app.schemas.chat import ChatMessageDTO

        return ChatMessageDTO(
            id="msg-saved-1",
            conversation_id=kwargs["conversation_id"],
            role="assistant",
            content=kwargs["content"],
            reasoning_content=kwargs["reasoning_content"],
            tool_calls_json=kwargs["tool_calls_json"],
            model_id_used=kwargs["model_id_used"],
            created_at=datetime.now(UTC),
        )

    monkeypatch.setattr(chat_runner.OpenAICompatChatClient, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(chat_runner, "_save_assistant_reply", fake_save)

    spec = ChatRunSpec(
        conversation_id="conv-bg-1",
        title="BG Test",
        agent_name="TestAgent",
        model_db_id="m1",
        runtime_cfg=LLMModelConfig(
            name="Mock",
            base_url="http://localhost:9999/v1",
            api_key="sk-mock",
            model_id="mock-model",
        ),
        llm_messages=[LLMMessage(role="user", content="hi")],
        tool_registry=ToolRegistry(),
        use_agent_tools=False,
        sandbox_mode="simulated",
        sandbox_image="agan-ctf-sandbox:latest",
        max_tool_rounds=4,
        user_message=None,
    )

    run, init_q = mgr.start_run(spec)

    # Initial subscriber reads first 2 events then disconnects (simulating F5 or switching conversation)
    seen_initial = []
    async for ev in mgr.stream_from_queue(run, init_q):
        seen_initial.append(ev)
        if ev.get("type") == "content_delta":
            break

    # Task MUST STILL BE RUNNING in background!
    assert mgr.is_running("conv-bg-1") is True

    # Reattach as a brand new subscriber (simulating returning to conversation after F5)
    reattached_events = []

    async def consume_reattach():
        async for ev in mgr.subscribe_existing("conv-bg-1"):
            reattached_events.append(ev)

    consumer = asyncio.create_task(consume_reattach())
    await asyncio.sleep(0.05)
    # Unblock the remaining tokens
    gate.set()
    await consumer

    assert reattached_events[0]["type"] == "snapshot"
    assert reattached_events[0]["content"] == "Hello "
    assert reattached_events[0]["reasoning_content"] == "step1 "
    assert any(
        e["type"] == "content_delta" and e["delta"] == "World!" for e in reattached_events
    )
    done_ev = next(e for e in reattached_events if e["type"] == "done")
    assert done_ev["assistant_message"]["content"] == "Hello World!"
    assert mgr.is_running("conv-bg-1") is False


@pytest.mark.asyncio
async def test_chat_run_manager_live_steering_interrupts_and_pivots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Ctrl+Enter live steering interrupts mid-stream, records steering, and pivots next LLM turn."""
    import asyncio

    from app.engine import chat_runner
    from app.engine.chat_runner import ChatRunManager, ChatRunSpec
    from app.engine.llm.types import LLMMessage, LLMModelConfig
    from app.engine.tools.registry import ToolRegistry

    mgr = ChatRunManager()
    first_chunk_sent = asyncio.Event()
    steer_sent = asyncio.Event()
    observed_prompts: list[list[str]] = []

    async def fake_stream_chat(self, messages, **kwargs):
        observed_prompts.append([f"{m.role}:{m.content}" for m in messages])
        if len(observed_prompts) == 1:
            yield {"type": "content_delta", "delta": "Scanning port 22..."}
            first_chunk_sent.set()
            await steer_sent.wait()
            yield {"type": "content_delta", "delta": "still scanning 22"}
            yield {"type": "done", "model": "mock-model", "usage": {}, "tool_calls": []}
        else:
            yield {"type": "content_delta", "delta": "Pivoting to port 8080 /admin!"}
            yield {"type": "done", "model": "mock-model", "usage": {}, "tool_calls": []}

    async def fake_save(**kwargs):
        from datetime import UTC, datetime

        from app.schemas.chat import ChatMessageDTO

        return ChatMessageDTO(
            id="msg-steered-1",
            conversation_id=kwargs["conversation_id"],
            role="assistant",
            content=kwargs["content"],
            reasoning_content=kwargs["reasoning_content"],
            tool_calls_json=kwargs["tool_calls_json"],
            model_id_used=kwargs["model_id_used"],
            created_at=datetime.now(UTC),
        )

    async def fake_save_user(**kwargs):
        from datetime import UTC, datetime

        from app.schemas.chat import ChatMessageDTO

        return ChatMessageDTO(
            id="msg-steer-user-1",
            conversation_id=kwargs["conversation_id"],
            role="user",
            content=kwargs["content"],
            created_at=datetime.now(UTC),
        )

    monkeypatch.setattr(chat_runner.OpenAICompatChatClient, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(chat_runner, "_save_assistant_reply", fake_save)
    monkeypatch.setattr(chat_runner, "_save_user_steering_message", fake_save_user)

    spec = ChatRunSpec(
        conversation_id="conv-steer-1",
        title="Steer Test",
        agent_name="TestAgent",
        model_db_id="m1",
        runtime_cfg=LLMModelConfig(
            name="Mock",
            base_url="http://localhost:9999/v1",
            api_key="sk-mock",
            model_id="mock-model",
        ),
        llm_messages=[LLMMessage(role="user", content="scan target")],
        tool_registry=ToolRegistry(),
        use_agent_tools=False,
        sandbox_mode="simulated",
        sandbox_image="agan-ctf-sandbox:latest",
        max_tool_rounds=4,
        user_message=None,
    )

    run, init_q = mgr.start_run(spec)
    await first_chunk_sent.wait()

    rec = mgr.steer_run("conv-steer-1", "Skip port 22, check 8080 /admin")
    assert rec["content"] == "Skip port 22, check 8080 /admin"
    steer_sent.set()

    events = []
    async for ev in mgr.stream_from_queue(run, init_q):
        events.append(ev)

    assert len(observed_prompts) == 2
    assert any("Skip port 22, check 8080 /admin" in line for line in observed_prompts[1])
    assert any(e["type"] == "segment_done" for e in events)
    steer_committed = next(e for e in events if e["type"] == "steering_committed")
    assert steer_committed["user_messages"][0]["role"] == "user"
    assert steer_committed["user_messages"][0]["content"] == "Skip port 22, check 8080 /admin"
    done_ev = next(e for e in events if e["type"] == "done")
    assert done_ev["assistant_message"]["content"] == "Pivoting to port 8080 /admin!"


@pytest.mark.asyncio
async def test_auto_title_cleaning_and_sidecar_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from app.engine import chat_runner
    from app.engine.chat_runner import ChatRunManager, ChatRunSpec, _clean_generated_title
    from app.engine.llm.chat_client import OpenAICompatChatClient
    from app.engine.llm.types import LLMMessage, LLMModelConfig, LLMResponse
    from app.engine.tools.registry import ToolRegistry
    from app.models.chat import ChatConversation

    # 1. Verify title sanitizer strips <think>, prefixes, quotes, punctuation
    assert _clean_generated_title("<think>hmm</think>\n标题：「自动会话命名原理」。") == "自动会话命名原理"
    assert _clean_generated_title('Title: "Async Sidecar Naming!"') == "Async Sidecar Naming"

    # 2. Verify sidecar title generation updates DB and broadcasts `title_updated`
    db_file = tmp_path / "auto_title.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    import app.models.agent  # noqa: F401
    import app.models.capability  # noqa: F401
    import app.models.chat  # noqa: F401
    import app.models.llm  # noqa: F401

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(chat_runner, "async_session_factory", session_maker)

    async with session_maker() as db:
        conv = ChatConversation(title="请问 Claude 是怎么实现自动...")
        db.add(conv)
        await db.commit()
        conv_id = conv.id

    async def fake_stream_chat(self, messages, **kwargs):
        yield {"type": "content_delta", "delta": "通过异步旁路调用实现。"}
        yield {"type": "done", "model": "mock-model", "usage": {}, "tool_calls": []}

    async def fake_chat(self, messages, **kwargs):
        return LLMResponse(content="「Claude 会话自动命名原理」。")

    monkeypatch.setattr(OpenAICompatChatClient, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(OpenAICompatChatClient, "chat", fake_chat)

    mgr = ChatRunManager()
    spec = ChatRunSpec(
        conversation_id=conv_id,
        title="请问 Claude 是怎么实现自动...",
        agent_name="TestAgent",
        model_db_id="m1",
        runtime_cfg=LLMModelConfig(
            name="Mock",
            base_url="http://localhost:9999/v1",
            api_key="sk-mock",
            model_id="mock-model",
        ),
        llm_messages=[LLMMessage(role="user", content="请问 Claude 是怎么实现自动命名会话的？")],
        tool_registry=ToolRegistry(),
        use_agent_tools=False,
        sandbox_mode="simulated",
        sandbox_image="agan-ctf-sandbox:latest",
        max_tool_rounds=4,
        user_message=None,
        auto_title_prompt="请问 Claude 是怎么实现自动命名会话的？",
    )

    run, init_q = mgr.start_run(spec)
    events = []
    async for ev in mgr.stream_from_queue(run, init_q):
        events.append(ev)

    title_evts = [e for e in events if e.get("type") == "title_updated"]
    assert len(title_evts) == 1
    assert title_evts[0]["title"] == "Claude 会话自动命名原理"

    async with session_maker() as db:
        updated = await db.get(ChatConversation, conv_id)
        assert updated is not None
        assert updated.title == "Claude 会话自动命名原理"

    await test_engine.dispose()
