from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base, get_db
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.skills.fs_loader import list_skills
from app.engine.skills.runtime import attach_skills_to_agent_run
from app.engine.tools.base import ToolExecutionContext
from app.engine.tools.registry import ToolRegistry
from app.main import create_app


@pytest.fixture
async def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_skills.db"
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "skills_dir", skills_dir)

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
        yield client, skills_dir

    await test_engine.dispose()


def _make_zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_repo_starter_skills_exist_on_disk() -> None:
    repo_skills = Path(__file__).resolve().parents[1] / "skills"
    items = list_skills(repo_skills)
    names = {s.name for s in items}
    assert "web-app-triage" in names
    assert "crypto-math-triage" in names
    crypto = next(s for s in items if s.name == "crypto-math-triage")
    assert crypto.file_count >= 2


@pytest.mark.asyncio
async def test_filesystem_skills_crud_zip_agent_binding_and_3stage_load(api_client) -> None:
    client, skills_dir = api_client

    # 1. Create a skill directory directly on disk via API
    create_res = await client.post(
        "/api/v1/skills",
        json={
            "name": "rsa-small-e",
            "description": "Quick checks for low public exponent RSA challenges.",
        },
    )
    assert create_res.status_code == 201
    created = create_res.json()
    assert created["name"] == "rsa-small-e"
    assert created["has_skill_md"] is True
    assert created["file_count"] == 1

    # 2. Write a nested script file inside the skill directory
    put_res = await client.put(
        "/api/v1/skills/rsa-small-e/file",
        json={
            "path": "scripts/check_iroot.py",
            "content": "def check(c, e):\n    return round(c ** (1 / e))\n",
        },
    )
    assert put_res.status_code == 200
    assert put_res.json()["path"] == "scripts/check_iroot.py"

    # 3. Verify tree structure includes scripts/check_iroot.py & read it back
    detail_res = await client.get("/api/v1/skills/rsa-small-e")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["file_count"] == 2
    dir_node = next(n for n in detail["tree"] if n["kind"] == "dir" and n["name"] == "scripts")
    assert any(c["path"] == "scripts/check_iroot.py" for c in dir_node["children"])

    read_res = await client.get(
        "/api/v1/skills/rsa-small-e/file", params={"path": "scripts/check_iroot.py"}
    )
    assert read_res.status_code == 200
    assert "def check" in read_res.json()["content"]

    # 4. Path traversal must be blocked
    trav_res = await client.get(
        "/api/v1/skills/rsa-small-e/file", params={"path": "../secret.txt"}
    )
    assert trav_res.status_code == 403

    # 5. Upload a multi-file .zip skill package
    good_zip = _make_zip_bytes(
        {
            "forensics-kit/SKILL.md": b"---\nname: forensics-kit\ndescription: Disk and PCAP triage.\n---\n\n# Forensics Kit\n\nInspect headers first.\n",
            "forensics-kit/references/magic-bytes.txt": b"PNG: 89 50 4E 47\nZIP: 50 4B 03 04\n",
        }
    )
    up_res = await client.post(
        "/api/v1/skills/upload-zip",
        files={"file": ("forensics-kit.zip", good_zip, "application/zip")},
    )
    assert up_res.status_code == 201
    uploaded = up_res.json()
    assert uploaded["name"] == "forensics-kit"
    assert uploaded["file_count"] == 2

    # 6. Zip-slip upload must be rejected with 403
    bad_zip = _make_zip_bytes({"../escape.txt": b"nope"})
    bad_res = await client.post(
        "/api/v1/skills/upload-zip",
        files={"file": ("bad.zip", bad_zip, "application/zip")},
    )
    assert bad_res.status_code == 403

    # 7. Bind filesystem skill_names to an Agent & verify clone preserves them
    model_res = await client.post(
        "/api/v1/models",
        json={
            "name": "Skill Test LLM",
            "provider": "openai",
            "model_type": "chat",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-skill-test",
            "model_id": "gpt-4o-mini",
        },
    )
    assert model_res.status_code == 201
    model_id = model_res.json()["id"]

    agent_res = await client.post(
        "/api/v1/agents",
        json={
            "name": "Crypto Specialist",
            "chat_model_id": model_id,
            "skill_names": ["rsa-small-e", "forensics-kit"],
        },
    )
    assert agent_res.status_code == 201
    agent_data = agent_res.json()
    assert agent_data["skill_names"] == ["rsa-small-e", "forensics-kit"]
    assert len(agent_data["skills"]) == 2
    assert all(s["exists"] for s in agent_data["skills"])

    clone_res = await client.post(f"/api/v1/agents/{agent_data['id']}/clone")
    assert clone_res.status_code == 201
    assert clone_res.json()["skill_names"] == ["rsa-small-e", "forensics-kit"]

    # 8. Test 3-stage progressive disclosure runtime (`attach_skills_to_agent_run` + `load_skill`)
    registry = ToolRegistry()
    prompt_addendum = attach_skills_to_agent_run(
        ["rsa-small-e", "forensics-kit"],
        skills_dir,
        registry,
    )
    assert "<available_skills>" in prompt_addendum
    assert "rsa-small-e" in prompt_addendum
    assert "forensics-kit" in prompt_addendum
    assert "def check" not in prompt_addendum  # Stage 1 never dumps full files into system prompt

    load_tool = registry.get("load_skill")
    assert load_tool is not None
    ctx = ToolExecutionContext(
        session_id="skill-test-session",
        sandbox=SimulatedSandbox(sandbox_id="skill-test-session"),
        scratchpad=WorkingScratchpad(),
    )

    # Stage 2: Load SKILL.md + bundled file manifest
    stage2 = await load_tool.execute({"name": "rsa-small-e"}, ctx)
    assert stage2.success is True
    assert "scripts/check_iroot.py" in stage2.output
    assert "/opt/agan/skills/rsa-small-e/" in stage2.output

    # Stage 3: Load nested script on demand
    stage3 = await load_tool.execute(
        {"name": "rsa-small-e", "file_path": "scripts/check_iroot.py"}, ctx
    )
    assert stage3.success is True
    assert "def check(c, e):" in stage3.output

    # Stage 3 traversal guard
    stage3_bad = await load_tool.execute(
        {"name": "rsa-small-e", "file_path": "../../etc/passwd"}, ctx
    )
    assert stage3_bad.success is False

    # 9. Delete nested file and whole skill directory
    del_file_res = await client.delete(
        "/api/v1/skills/rsa-small-e/file", params={"path": "scripts/check_iroot.py"}
    )
    assert del_file_res.status_code == 204
    assert not (skills_dir / "rsa-small-e" / "scripts").exists()

    del_skill_res = await client.delete("/api/v1/skills/rsa-small-e")
    assert del_skill_res.status_code == 204
    assert not (skills_dir / "rsa-small-e").exists()
