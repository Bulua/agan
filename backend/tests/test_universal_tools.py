from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.tools.base import ToolExecutionContext
from app.engine.tools.builtin.sandbox_tools import (
    EditFileTool,
    GrepSearchTool,
    ListFilesTool,
    WriteFileTool,
)
from app.engine.tools.builtin.skill_tools import CreateSkillTool, EditSkillFileTool
from app.engine.tools.builtin.web_tools import (
    WebFetchTool,
    WebSearchTool,
    html_to_readable_text,
    parse_ddg_html,
)
from app.engine.tools.db_loader import seed_builtin_tools
from app.main import create_app


def _make_ctx(tmp_path: Path, *, disk_backed: bool = True) -> ToolExecutionContext:
    sbx = SimulatedSandbox(
        "u-test",
        workspace_host_dir=(tmp_path / "ws") if disk_backed else None,
        seed_demo_if_empty=False,
    )
    return ToolExecutionContext(
        session_id="u-test",
        sandbox=sbx,
        scratchpad=WorkingScratchpad(),
    )


@pytest.mark.asyncio
async def test_edit_file_unique_replace_all_errors_and_jail(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, disk_backed=True)
    writer = WriteFileTool()
    editor = EditFileTool()

    await writer.execute(
        {
            "path": "app.py",
            "content": "def greet():\n    x = 1\n    y = 1\n    return x + y\n",
        },
        ctx,
    )

    # 1. Ambiguous match blocked when replace_all=False
    ambig = await editor.execute(
        {"path": "app.py", "old_text": "= 1", "new_text": "= 2"},
        ctx,
    )
    assert ambig.success is False
    assert "matched 2 times" in (ambig.error or "")

    # 2. Unique replacement succeeds with accurate diff stats
    ok = await editor.execute(
        {"path": "app.py", "old_text": "    x = 1\n", "new_text": "    x = 10\n    z = 5\n"},
        ctx,
    )
    assert ok.success is True
    assert ok.metadata["replacements"] == 1
    assert ok.metadata["lines_added"] == 2
    assert ok.metadata["lines_removed"] == 1

    # 3. replace_all=True replaces every occurrence
    all_res = await editor.execute(
        {"path": "app.py", "old_text": "1", "new_text": "9", "replace_all": True},
        ctx,
    )
    assert all_res.success is True
    assert all_res.metadata["replacements"] == 2
    assert "x = 90" in (tmp_path / "ws" / "app.py").read_text(encoding="utf-8")

    # 4. Missing old_text fails gracefully
    miss = await editor.execute(
        {"path": "app.py", "old_text": "NONEXISTENT_BLOCK", "new_text": "abc"},
        ctx,
    )
    assert miss.success is False

    # 5. Path escape blocked
    esc = await editor.execute(
        {"path": "../outside.py", "old_text": "a", "new_text": "b"},
        ctx,
    )
    assert esc.success is False
    assert "Access denied" in (esc.error or "")


@pytest.mark.asyncio
async def test_list_files_and_grep_search_jailed_and_skip_noise(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, disk_backed=True)
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "main.py").write_text("import os\nSECRET_KEY = 'alpha'\n", encoding="utf-8")
    (ws / "src" / "utils.ts").write_text("export const SECRET_KEY = 'beta'\n", encoding="utf-8")
    (ws / "docs").mkdir()
    (ws / "docs" / "guide.md").write_text("# Guide\nUse secret_key carefully.\n", encoding="utf-8")
    (ws / "binary.bin").write_bytes(b"\x00\x01\x02SECRET_KEY\x00")
    (ws / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "node_modules" / "pkg" / "index.js").write_text("SECRET_KEY = 'ignored'\n", encoding="utf-8")

    lister = ListFilesTool()
    res_all = await lister.execute({"path": ".", "pattern": "*.py"}, ctx)
    assert res_all.success is True
    assert "src/main.py" in res_all.output
    assert "utils.ts" not in res_all.output
    assert "node_modules" not in res_all.output

    grep = GrepSearchTool()
    # Case-insensitive literal search skips binary & node_modules
    g1 = await grep.execute({"pattern": "secret_key"}, ctx)
    assert g1.success is True
    assert g1.metadata["match_count"] == 3
    assert "src/main.py:2:" in g1.output
    assert "docs/guide.md:2:" in g1.output
    assert "binary.bin" not in g1.output
    assert "node_modules" not in g1.output

    # Regex + glob filter + case-sensitive
    g2 = await grep.execute(
        {
            "pattern": r"SECRET_KEY\s*=\s*'alpha'",
            "glob": "*.py",
            "is_regex": True,
            "case_sensitive": True,
        },
        ctx,
    )
    assert g2.success is True
    assert g2.metadata["match_count"] == 1
    assert "src/main.py:2:" in g2.output

    # Escape blocked
    bad_list = await lister.execute({"path": "../.."}, ctx)
    assert bad_list.success is False
    bad_grep = await grep.execute({"pattern": "x", "path": "../.."}, ctx)
    assert bad_grep.success is False


def test_ddg_and_html_parsers() -> None:
    sample_ddg = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffastapi.tiangolo.com%2F&amp;rut=123">
        FastAPI Documentation
      </a>
      <a class="result__snippet">Modern, fast web framework for Python APIs.</a>
    </div>
    """
    parsed = parse_ddg_html(sample_ddg, max_results=5)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "FastAPI Documentation"
    assert parsed[0]["url"] == "https://fastapi.tiangolo.com/"
    assert "web framework" in parsed[0]["snippet"]

    sample_html = """
    <html>
      <head><title>Test Page</title><style>body { color: red; }</style></head>
      <body>
        <script>console.log('ignored')</script>
        <h1>Hello World</h1>
        <p>Use <code>edit_file</code> to patch files cleanly.</p>
      </body>
    </html>
    """
    title, text = html_to_readable_text(sample_html)
    assert title == "Test Page"
    assert "# Hello World" in text
    assert "`edit_file`" in text
    assert "ignored" not in text
    assert "color: red" not in text


@pytest.mark.asyncio
async def test_web_search_and_web_fetch_mocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, disk_backed=False)

    def _mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.tavily.com" in url:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Tavily Result",
                            "url": "https://example.org/doc",
                            "content": "Concise Tavily snippet.",
                        }
                    ]
                },
            )
        if "example.org/doc" in url:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><head><title>Doc Title</title></head><body><h1>Docs</h1><p>Useful body.</p></body></html>",
            )
        if "example.org/data.json" in url:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"ok": True, "items": [1, 2]},
            )
        if "example.org/img.png" in url:
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"\x89PNG\r\n\x1a\n",
            )
        return httpx.Response(404, text="Not found")

    transport = httpx.MockTransport(_mock_handler)
    real_async_client = httpx.AsyncClient

    def _patched_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched_client)

    search_tool = WebSearchTool(config={"provider": "tavily", "api_key": "tvly-secret-123"})
    s_res = await search_tool.execute({"query": "async fastapi", "max_results": 3}, ctx)
    assert s_res.success is True
    assert "Tavily Result" in s_res.output
    assert s_res.metadata["provider"] == "tavily"

    fetch_tool = WebFetchTool()
    f_html = await fetch_tool.execute({"url": "https://example.org/doc"}, ctx)
    assert f_html.success is True
    assert "Title: Doc Title" in f_html.output
    assert "Useful body." in f_html.output

    f_json = await fetch_tool.execute({"url": "https://example.org/data.json"}, ctx)
    assert f_json.success is True
    assert '"ok": true' in f_json.output

    f_bin = await fetch_tool.execute({"url": "https://example.org/img.png"}, ctx)
    assert f_bin.success is False
    assert "binary" in (f_bin.error or "").lower()

    f_bad_scheme = await fetch_tool.execute({"url": "file:///etc/passwd"}, ctx)
    assert f_bad_scheme.success is False


@pytest.mark.asyncio
async def test_create_and_edit_skill_tools(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    ctx = _make_ctx(tmp_path, disk_backed=False)

    creator = CreateSkillTool(skills_root=skills_root)
    editor = EditSkillFileTool(skills_root=skills_root)

    created = await creator.execute(
        {
            "name": "log-triage",
            "title": "Log Triage Guide",
            "description": "Systematic workflow for inspecting application logs.",
            "skill_md_content": "# Log Triage\n\nStep 1: Locate error spikes.\n",
            "files": [
                {"path": "scripts/summarize.py", "content": "print('summary v1')\n"},
            ],
        },
        ctx,
    )
    assert created.success is True
    assert (skills_root / "log-triage" / "SKILL.md").is_file()
    assert (skills_root / "log-triage" / "scripts" / "summarize.py").is_file()

    # Duplicate creation rejected
    dup = await creator.execute(
        {"name": "log-triage", "skill_md_content": "# Duplicate"},
        ctx,
    )
    assert dup.success is False

    # List skills & files
    listed_all = await editor.execute({"action": "list"}, ctx)
    assert listed_all.success is True
    assert "log-triage" in listed_all.output

    listed_one = await editor.execute({"action": "list", "skill_name": "log-triage"}, ctx)
    assert listed_one.success is True
    assert "scripts/summarize.py" in listed_one.output

    # Targeted edit on bundled script
    edited = await editor.execute(
        {
            "action": "edit",
            "skill_name": "log-triage",
            "file_path": "scripts/summarize.py",
            "old_text": "summary v1",
            "new_text": "summary v2",
        },
        ctx,
    )
    assert edited.success is True
    read_back = await editor.execute(
        {"action": "read", "skill_name": "log-triage", "file_path": "scripts/summarize.py"},
        ctx,
    )
    assert read_back.success is True
    assert "summary v2" in read_back.output

    # Delete bundled file & block traversal
    deleted = await editor.execute(
        {"action": "delete", "skill_name": "log-triage", "file_path": "scripts/summarize.py"},
        ctx,
    )
    assert deleted.success is True
    assert not (skills_root / "log-triage" / "scripts" / "summarize.py").exists()

    escape = await editor.execute(
        {"action": "read", "skill_name": "log-triage", "file_path": "../../secret.txt"},
        ctx,
    )
    assert escape.success is False


@pytest.mark.asyncio
async def test_phase1_builtin_seed_and_web_search_secret_masking(tmp_path: Path) -> None:
    db_file = tmp_path / "phase1_api.db"
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
        res = (await client.get("/api/v1/tools?page=1&page_size=50")).json()
        by_name = {item["name"]: item for item in res["items"]}

        for expected in (
            "read_file",
            "write_file",
            "edit_file",
            "list_files",
            "grep_search",
            "web_search",
            "web_fetch",
            "create_skill",
            "edit_skill_file",
        ):
            assert expected in by_name
            assert by_name[expected]["is_enabled"] is True

        # CTF-only submit_flag starts disabled by default on fresh DB
        assert by_name["submit_flag"]["is_enabled"] is False

        # Save Tavily API key into web_search -> encrypted & masked in API response
        ws_id = by_name["web_search"]["id"]
        upd = await client.put(
            f"/api/v1/tools/{ws_id}",
            json={
                "implementation_config": {
                    "provider": "tavily",
                    "api_key": "tvly-supersecretkey9999",
                    "default_max_results": 6,
                    "timeout": 20,
                }
            },
        )
        assert upd.status_code == 200
        cfg = upd.json()["implementation_config"]
        assert cfg["provider"] == "tavily"
        assert cfg["has_api_key"] is True
        assert "api_key_encrypted" not in cfg
        assert "supersecret" not in str(cfg)
        assert cfg["api_key_masked"].endswith("9999")

        # Updating timeout without passing api_key preserves existing encrypted secret
        upd2 = await client.put(
            f"/api/v1/tools/{ws_id}",
            json={"implementation_config": {"provider": "tavily", "timeout": 25}},
        )
        assert upd2.status_code == 200
        assert upd2.json()["implementation_config"]["has_api_key"] is True
        assert upd2.json()["implementation_config"]["timeout"] == 25

    await test_engine.dispose()
