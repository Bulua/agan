from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.core.events import SessionEventBus, TraceEventType
from app.engine.llm.chat_client import BaseChatClient
from app.engine.llm.types import LLMMessage, LLMModelConfig, LLMResponse, ToolCallRequest
from app.engine.loops.base import ChallengeBrief, LoopContext, LoopStatus
from app.engine.loops.factory import create_loop_engine
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.tools.registry import create_default_tool_registry
from app.main import create_app


@pytest.fixture
async def api_client(tmp_path):
    db_file = tmp_path / "test_sessions.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    import app.models.llm  # noqa: F401
    import app.models.session  # noqa: F401

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
async def test_simulated_sandbox_solves_default_demo_puzzle() -> None:
    sbx = SimulatedSandbox("sim-test")
    r1 = await sbx.exec_command("ls /workspace/challenge")
    assert r1.exit_code == 0
    assert "config.bak" in r1.output

    r2 = await sbx.exec_command(
        "cd /workspace/challenge && grep encoded_secret config.bak"
    )
    assert r2.exit_code == 0
    assert r2.cwd == "/workspace/challenge"

    # Double base64 decode recovers exact flag
    r3 = await sbx.exec_command(
        "echo Wm14aFozdGhaMkZ1WDNOcGJYVnNZWFJsWkY5ellXNWtZbTk0WDI5cmZRPT0= | base64 -d | base64 -d"
    )
    assert r3.exit_code == 0
    assert r3.output.strip() == "flag{agan_simulated_sandbox_ok}"


class _ReflexionScriptedLLM(BaseChatClient):
    def __init__(self) -> None:
        self.config = LLMModelConfig(model_id="mock-reflexion")
        self._responses = [
            # Step 1: failing command #1
            LLMResponse(
                content="Try reading nonexistent file",
                tool_calls=[
                    ToolCallRequest(
                        id="c1", name="bash_exec", arguments={"command": "cat /workspace/nope.txt"}
                    )
                ],
            ),
            # Step 2: failing command #2 -> triggers Reflexion!
            LLMResponse(
                content="Try another nonexistent file",
                tool_calls=[
                    ToolCallRequest(
                        id="c2", name="bash_exec", arguments={"command": "cat /workspace/missing.txt"}
                    )
                ],
            ),
            # Reflexion critique response (no tools)
            LLMResponse(content="Root cause: wrong path. Pivot to /workspace/challenge/config.bak."),
            # Step 3: submit flag after reflection
            LLMResponse(
                content="Submitting flag after reflection.",
                tool_calls=[
                    ToolCallRequest(
                        id="c3",
                        name="submit_flag",
                        arguments={"flag": "flag{reflexion_works}"},
                    )
                ],
            ),
        ]

    async def chat(self, messages: list[LLMMessage], **_kwargs) -> LLMResponse:
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_reflexion_engine_triggers_critique_after_failure_streak() -> None:
    sbx = SimulatedSandbox("ref-test")
    bus = SessionEventBus()
    ctx = LoopContext(
        session_id="sess-ref-1",
        challenge=ChallengeBrief(title="ReflexionTest", category="misc"),
        sandbox=sbx,
        llm_client=_ReflexionScriptedLLM(),
        tool_registry=create_default_tool_registry(),
        event_bus=bus,
    )
    result = await create_loop_engine("reflexion").run(ctx)
    assert result.status == LoopStatus.SOLVED
    assert result.detected_flag == "flag{reflexion_works}"
    assert any(
        e.event_type == TraceEventType.REFLEXION for e in bus.get_history("sess-ref-1")
    )


@pytest.mark.asyncio
async def test_sessions_api_crud_pagination_scratchpad_and_attachments(
    api_client: AsyncClient,
) -> None:
    # 1. List available loop engines
    eng = (await api_client.get("/api/v1/sessions/engines")).json()
    assert {"react", "plan_execute", "reflexion"}.issubset(set(eng["engines"]))

    # 2. Create a Chat LLM model first
    model_resp = await api_client.post(
        "/api/v1/models",
        json={
            "name": "Test Chat Model",
            "model_type": "chat",
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-key",
            "model_id": "gpt-4o",
        },
    )
    assert model_resp.status_code == 201
    model_id = model_resp.json()["id"]

    # 3. Create solve session
    create_resp = await api_client.post(
        "/api/v1/sessions",
        json={
            "title": "Warmup Base64 Challenge",
            "category": "misc",
            "description": "Decode the double-base64 secret in config.bak.",
            "chat_model_id": model_id,
            "loop_engine": "plan_execute",
            "sandbox_mode": "simulated",
        },
    )
    assert create_resp.status_code == 201
    sess = create_resp.json()
    sid = sess["id"]
    assert sess["status"] == "pending"
    assert sess["loop_engine"] == "plan_execute"

    # 4. Upload attachment
    att_resp = await api_client.post(
        f"/api/v1/sessions/{sid}/attachments",
        files={"file": ("hint.txt", b"check config.bak", "text/plain")},
    )
    assert att_resp.status_code == 200
    assert "hint.txt" in att_resp.json()["attachments"]

    # 5. Update scratchpad
    sp_resp = await api_client.put(
        f"/api/v1/sessions/{sid}/scratchpad",
        json={
            "known_clues": ["config.bak contains encoded_secret"],
            "endpoints_ports": ["localhost:8080"],
            "vulnerabilities": [],
            "ruled_out": ["brute force"],
            "notes_md": "Double base64 suspected.",
        },
    )
    assert sp_resp.status_code == 200
    assert sp_resp.json()["notes_md"] == "Double base64 suspected."

    # 6. Paginated list
    list_resp = (await api_client.get("/api/v1/sessions?page=1&page_size=5")).json()
    assert list_resp["total"] == 1
    assert list_resp["status_counts"]["pending"] == 1

    # 7. Delete session
    del_resp = await api_client.delete(f"/api/v1/sessions/{sid}")
    assert del_resp.status_code == 204
    assert (await api_client.get(f"/api/v1/sessions/{sid}")).status_code == 404
