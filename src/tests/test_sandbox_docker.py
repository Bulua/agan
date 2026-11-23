from __future__ import annotations

import os

import docker
import pytest

from app.engine.sandbox.base import SandboxConfig
from app.engine.sandbox.manager import SandboxManager

SANDBOX_IMAGE = os.getenv("AGAN_TEST_SANDBOX_IMAGE", "agan-ctf-sandbox:latest")


def _docker_and_image_ready() -> tuple[bool, str]:
    try:
        client = docker.from_env()
        client.ping()
        client.images.get(SANDBOX_IMAGE)
        return True, ""
    except Exception as exc:
        return False, f"Docker or sandbox image '{SANDBOX_IMAGE}' unavailable: {exc}"


_READY, _SKIP_REASON = _docker_and_image_ready()


@pytest.mark.asyncio
@pytest.mark.skipif(not _READY, reason=_SKIP_REASON)
async def test_docker_sandbox_persistent_shell_timeout_and_truncation() -> None:
    mgr = SandboxManager()
    sbx = await mgr.create_or_get(
        "test-integration",
        SandboxConfig(
            image=SANDBOX_IMAGE,
            default_timeout=10,
            output_max_chars=2000,
            output_max_lines=40,
            output_head_lines=10,
            output_tail_lines=10,
        ),
    )
    try:
        # 1. Persistent cd and env vars across commands
        r1 = await sbx.exec_command("mkdir -p /workspace/sub && cd /workspace/sub && export AGAN_VAR=hello_ctf")
        assert r1.exit_code == 0
        assert r1.cwd == "/workspace/sub"

        r2 = await sbx.exec_command("pwd && echo $AGAN_VAR")
        assert r2.exit_code == 0
        assert "/workspace/sub" in r2.output
        assert "hello_ctf" in r2.output

        # 2. Timeout + Ctrl+C recovery without poisoning subsequent commands
        r_timeout = await sbx.exec_command("echo started && sleep 20", timeout=2)
        assert r_timeout.timed_out is True
        assert r_timeout.exit_code == 124
        assert "started" in r_timeout.output

        r_after = await sbx.exec_command("echo recovered_ok")
        assert r_after.exit_code == 0
        assert "recovered_ok" in r_after.output

        # 3. Oversized output truncation + full log persisted inside container
        r_big = await sbx.exec_command("seq 1 1000")
        assert r_big.truncated is True
        assert r_big.full_log_path is not None
        assert "OUTPUT TRUNCATED" in r_big.output
        assert r_big.total_lines == 1000

        # Agent can inspect full log via grep
        r_grep = await sbx.exec_command(f"grep -x 500 {r_big.full_log_path}")
        assert r_grep.exit_code == 0
        assert "500" in r_grep.output

        # 4. File write & ranged read
        written = await sbx.write_file("demo.txt", "line1\nline2\nline3\nline4\n")
        assert written == "/workspace/demo.txt"
        partial = await sbx.read_file("demo.txt", start_line=2, end_line=3)
        assert partial.strip() == "line2\nline3"
    finally:
        await mgr.remove("test-integration", remove_container=True)
