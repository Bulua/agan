from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SandboxConfig(BaseModel):
    """Resource, security, and terminal configuration for a CTF sandbox container."""

    image: str = "agan-ctf-sandbox:latest"
    platform: str = "linux/amd64"
    cpu_count: float = 2.0
    mem_limit: str = "2g"
    memswap_limit: str = "2g"
    pids_limit: int = 256
    network_mode: str = "bridge"
    cap_drop: list[str] = Field(default_factory=lambda: ["ALL"])
    # SYS_PTRACE is required for GDB / pwndbg / gef debugging in Pwn & Reverse challenges.
    cap_add: list[str] = Field(
        default_factory=lambda: ["SYS_PTRACE", "CHOWN", "DAC_OVERRIDE", "SETUID", "SETGID"]
    )
    security_opt: list[str] = Field(default_factory=lambda: ["seccomp=unconfined"])
    storage_opt: dict[str, str] | None = None
    workspace_host_dir: Path | None = None
    attachments_host_dir: Path | None = None
    attachments_readonly: bool = True
    skills_host_dir: Path | None = None
    default_timeout: int = 30
    max_timeout: int = 180
    output_max_chars: int = 8000
    output_max_lines: int = 120
    output_head_lines: int = 50
    output_tail_lines: int = 50
    labels: dict[str, str] = Field(default_factory=dict)


class ExecResult(BaseModel):
    """Result of executing a shell command inside the sandbox."""

    command: str
    output: str
    exit_code: int
    timed_out: bool = False
    truncated: bool = False
    total_lines: int = 0
    total_chars: int = 0
    full_log_path: str | None = None
    cwd: str | None = None
    duration_ms: int = 0


class BaseSandbox(ABC):
    """Abstract sandbox interface implemented by DockerSandbox and mock sandboxes."""

    sandbox_id: str

    @abstractmethod
    async def exec_command(
        self,
        command: str,
        *,
        timeout: int | None = None,
        window_name: str = "ctf_main",
    ) -> ExecResult:
        """Execute a command in a persistent shell session."""

    @abstractmethod
    async def read_file(
        self,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Read text file content from the sandbox."""

    @abstractmethod
    async def write_file(self, path: str, content: str) -> str:
        """Write text file content into the sandbox."""

    @abstractmethod
    async def get_status(self) -> dict[str, Any]:
        """Return sandbox health and metadata."""

    @abstractmethod
    async def close(self, *, remove: bool = True) -> None:
        """Stop and optionally remove the sandbox."""
