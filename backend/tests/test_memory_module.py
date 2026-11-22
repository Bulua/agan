from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base, get_db
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.memory.store import (
    INDEX_FILENAME,
    build_shared_blackboard_pin,
    delete_memory_file,
    edit_memory_file,
    read_memory_file,
    write_memory_file,
)
from app.engine.memory.tools import attach_memory_to_agent_run
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.tools.base import ToolExecutionContext
from app.engine.tools.registry import ToolRegistry
from app.main import create_app
from app.models.chat import ChatConversation


def test_memory_store_frontmatter_auto_index_edit_delete_and_jail(tmp_path: Path) -> None:
    scope_dir = tmp_path / "global"
    written = write_memory_file(
        scope_dir,
        "user-prefs",  # auto-appends .md
        "Always reply concisely in Chinese.\nPrefer 2px border radius.\n",
        title="User Preferences",
        memory_type="preference",
        tags=["style", "ui"],
        author="Main Agent",
        scope_label="Global Memory",
    )
    assert written["path"] == "user-prefs.md"
    assert written["title"] == "User Preferences"
    assert written["author"] == "Main Agent"
    assert "style" in written["tags"]

    # MEMORY.md automatically generated and references the note
    idx_file = scope_dir / INDEX_FILENAME
    assert idx_file.is_file()
    idx_text = idx_file.read_text(encoding="utf-8")
    assert "[User Preferences](user-prefs.md)" in idx_text
    assert "`preference`" in idx_text

    # Targeted edit updates content & refreshes index
    edited = edit_memory_file(
        scope_dir,
        "user-prefs.md",
        "Prefer 2px border radius.",
        "Prefer 2px-4px border radius.",
        scope_label="Global Memory",
    )
    assert edited["replacements"] == 1
    assert "2px-4px" in read_memory_file(scope_dir, "user-prefs.md")["content"]

    # Path traversal blocked
    with pytest.raises(PermissionError):
        read_memory_file(scope_dir, "../../secret.md")
    with pytest.raises(PermissionError):
        write_memory_file(scope_dir, "../escape.md", "bad")

    # Delete removes note and cleans MEMORY.md
    removed = delete_memory_file(scope_dir, "user-prefs.md", scope_label="Global Memory")
    assert removed == "user-prefs.md"
    assert "user-prefs.md" not in idx_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_multi_agent_shared_blackboard_and_readonly_policy(tmp_path: Path) -> None:
    mem_root = tmp_path / "memories"
    shared_dir = tmp_path / "workspaces" / "chat-run1" / ".agan" / "shared"

    sbx = SimulatedSandbox("mem-test", seed_demo_if_empty=False)
    tool_ctx = ToolExecutionContext(
        session_id="run1",
        sandbox=sbx,
        scratchpad=WorkingScratchpad(),
    )

    # Agent A: Main Coordinator (read_write)
    reg_a = ToolRegistry()
    attach_memory_to_agent_run(
        reg_a,
        memories_root=mem_root,
        agent_id="agent-main",
        agent_name="Coordinator",
        shared_dir=shared_dir,
        memory_config={"enable_long_term_memory": True, "memory_mode": "read_write"},
    )

    # Agent B: Specialist Subagent (read_only for long-term, still writable for shared blackboard!)
    reg_b = ToolRegistry()
    attach_memory_to_agent_run(
        reg_b,
        memories_root=mem_root,
        agent_id="agent-sub",
        agent_name="Code Reviewer",
        shared_dir=shared_dir,
        memory_config={"enable_long_term_memory": True, "memory_mode": "read_only"},
    )

    # Subagent B blocked from writing global/agent long-term memory
    deny_global = await reg_b.execute(
        "memory_write",
        {"path": "hack.md", "content": "nope", "scope": "global"},
        tool_ctx,
    )
    assert deny_global.success is False
    assert "read_only" in (deny_global.error or "")

    # Subagent B CAN publish verified finding to shared blackboard!
    ok_shared = await reg_b.execute(
        "memory_write",
        {
            "path": "auth-findings.md",
            "scope": "shared",
            "title": "Auth Token Verification",
            "memory_type": "finding",
            "tags": ["auth", "jwt"],
            "content": "Found hardcoded JWT secret in `/workspace/app/config.py:42`.",
        },
        tool_ctx,
    )
    assert ok_shared.success is True

    # Coordinator A immediately discovers and reads Subagent B's finding on shared blackboard
    search_a = await reg_a.execute(
        "memory_search",
        {"query": "JWT secret", "scope": "shared"},
        tool_ctx,
    )
    assert search_a.success is True
    assert "auth-findings.md" in search_a.output
    assert "Code Reviewer" in search_a.output

    read_a = await reg_a.execute(
        "memory_read",
        {"path": "auth-findings.md", "scope": "shared"},
        tool_ctx,
    )
    assert read_a.success is True
    assert "/workspace/app/config.py:42" in read_a.output

    # Shared blackboard pin block is ready for ContextCompactor pinning
    pin = build_shared_blackboard_pin(shared_dir)
    assert "Shared Task Blackboard" in pin
    assert "auth-findings.md" in pin
    assert "Code Reviewer" in pin

    # Coordinator A can also maintain its own durable agent memory + prompt injection reflects both
    ok_agent = await reg_a.execute(
        "memory_write",
        {
            "path": "heuristics.md",
            "scope": "agent",
            "title": "Review Heuristics",
            "memory_type": "lesson",
            "content": "Always inspect config modules before deep-diving handlers.",
        },
        tool_ctx,
    )
    assert ok_agent.success is True

    reg_a_next = ToolRegistry()
    prompt_block = attach_memory_to_agent_run(
        reg_a_next,
        memories_root=mem_root,
        agent_id="agent-main",
        agent_name="Coordinator",
        shared_dir=shared_dir,
    )
    assert "auth-findings.md" in prompt_block
    assert "heuristics.md" in prompt_block


@pytest.mark.asyncio
async def test_memory_rest_api_scopes_crud_and_search(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "memories_dir", tmp_path / "memories")
    monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspaces")

    db_file = tmp_path / "memory_api.db"
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
        # Seed a model + agent + conversation
        m_res = await client.post(
            "/api/v1/models",
            json={
                "name": "Mem LLM",
                "model_type": "chat",
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-mem",
                "model_id": "gpt-4o",
            },
        )
        ag_res = await client.post(
            "/api/v1/agents",
            json={"name": "Memory Tester", "chat_model_id": m_res.json()["id"]},
        )
        assert ag_res.status_code == 201
        agent_id = ag_res.json()["id"]

        async with session_maker() as db:
            conv = ChatConversation(title="Team Investigation", agent_id=agent_id, agent_name_snapshot="Memory Tester")
            db.add(conv)
            await db.commit()
            conv_id = conv.id

        # Create notes across global, agent, and shared scopes
        r1 = await client.put(
            "/api/v1/memories/file",
            json={
                "scope": "global",
                "path": "prefs.md",
                "title": "Global Prefs",
                "memory_type": "preference",
                "tags": ["lang"],
                "content": "Respond in Chinese.",
                "raw_mode": False,
            },
        )
        assert r1.status_code == 200
        assert r1.json()["path"] == "prefs.md"

        r2 = await client.put(
            "/api/v1/memories/file",
            json={
                "scope": "agent",
                "scope_id": agent_id,
                "path": "lessons.md",
                "title": "Agent Lessons",
                "content": "Check environment variables first.",
                "raw_mode": False,
            },
        )
        assert r2.status_code == 200

        r3 = await client.put(
            "/api/v1/memories/file",
            json={
                "scope": "shared",
                "scope_id": conv_id,
                "path": "handoff.md",
                "title": "Team Handoff",
                "content": "API gateway runs on port 9090.",
                "raw_mode": False,
            },
        )
        assert r3.status_code == 200

        # List scopes -> sees all 3 tiers + auto-generated MEMORY.md
        scopes = (await client.get("/api/v1/memories/scopes")).json()
        assert scopes["global_scope"]["file_count"] == 1
        assert any(f["path"] == "MEMORY.md" for f in scopes["global_scope"]["files"])
        assert any(a["scope_id"] == agent_id and a["file_count"] == 1 for a in scopes["agent_scopes"])
        assert any(s["scope_id"] == conv_id and s["file_count"] == 1 for s in scopes["shared_scopes"])

        # Cross-scope search
        search_res = (await client.get("/api/v1/memories/search", params={"q": "9090"})).json()
        assert search_res["total"] == 1
        assert search_res["items"][0]["scope"] == "shared"
        assert search_res["items"][0]["path"] == "handoff.md"

        # Read & delete file
        read_res = await client.get(
            "/api/v1/memories/file",
            params={"scope": "global", "path": "prefs.md"},
        )
        assert read_res.status_code == 200
        assert "Respond in Chinese." in read_res.json()["content"]

        del_res = await client.delete(
            "/api/v1/memories/file",
            params={"scope": "global", "path": "prefs.md"},
        )
        assert del_res.status_code == 204

    await test_engine.dispose()
