from __future__ import annotations

import asyncio
import io
import shlex
import tarfile
import time
import uuid
from typing import Any

from app.engine.sandbox.base import ExecResult
from app.engine.sandbox.truncator import OutputTruncator

LOG_DIR = "/tmp/agan_logs"
CMD_DIR = "/tmp/agan_cmds"


def _build_tar_bytes(filename: str, content: bytes, mode: int = 0o644) -> bytes:
    """Create an in-memory POSIX tar archive containing a single file."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=filename)
        info.size = len(content)
        info.mode = mode
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class PersistentTmuxTerminal:
    """Persistent shell backed by tmux inside a Docker container.

    Preserves working directory (`cd`), environment variables (`export`),
    virtualenvs, shell functions, and background jobs across commands.
    """

    def __init__(
        self,
        container: Any,
        truncator: OutputTruncator,
        *,
        default_timeout: int = 30,
        max_timeout: int = 180,
        workdir: str = "/workspace",
    ) -> None:
        self._container = container
        self._truncator = truncator
        self._default_timeout = default_timeout
        self._max_timeout = max_timeout
        self._workdir = workdir
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, session_name: str) -> asyncio.Lock:
        if session_name not in self._locks:
            self._locks[session_name] = asyncio.Lock()
        return self._locks[session_name]

    async def _exec_raw(self, cmd: list[str] | str) -> tuple[int, bytes]:
        """Run a lightweight control command via Docker exec without blocking the event loop."""

        def _run() -> tuple[int, bytes]:
            res = self._container.exec_run(cmd, demux=False)
            return res.exit_code, res.output or b""

        return await asyncio.to_thread(_run)

    async def _put_file(self, directory: str, filename: str, content: bytes) -> None:
        tar_data = _build_tar_bytes(filename, content)
        await asyncio.to_thread(self._container.put_archive, directory, tar_data)

    async def ensure_session(self, session_name: str = "ctf_main") -> None:
        """Ensure tmux session and helper directories exist inside the container."""
        safe_session = shlex.quote(session_name)
        safe_workdir = shlex.quote(self._workdir)
        init_cmd = (
            f"mkdir -p {LOG_DIR} {CMD_DIR} {safe_workdir} && "
            f"(tmux has-session -t {safe_session} 2>/dev/null || "
            f"tmux new-session -d -s {safe_session} -c {safe_workdir} 'bash --norc')"
        )
        code, out = await self._exec_raw(["bash", "-lc", init_cmd])
        if code != 0:
            decoded = out.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Failed to initialize tmux session '{session_name}' inside container: {decoded}"
            )

    async def _is_session_alive(self, session_name: str) -> bool:
        code, _ = await self._exec_raw(["tmux", "has-session", "-t", session_name])
        return code == 0

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        session_name: str = "ctf_main",
    ) -> ExecResult:
        """Execute a command in the persistent tmux session with timeout & truncation."""
        effective_timeout = min(
            max(1, timeout if timeout is not None else self._default_timeout),
            self._max_timeout,
        )
        lock = self._get_lock(session_name)

        async with lock:
            start_ts = time.monotonic()
            await self.ensure_session(session_name)

            cmd_id = uuid.uuid4().hex[:12]
            script_name = f"cmd_{cmd_id}.sh"
            script_path = f"{CMD_DIR}/{script_name}"
            log_path = f"{LOG_DIR}/cmd_{cmd_id}.log"
            done_path = f"{LOG_DIR}/cmd_{cmd_id}.done"

            # Write script via tar archive to eliminate shell-escaping & multiline issues.
            script_bytes = (command.rstrip("\n") + "\n").encode("utf-8")
            await self._put_file(CMD_DIR, script_name, script_bytes)

            # Group command `{ source ...; }` executes in the CURRENT bash process,
            # preserving cd, env vars, virtualenvs, and background jobs.
            dispatch_line = (
                f"set +e; {{ source {script_path}; __agan_rc=$?; }} "
                f">{log_path} 2>&1; "
                f"printf '%s\\n%s\\n' \"$__agan_rc\" \"$PWD\" >{done_path}"
            )

            # Send literally (-l) then press Enter
            code, err_out = await self._exec_raw(
                ["tmux", "send-keys", "-t", session_name, "-l", dispatch_line]
            )
            if code != 0:
                raise RuntimeError(
                    f"tmux send-keys failed: {err_out.decode('utf-8', errors='replace')}"
                )
            await self._exec_raw(["tmux", "send-keys", "-t", session_name, "Enter"])

            # Poll for completion marker or timeout
            timed_out = False
            shell_exited = False
            exit_code = -1
            cwd: str | None = None
            deadline = start_ts + effective_timeout
            poll_interval = 0.08

            while True:
                check_code, check_out = await self._exec_raw(
                    ["bash", "-c", f"test -f {done_path} && cat {done_path}"]
                )
                if check_code == 0:
                    lines = check_out.decode("utf-8", errors="replace").splitlines()
                    if lines:
                        try:
                            exit_code = int(lines[0].strip())
                        except ValueError:
                            exit_code = 0
                    if len(lines) >= 2:
                        cwd = lines[1].strip() or None
                    break

                if time.monotonic() >= deadline:
                    timed_out = True
                    exit_code = 124
                    await self._interrupt_session(session_name)
                    break

                if not await self._is_session_alive(session_name):
                    shell_exited = True
                    exit_code = 1
                    await self.ensure_session(session_name)
                    break

                await asyncio.sleep(poll_interval)
                # Gently back off polling interval for long-running commands
                if poll_interval < 0.25:
                    poll_interval = min(0.25, poll_interval + 0.02)

            # Read log with container-side size guard to prevent OOM on giant outputs
            trunc_result = await self._read_log_bounded(log_path)
            output_text = trunc_result.text

            if timed_out:
                notice = (
                    f"\n[COMMAND TIMED OUT after {effective_timeout}s — "
                    f"sent Ctrl+C to interrupt. Partial output shown above.]"
                )
                output_text = (output_text + notice).strip()
            elif shell_exited:
                notice = (
                    "\n[SHELL EXITED during command execution; "
                    "a fresh tmux shell session was automatically respawned.]"
                )
                output_text = (output_text + notice).strip()

            # Clean up ephemeral script and done marker; keep log_path if truncated
            cleanup_targets = [script_path, done_path]
            if not trunc_result.truncated:
                cleanup_targets.append(log_path)
            await self._exec_raw(["rm", "-f", *cleanup_targets])

            duration_ms = int((time.monotonic() - start_ts) * 1000)
            return ExecResult(
                command=command,
                output=output_text,
                exit_code=exit_code,
                timed_out=timed_out,
                truncated=trunc_result.truncated,
                total_lines=trunc_result.total_lines,
                total_chars=trunc_result.total_chars,
                full_log_path=log_path if trunc_result.truncated else None,
                cwd=cwd,
                duration_ms=duration_ms,
            )

    async def _interrupt_session(self, session_name: str) -> None:
        """Send Ctrl+C to recover the interactive tmux prompt after a timeout."""
        await self._exec_raw(["tmux", "send-keys", "-t", session_name, "C-c"])
        await asyncio.sleep(0.35)
        await self._exec_raw(["tmux", "send-keys", "-t", session_name, "C-c"])
        await asyncio.sleep(0.15)
        await self._exec_raw(["tmux", "send-keys", "-t", session_name, "Enter"])

    async def _read_log_bounded(self, log_path: str):
        """Inspect log size inside container first; only transfer head/tail if oversized."""
        stat_code, stat_out = await self._exec_raw(
            [
                "bash",
                "-c",
                f"if [ -f {log_path} ]; then wc -c -l < {log_path}; else echo '0 0'; fi",
            ]
        )
        total_lines = 0
        total_bytes = 0
        if stat_code == 0:
            parts = stat_out.decode("utf-8", errors="replace").split()
            # `wc -c -l` prints: <lines> <bytes>
            if len(parts) >= 2:
                try:
                    total_lines = int(parts[0])
                    total_bytes = int(parts[1])
                except ValueError:
                    pass

        if (
            total_bytes <= self._truncator.max_chars
            and total_lines <= self._truncator.max_lines
        ):
            _, raw = await self._exec_raw(
                ["bash", "-c", f"test -f {log_path} && cat {log_path} || true"]
            )
            text = raw.decode("utf-8", errors="replace")
            return self._truncator.truncate(text, full_log_path=log_path)

        # Oversized log: read bounded head & tail directly inside container
        half_budget = max(500, self._truncator.max_chars // 2)
        head_n = self._truncator.head_lines
        tail_n = self._truncator.tail_lines

        if total_lines > head_n + tail_n:
            _, head_bytes = await self._exec_raw(
                ["bash", "-c", f"head -n {head_n} {log_path} | head -c {half_budget}"]
            )
            _, tail_bytes = await self._exec_raw(
                ["bash", "-c", f"tail -n {tail_n} {log_path} | tail -c {half_budget}"]
            )
            omitted_lines = max(0, total_lines - head_n - tail_n)
        else:
            _, head_bytes = await self._exec_raw(
                ["bash", "-c", f"head -c {half_budget} {log_path}"]
            )
            _, tail_bytes = await self._exec_raw(
                ["bash", "-c", f"tail -c {half_budget} {log_path}"]
            )
            omitted_lines = 0

        head_text = head_bytes.decode("utf-8", errors="replace")
        tail_text = tail_bytes.decode("utf-8", errors="replace")
        formatted = self._truncator.format_truncated_output(
            head_text=head_text,
            tail_text=tail_text,
            total_lines=total_lines,
            total_chars=total_bytes,
            omitted_lines=omitted_lines,
            full_log_path=log_path,
        )
        from app.engine.sandbox.truncator import TruncationResult

        return TruncationResult(
            text=formatted,
            truncated=True,
            total_lines=total_lines,
            total_chars=total_bytes,
            full_log_path=log_path,
        )
