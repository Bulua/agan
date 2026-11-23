from __future__ import annotations

import asyncio
import io
import posixpath
import shlex
import tarfile
import time
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, NotFound

from app.engine.sandbox.base import BaseSandbox, ExecResult, SandboxConfig
from app.engine.sandbox.terminal import PersistentTmuxTerminal, _build_tar_bytes
from app.engine.sandbox.truncator import OutputTruncator


class DockerSandbox(BaseSandbox):
    """Isolated Ubuntu 22.04 Docker sandbox with persistent tmux shell and file I/O."""

    def __init__(
        self,
        sandbox_id: str,
        container: Any,
        config: SandboxConfig,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.container = container
        self.config = config
        self.truncator = OutputTruncator(
            max_chars=config.output_max_chars,
            max_lines=config.output_max_lines,
            head_lines=config.output_head_lines,
            tail_lines=config.output_tail_lines,
        )
        self.terminal = PersistentTmuxTerminal(
            container=container,
            truncator=self.truncator,
            default_timeout=config.default_timeout,
            max_timeout=config.max_timeout,
        )

    @property
    def container_id(self) -> str:
        return self.container.id

    async def exec_command(
        self,
        command: str,
        *,
        timeout: int | None = None,
        window_name: str = "ctf_main",
    ) -> ExecResult:
        return await self.terminal.execute(
            command,
            timeout=timeout,
            session_name=window_name,
        )

    async def read_file(
        self,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Read a file inside the container with optional 1-based inclusive line range."""
        norm_path = path if path.startswith("/") else posixpath.join("/workspace", path)
        safe_path = shlex.quote(norm_path)

        if start_line is not None or end_line is not None:
            s = max(1, start_line or 1)
            e = end_line if end_line is not None else s + 200
            cmd = f"test -f {safe_path} && sed -n '{s},{e}p' {safe_path}"
        else:
            # Guard full-file reads by max_chars * 2
            limit_bytes = self.config.output_max_chars * 2
            cmd = f"test -f {safe_path} && head -c {limit_bytes} {safe_path}"

        def _run() -> tuple[int, bytes]:
            res = self.container.exec_run(["bash", "-c", cmd], demux=False)
            return res.exit_code, res.output or b""

        code, out = await asyncio.to_thread(_run)
        if code != 0:
            raise FileNotFoundError(f"File not found or unreadable in sandbox: {norm_path}")

        text = out.decode("utf-8", errors="replace")
        truncated = self.truncator.truncate(text, full_log_path=norm_path)
        return truncated.text

    async def write_file(self, path: str, content: str) -> str:
        """Write text content into the container via Docker tar archive (no shell escaping)."""
        norm_path = path if path.startswith("/") else posixpath.join("/workspace", path)
        parent_dir = posixpath.dirname(norm_path) or "/"
        filename = posixpath.basename(norm_path)

        def _ensure_and_put() -> None:
            self.container.exec_run(["mkdir", "-p", parent_dir])
            tar_bytes = _build_tar_bytes(filename, content.encode("utf-8"))
            self.container.put_archive(parent_dir, tar_bytes)

        await asyncio.to_thread(_ensure_and_put)
        return norm_path

    async def copy_bytes_in(self, dest_dir: str, filename: str, data: bytes) -> str:
        """Upload raw binary bytes (e.g. challenge attachment) into the container."""

        def _put() -> None:
            self.container.exec_run(["mkdir", "-p", dest_dir])
            tar_bytes = _build_tar_bytes(filename, data)
            self.container.put_archive(dest_dir, tar_bytes)

        await asyncio.to_thread(_put)
        return posixpath.join(dest_dir, filename)

    async def read_bytes_out(self, path: str) -> bytes:
        """Retrieve raw file bytes from the container."""
        norm_path = path if path.startswith("/") else posixpath.join("/workspace", path)

        def _get() -> bytes:
            stream, _ = self.container.get_archive(norm_path)
            raw_tar = b"".join(stream)
            with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:*") as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        extracted = tar.extractfile(member)
                        if extracted is not None:
                            return extracted.read()
            raise FileNotFoundError(f"Could not extract file: {norm_path}")

        return await asyncio.to_thread(_get)

    async def get_status(self) -> dict[str, Any]:
        def _inspect() -> dict[str, Any]:
            self.container.reload()
            return {
                "sandbox_id": self.sandbox_id,
                "container_id": self.container.id[:12],
                "name": self.container.name,
                "status": self.container.status,
                "image": self.config.image,
            }

        return await asyncio.to_thread(_inspect)

    async def close(self, *, remove: bool = True) -> None:
        def _cleanup() -> None:
            try:
                self.container.stop(timeout=3)
            except (NotFound, DockerException):
                pass
            if remove:
                try:
                    self.container.remove(force=True)
                except (NotFound, DockerException):
                    pass

        await asyncio.to_thread(_cleanup)


class SandboxManager:
    """Manages lifecycle, resource quotas, mounts, and reuse of Docker CTF sandboxes."""

    def __init__(self, docker_client: docker.DockerClient | None = None) -> None:
        self._client = docker_client
        self._sandboxes: dict[str, DockerSandbox] = {}
        self._lock = asyncio.Lock()

    def _get_client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    async def create_or_get(
        self,
        sandbox_id: str,
        config: SandboxConfig | None = None,
        *,
        reuse_existing: bool = True,
    ) -> DockerSandbox:
        """Create a new isolated container or return an existing running sandbox."""
        cfg = config or SandboxConfig()

        async with self._lock:
            if reuse_existing and sandbox_id in self._sandboxes:
                existing = self._sandboxes[sandbox_id]
                try:
                    status = await existing.get_status()
                    if status.get("status") == "running":
                        return existing
                except DockerException:
                    self._sandboxes.pop(sandbox_id, None)

            container = await asyncio.to_thread(self._spawn_container, sandbox_id, cfg)
            sandbox = DockerSandbox(sandbox_id=sandbox_id, container=container, config=cfg)
            await sandbox.terminal.ensure_session("ctf_main")
            self._sandboxes[sandbox_id] = sandbox
            return sandbox

    def _spawn_container(self, sandbox_id: str, cfg: SandboxConfig) -> Any:
        client = self._get_client()
        safe_name = f"agan-sbx-{sandbox_id[:24]}-{int(time.time()) % 10000}"

        volumes: dict[str, dict[str, str]] = {}
        if cfg.workspace_host_dir is not None:
            host_ws = Path(cfg.workspace_host_dir).resolve()
            host_ws.mkdir(parents=True, exist_ok=True)
            volumes[str(host_ws)] = {"bind": "/workspace", "mode": "rw"}

        if cfg.attachments_host_dir is not None:
            host_att = Path(cfg.attachments_host_dir).resolve()
            host_att.mkdir(parents=True, exist_ok=True)
            mode = "ro" if cfg.attachments_readonly else "rw"
            volumes[str(host_att)] = {"bind": "/workspace/challenge", "mode": mode}

        if cfg.skills_host_dir is not None:
            host_skills = Path(cfg.skills_host_dir).resolve()
            if host_skills.exists() and host_skills.is_dir():
                volumes[str(host_skills)] = {"bind": "/opt/agan/skills", "mode": "ro"}

        labels = {"managed_by": "agan-ctf", "sandbox_id": sandbox_id, **cfg.labels}

        run_kwargs: dict[str, Any] = {
            "image": cfg.image,
            "name": safe_name,
            "detach": True,
            "tty": True,
            "stdin_open": True,
            "platform": cfg.platform,
            "nano_cpus": int(cfg.cpu_count * 1_000_000_000),
            "mem_limit": cfg.mem_limit,
            "memswap_limit": cfg.memswap_limit,
            "pids_limit": cfg.pids_limit,
            "network_mode": cfg.network_mode,
            "cap_drop": cfg.cap_drop,
            "cap_add": cfg.cap_add,
            "security_opt": cfg.security_opt,
            "volumes": volumes or None,
            "labels": labels,
            "working_dir": "/workspace",
        }
        if cfg.storage_opt:
            run_kwargs["storage_opt"] = cfg.storage_opt

        return client.containers.run(**run_kwargs)

    def get(self, sandbox_id: str) -> DockerSandbox | None:
        return self._sandboxes.get(sandbox_id)

    async def list_active(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for sbx in list(self._sandboxes.values()):
            try:
                results.append(await sbx.get_status())
            except DockerException:
                pass
        return results

    async def remove(self, sandbox_id: str, *, remove_container: bool = True) -> None:
        async with self._lock:
            sbx = self._sandboxes.pop(sandbox_id, None)
        if sbx is not None:
            await sbx.close(remove=remove_container)

    async def cleanup_all(self) -> None:
        ids = list(self._sandboxes.keys())
        for sid in ids:
            await self.remove(sid, remove_container=True)
