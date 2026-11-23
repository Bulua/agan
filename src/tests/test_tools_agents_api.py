from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.engine.tools.db_loader import seed_builtin_tools
from app.main import create_app


@pytest.fixture
async def api_client(tmp_path):
    db_file = tmp_path / "test_tools_agents.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    import app.models.agent  # noqa: F401
    import app.models.capability  # noqa: F401
    import app.models.chat  # noqa: F401
    import app.models.llm  # noqa: F401

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_maker() as seed_db:
        await seed_builtin_tools(seed_db)
        await seed_db.commit()

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
async def test_tools_builtin_seed_custom_crud_and_dry_run(api_client: AsyncClient) -> None:
    # 1. Built-in tools seeded automatically
    list_res = (await api_client.get("/api/v1/tools?page=1&page_size=20")).json()
    assert list_res["total"] >= 7
    assert list_res["type_counts"]["builtin"] >= 7
    bash_tool = next(t for t in list_res["items"] if t["name"] == "bash_exec")

    # 2. Built-in tool dry-run test via SimulatedSandbox
    test_res = await api_client.post(
        f"/api/v1/tools/{bash_tool['id']}/test",
        json={"arguments": {"command": "ls /workspace/challenge"}},
    )
    assert test_res.status_code == 200
    assert test_res.json()["success"] is True
    assert "config.bak" in test_res.json()["output"]

    # 3. Built-in tool cannot be deleted, but can be toggled
    assert (await api_client.delete(f"/api/v1/tools/{bash_tool['id']}")).status_code == 400
    tog = (await api_client.patch(f"/api/v1/tools/{bash_tool['id']}/toggle")).json()
    assert tog["is_enabled"] is False
    # Re-enable
    await api_client.patch(f"/api/v1/tools/{bash_tool['id']}/toggle")

    # 4. Create custom HTTP tool
    custom = await api_client.post(
        "/api/v1/tools",
        json={
            "name": "cve_lookup",
            "display_name": "CVE Lookup Webhook",
            "description": "Query internal CVE API",
            "tool_type": "custom_http",
            "category": "network",
            "parameters_schema": {
                "type": "object",
                "properties": {"cve_id": {"type": "string"}},
                "required": ["cve_id"],
            },
            "implementation_config": {
                "method": "GET",
                "url": "https://example.invalid/api/{{cve_id}}",
            },
        },
    )
    assert custom.status_code == 201
    cid = custom.json()["id"]

    # 5. Update & Delete custom tool
    upd = await api_client.put(f"/api/v1/tools/{cid}", json={"display_name": "CVE Query v2"})
    assert upd.status_code == 200
    assert upd.json()["display_name"] == "CVE Query v2"
    assert (await api_client.delete(f"/api/v1/tools/{cid}")).status_code == 204


@pytest.mark.asyncio
async def test_agents_crud_tool_binding_clone_and_chat_binding(api_client: AsyncClient) -> None:
    # 1. Create Chat LLM
    m_res = await api_client.post(
        "/api/v1/models",
        json={
            "name": "Agent Test LLM",
            "model_type": "chat",
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-agent",
            "model_id": "gpt-4o",
        },
    )
    model_id = m_res.json()["id"]

    tools_list = (await api_client.get("/api/v1/tools?page=1&page_size=20")).json()["items"]
    bash_id = next(t["id"] for t in tools_list if t["name"] == "bash_exec")
    read_id = next(t["id"] for t in tools_list if t["name"] == "read_file")

    # 2. Create Specialist Agent bound to LLM + 2 Tools
    a1_resp = await api_client.post(
        "/api/v1/agents",
        json={
            "name": "Web CTF Specialist",
            "role": "web",
            "description": "Expert in Flask SSTI, SQLi, SSRF.",
            "chat_model_id": model_id,
            "loop_engine": "react",
            "system_prompt": "You are a Web CTF expert.",
            "tool_ids": [bash_id, read_id],
        },
    )
    assert a1_resp.status_code == 201
    a1 = a1_resp.json()
    assert a1["is_default"] is True
    assert set(a1["tool_ids"]) == {bash_id, read_id}

    # 3. Clone Agent
    cloned_resp = await api_client.post(f"/api/v1/agents/{a1['id']}/clone")
    assert cloned_resp.status_code == 201
    a2 = cloned_resp.json()
    assert a2["name"] == "Web CTF Specialist (Copy)"
    assert set(a2["tool_ids"]) == {bash_id, read_id}
    assert a2["is_default"] is False

    # 4. Paginated Agent list
    page1 = (await api_client.get("/api/v1/agents?page=1&page_size=1")).json()
    assert page1["total"] == 2
    assert page1["role_counts"]["web"] == 2

    # 5. Create Chat Conversation bound to Agent (auto-picks default Agent!)
    conv_resp = await api_client.post("/api/v1/chat/conversations", json={"title": "Agent Chat"})
    assert conv_resp.status_code == 201
    conv = conv_resp.json()
    assert conv["agent_id"] == a1["id"]
    assert conv["agent_name_snapshot"] == "Web CTF Specialist"
    assert conv["model_id"] == model_id

    # Switch conversation to cloned agent
    sw = await api_client.patch(
        f"/api/v1/chat/conversations/{conv['id']}",
        json={"agent_id": a2["id"]},
    )
    assert sw.status_code == 200
    assert sw.json()["agent_id"] == a2["id"]
    assert sw.json()["agent_name_snapshot"] == "Web CTF Specialist (Copy)"

    # Delete cloned agent
    assert (await api_client.delete(f"/api/v1/agents/{a2['id']}")).status_code == 204
