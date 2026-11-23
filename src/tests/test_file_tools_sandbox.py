from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.sandbox.path_guard import resolve_jailed_path
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.tools.base import ToolExecutionContext
from app.engine.tools.builtin.sandbox_tools import ReadFileTool, WriteFileTool
from app.engine.tools.db_loader import seed_builtin_tools
from app.main import create_app


def test_resolve_jailed_path_blocks_traversal_and_escapes(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("TOP_SECRET", encoding="utf-8")

    # Valid relative & /workspace paths
    hp1, vp1 = resolve_jailed_path("notes.txt", root)
    assert hp1 == (root / "notes.txt").resolve()
    assert vp1 == "/workspace/notes.txt"

    hp2, vp2 = resolve_jailed_path("/workspace/sub/exploit.py", root)
    assert hp2 == (root / "sub" / "exploit.py").resolve()
    assert vp2 == "/workspace/sub/exploit.py"

    # Valid host absolute path inside root
    hp3, vp3 = resolve_jailed_path(str(root / "inside.md"), root)
    assert hp3 == (root / "inside.md").resolve()
    assert vp3 == "/workspace/inside.md"

    # Blocked traversal & external paths
    for bad in (
        "../secret.txt",
        "/workspace/../../secret.txt",
        "sub/../../secret.txt",
        str(outside),
        "/etc/passwd",
        "bad\x00name.txt",
    ):
        with pytest.raises(PermissionError):
            resolve_jailed_path(bad, root)


@pytest.mark.asyncio
async def test_simulated_sandbox_disk_persistence_across_turns(tmp_path: Path) -> None:
    ws_dir = tmp_path / "chat-conv-1"

    # Turn 1: write a file
    sbx1 = SimulatedSandbox("chat-1", workspace_host_dir=ws_dir, seed_demo_if_empty=False)
    ctx1 = ToolExecutionContext(
        session_id="chat-1",
        sandbox=sbx1,
        scratchpad=WorkingScratchpad(),
    )
    writer = WriteFileTool()
    reader = ReadFileTool()

    res_w = await writer.execute(
        {"path": "notes/findings.txt", "content": "line1\nline2\n"}, ctx1
    )
    assert res_w.success is True
    assert (ws_dir / "notes" / "findings.txt").is_file()

    # Turn 2: fresh SimulatedSandbox pointed at same workspace_host_dir must still see file!
    sbx2 = SimulatedSandbox("chat-1", workspace_host_dir=ws_dir, seed_demo_if_empty=False)
    ctx2 = ToolExecutionContext(
        session_id="chat-1",
        sandbox=sbx2,
        scratchpad=WorkingScratchpad(),
    )
    res_append = await writer.execute(
        {"path": "/workspace/notes/findings.txt", "content": "line3\n", "mode": "append"},
        ctx2,
    )
    assert res_append.success is True

    res_r = await reader.execute(
        {
            "path": "notes/findings.txt",
            "start_line": 2,
            "end_line": 3,
            "show_line_numbers": True,
        },
        ctx2,
    )
    assert res_r.success is True
    assert res_r.output == "2: line2\n3: line3"
    assert res_r.metadata["total_lines"] == 3

    # Shell commands in SimulatedSandbox also see persisted disk files
    ls_res = await sbx2.exec_command("ls /workspace/notes")
    assert ls_res.exit_code == 0
    assert "findings.txt" in ls_res.output


@pytest.mark.asyncio
async def test_file_tools_security_policies_extensions_size_overwrite_binary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "jailed_root"
    root.mkdir()
    sbx = SimulatedSandbox("policy-test", workspace_host_dir=tmp_path / "other", seed_demo_if_empty=False)
    ctx = ToolExecutionContext(
        session_id="policy-test",
        sandbox=sbx,
        scratchpad=WorkingScratchpad(),
    )

    writer = WriteFileTool(
        config={
            "root_dir": str(root),
            "max_file_size_kb": 1,
            "allow_overwrite": False,
            "allowed_extensions": [".txt", ".md"],
        }
    )
    reader = ReadFileTool(
        config={
            "root_dir": str(root),
            "max_file_size_kb": 1,
            "allowed_extensions": [".txt", ".md"],
        }
    )

    # Disallowed extension
    bad_ext = await writer.execute({"path": "hack.py", "content": "print(1)"}, ctx)
    assert bad_ext.success is False
    assert "not allowed" in (bad_ext.error or "")

    # Path escape blocked
    escape = await writer.execute({"path": "../evil.txt", "content": "nope"}, ctx)
    assert escape.success is False
    assert "Access denied" in (escape.error or "")

    # Exceeds size limit (1 KB)
    too_big = await writer.execute({"path": "big.txt", "content": "A" * 2048}, ctx)
    assert too_big.success is False
    assert "exceeds" in (too_big.error or "")

    # Valid initial write
    ok = await writer.execute({"path": "memo.txt", "content": "hello\n"}, ctx)
    assert ok.success is True
    assert (root / "memo.txt").read_text(encoding="utf-8") == "hello\n"

    # Overwrite blocked when allow_overwrite=False
    no_ow = await writer.execute({"path": "memo.txt", "content": "replace"}, ctx)
    assert no_ow.success is False
    assert "allow_overwrite=false" in (no_ow.error or "")

    # Append still allowed
    app_ok = await writer.execute(
        {"path": "memo.txt", "content": "world\n", "mode": "append"}, ctx
    )
    assert app_ok.success is True

    # Read back from configured root_dir
    rd = await reader.execute({"path": "memo.txt"}, ctx)
    assert rd.success is True
    assert rd.output == "hello\nworld"

    # Binary file rejected safely
    (root / "bin.txt").write_bytes(b"\x7fELF\x00\x01\x02\x03")
    bin_rd = await reader.execute({"path": "bin.txt"}, ctx)
    assert bin_rd.success is False
    assert "binary" in (bin_rd.error or "").lower()


@pytest.mark.asyncio
async def test_builtin_file_tools_config_update_and_dry_run_api(tmp_path: Path) -> None:
    db_file = tmp_path / "test_file_api.db"
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

    custom_root = tmp_path / "custom_allowed_dir"
    custom_root.mkdir()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        tools = (await client.get("/api/v1/tools?page=1&page_size=20")).json()["items"]
        read_tool = next(t for t in tools if t["name"] == "read_file")
        write_tool = next(t for t in tools if t["name"] == "write_file")

        # Update built-in file tools with custom root_dir via PUT API
        upd_w = await client.put(
            f"/api/v1/tools/{write_tool['id']}",
            json={
                "implementation_config": {
                    "root_dir": str(custom_root),
                    "max_file_size_kb": 256,
                    "allow_overwrite": True,
                    "allowed_extensions": [".txt", "md"],
                }
            },
        )
        assert upd_w.status_code == 200
        assert upd_w.json()["implementation_config"]["root_dir"] == str(custom_root)
        assert upd_w.json()["implementation_config"]["allowed_extensions"] == [".txt", ".md"]

        upd_r = await client.put(
            f"/api/v1/tools/{read_tool['id']}",
            json={
                "implementation_config": {
                    "root_dir": str(custom_root),
                    "max_file_size_kb": 256,
                    "allowed_extensions": [".txt", ".md"],
                }
            },
        )
        assert upd_r.status_code == 200

        # Write via /test endpoint and verify persistence in custom_root
        t_write = await client.post(
            f"/api/v1/tools/{write_tool['id']}/test",
            json={"arguments": {"path": "demo.md", "content": "# AGAN File Tool OK\n"}},
        )
        assert t_write.status_code == 200
        assert t_write.json()["success"] is True
        assert (custom_root / "demo.md").is_file()

        # Read back via /test endpoint
        t_read = await client.post(
            f"/api/v1/tools/{read_tool['id']}/test",
            json={"arguments": {"path": "demo.md"}},
        )
        assert t_read.status_code == 200
        assert t_read.json()["success"] is True
        assert "# AGAN File Tool OK" in t_read.json()["output"]

        # Escape attempt via /test endpoint must fail cleanly
        t_escape = await client.post(
            f"/api/v1/tools/{read_tool['id']}/test",
            json={"arguments": {"path": "../outside.txt"}},
        )
        assert t_escape.status_code == 200
        assert t_escape.json()["success"] is False
        assert "Access denied" in (t_escape.json()["error"] or "")

    await test_engine.dispose()
