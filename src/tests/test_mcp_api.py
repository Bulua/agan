from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.engine.mcp.manager import attach_mcp_tools_to_registry, make_mcp_tool_name
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.tools.base import ToolExecutionContext
from app.engine.tools.registry import ToolRegistry
from app.main import create_app
from app.models.mcp import MCPServer

MOCK_STDIO_MCP_SCRIPT = r'''
import json, sys

for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    msg = json.loads(line)
    req_id = msg.get("id")
    method = msg.get("method")
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-ctf-mcp", "version": "1.0.0"},
            },
        }
    elif method == "tools/list":
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "decode_hex",
                        "description": "Decode a hex-encoded string into UTF-8.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"hex_str": {"type": "string"}},
                            "required": ["hex_str"],
                        },
                    },
                    {
                        "name": "reverse_str",
                        "description": "Reverse a string.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    },
                ]
            },
        }
    elif method == "tools/call":
        params = msg.get("params") or {}
        tname = params.get("name")
        targs = params.get("arguments") or {}
        if tname == "decode_hex":
            decoded = bytes.fromhex(str(targs.get("hex_str", ""))).decode("utf-8", errors="replace")
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": decoded}],
                    "isError": False,
                },
            }
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tname}"},
            }
    else:
        resp = {"jsonrpc": "2.0", "id": req_id, "result": {}}
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()
'''


@pytest.fixture
async def api_client(tmp_path: Path):
    db_file = tmp_path / "test_mcp.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True)
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    import app.models.agent  # noqa: F401
    import app.models.capability  # noqa: F401
    import app.models.chat  # noqa: F401
    import app.models.llm  # noqa: F401
    import app.models.mcp  # noqa: F401

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
        yield client, session_maker

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_mcp_stdio_discovery_tool_call_import_and_agent_binding(
    api_client, tmp_path: Path
) -> None:
    client, session_maker = api_client

    # Write real local mock stdio MCP server script
    script_path = tmp_path / "mock_mcp_server.py"
    script_path.write_text(MOCK_STDIO_MCP_SCRIPT, encoding="utf-8")

    # 1. Create Stdio MCP Server with encrypted env
    create_resp = await client.post(
        "/api/v1/mcp",
        json={
            "name": "ctf-utils",
            "description": "Local mock CTF MCP server",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(script_path)],
            "env": {"API_SECRET": "super-secret-token-1234"},
            "timeout_sec": 15,
        },
    )
    assert create_resp.status_code == 201
    srv = create_resp.json()
    assert srv["name"] == "ctf-utils"
    assert srv["has_env"] is True
    assert "super-secret" not in str(srv["env_masked"])

    # 2. Live refresh -> initialize + tools/list
    ref_resp = await client.post(f"/api/v1/mcp/{srv['id']}/refresh")
    assert ref_resp.status_code == 200
    refreshed = ref_resp.json()
    assert refreshed["last_status"] == "connected"
    assert refreshed["tool_count"] == 2
    assert refreshed["discovered_tools"][0]["name"] == "decode_hex"

    # 3. Live tool dry-run -> tools/call
    hex_flag = b"flag{mcp_stdio_works}".hex()
    call_resp = await client.post(
        f"/api/v1/mcp/{srv['id']}/tools/decode_hex/test",
        json={"arguments": {"hex_str": hex_flag}},
    )
    assert call_resp.status_code == 200
    call_data = call_resp.json()
    assert call_data["success"] is True
    assert call_data["output"] == "flag{mcp_stdio_works}"

    # 4. Batch import standard `mcpServers` JSON
    imp_resp = await client.post(
        "/api/v1/mcp/import",
        json={
            "mcpServers": {
                "remote-docs": {
                    "url": "https://example.invalid/mcp",
                    "headers": {"Authorization": "Bearer tok_abc123456"},
                },
                "legacy-sse": {
                    "url": "https://example.invalid/sse",
                },
            }
        },
    )
    assert imp_resp.status_code == 200
    imp = imp_resp.json()
    assert imp["created"] == 2
    by_name = {item["name"]: item for item in imp["items"]}
    assert by_name["remote-docs"]["transport"] == "streamable_http"
    assert by_name["legacy-sse"]["transport"] == "sse"

    # 5. Bind MCP Server to an Agent and verify clone preserves mcp_server_ids
    m_res = await client.post(
        "/api/v1/models",
        json={
            "name": "MCP Agent LLM",
            "model_type": "chat",
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-mcp",
            "model_id": "gpt-4o",
        },
    )
    mid = m_res.json()["id"]

    a_res = await client.post(
        "/api/v1/agents",
        json={
            "name": "MCP Powered Agent",
            "chat_model_id": mid,
            "loop_engine": "react",
            "mcp_server_ids": [srv["id"]],
            "mcp_tool_filters": {srv["id"]: ["decode_hex"]},
        },
    )
    assert a_res.status_code == 201
    agent_data = a_res.json()
    assert agent_data["mcp_server_ids"] == [srv["id"]]
    assert agent_data["mcp_servers"][0]["tool_count"] == 2
    assert agent_data["mcp_tool_filters"] == {srv["id"]: ["decode_hex"]}

    clone_res = await client.post(f"/api/v1/agents/{agent_data['id']}/clone")
    assert clone_res.status_code == 201
    assert clone_res.json()["mcp_server_ids"] == [srv["id"]]
    assert clone_res.json()["mcp_tool_filters"] == {srv["id"]: ["decode_hex"]}

    # 6. Verify attach_mcp_tools_to_registry respects per-tool allowlist AND unfiltered full mode
    async with session_maker() as db:
        srv_row = await db.get(MCPServer, srv["id"])
        assert srv_row is not None

        # Filtered: only decode_hex attached, reverse_str excluded
        reg_filtered = ToolRegistry()
        attached = await attach_mcp_tools_to_registry(
            reg_filtered, [srv_row], tool_filters={srv["id"]: ["decode_hex"]}
        )
        assert attached == 1
        expected_decode = make_mcp_tool_name("ctf-utils", "decode_hex")
        expected_rev = make_mcp_tool_name("ctf-utils", "reverse_str")
        assert reg_filtered.get(expected_decode) is not None
        assert reg_filtered.get(expected_rev) is None

        # Unfiltered: all 2 tools attached
        reg_all = ToolRegistry()
        assert await attach_mcp_tools_to_registry(reg_all, [srv_row], tool_filters={}) == 2
        assert reg_all.get(expected_rev) is not None

        ctx = ToolExecutionContext(
            session_id="mcp-reg-test",
            sandbox=SimulatedSandbox("mcp-reg-test"),
            scratchpad=WorkingScratchpad(),
        )
        res = await reg_filtered.execute(expected_decode, {"hex_str": hex_flag}, ctx)
        assert res.success is True
        assert res.output == "flag{mcp_stdio_works}"
        assert res.metadata["mcp_server"] == "ctf-utils"
        assert res.metadata["mcp_tool"] == "decode_hex"
