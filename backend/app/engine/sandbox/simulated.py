from __future__ import annotations

import base64
import posixpath
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

from app.engine.sandbox.base import BaseSandbox, ExecResult
from app.engine.sandbox.path_guard import resolve_jailed_path
from app.engine.sandbox.truncator import OutputTruncator

DEFAULT_DEMO_FILES: dict[str, str] = {
    "/workspace/challenge/README.txt": (
        "Welcome to AGAN-CTF Simulated Sandbox!\n"
        "A suspicious backup config was found in `config.bak`.\n"
        "Inspect the files in `/workspace/challenge` to recover the flag.\n"
    ),
    "/workspace/challenge/config.bak": (
        "# Legacy Service Backup\n"
        "service_port=8080\n"
        "debug_mode=true\n"
        "# Note: operator encoded the secret token in base64 twice\n"
        "encoded_secret=Wm14aFozdGhaMkZ1WDNOcGJYVnNZWFJsWkY5ellXNWtZbTk0WDI5cmZRPT0=\n"
    ),
}


class SimulatedSandbox(BaseSandbox):
    """Safe virtual sandbox for instant Web UI & LLM testing without Docker.

    Never executes commands on the host OS. When `workspace_host_dir` is supplied,
    all `/workspace` files are persisted to that jailed host directory across turns
    and restarts (preventing path traversal outside the directory). When omitted,
    operates purely in memory.
    """

    def __init__(
        self,
        sandbox_id: str,
        *,
        workspace_host_dir: Path | None = None,
        attachments_host_dir: Path | None = None,
        initial_files: dict[str, str] | None = None,
        seed_demo_if_empty: bool = True,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.cwd = "/workspace"
        self.env: dict[str, str] = {"HOME": "/root", "PWD": "/workspace", "USER": "root"}
        self.workspace_host_dir: Path | None = None
        # In-memory store (used for all files when workspace_host_dir is None,
        # and for transient /tmp/... helper files when disk-backed).
        self.files: dict[str, str] = {}
        self.truncator = OutputTruncator()

        if workspace_host_dir is not None:
            self.workspace_host_dir = Path(workspace_host_dir).resolve()
            self.workspace_host_dir.mkdir(parents=True, exist_ok=True)

        loaded_any = False

        if attachments_host_dir and attachments_host_dir.exists():
            for fp in sorted(attachments_host_dir.iterdir()):
                if fp.is_file():
                    if self.workspace_host_dir is not None:
                        dest = self.workspace_host_dir / "challenge" / fp.name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if not dest.exists():
                            shutil.copy2(fp, dest)
                    else:
                        raw = fp.read_bytes()
                        text = raw.decode("utf-8", errors="replace")
                        self.files[f"/workspace/challenge/{fp.name}"] = text
                    loaded_any = True

        if initial_files:
            for k, v in initial_files.items():
                self._write_sync(k, v)
                loaded_any = True

        if seed_demo_if_empty and not loaded_any:
            if self.workspace_host_dir is not None:
                has_any_disk_file = any(
                    p.is_file() for p in self.workspace_host_dir.rglob("*")
                )
                if not has_any_disk_file:
                    for vpath, content in DEFAULT_DEMO_FILES.items():
                        self._write_sync(vpath, content)
            else:
                self.files.update(DEFAULT_DEMO_FILES)

    def _resolve(self, path: str) -> str:
        if not path:
            return self.cwd
        slash = path.replace("\\", "/")
        if slash.startswith("/"):
            return posixpath.normpath(slash)
        return posixpath.normpath(posixpath.join(self.cwd, slash))

    def _is_workspace_path(self, resolved: str) -> bool:
        return resolved == "/workspace" or resolved.startswith("/workspace/")

    def _to_disk_path(self, raw_or_virtual: str) -> tuple[Path, str]:
        assert self.workspace_host_dir is not None
        return resolve_jailed_path(
            raw_or_virtual,
            self.workspace_host_dir,
            virtual_cwd=self.cwd,
            virtual_root="/workspace",
        )

    def _iter_all_virtual_files(self) -> list[str]:
        found: set[str] = set(self.files.keys())
        if self.workspace_host_dir is not None and self.workspace_host_dir.exists():
            for p in self.workspace_host_dir.rglob("*"):
                if p.is_file():
                    rel = p.relative_to(self.workspace_host_dir).as_posix()
                    found.add(posixpath.join("/workspace", rel))
        return sorted(found)

    def _file_exists(self, raw_path: str) -> bool:
        resolved = self._resolve(raw_path)
        if self.workspace_host_dir is not None and self._is_workspace_path(resolved):
            try:
                hp, _ = self._to_disk_path(resolved)
                return hp.is_file()
            except (PermissionError, ValueError):
                return False
        return resolved in self.files

    def _read_sync(self, raw_path: str) -> str:
        resolved = self._resolve(raw_path)
        if self.workspace_host_dir is not None and self._is_workspace_path(resolved):
            hp, virt = self._to_disk_path(raw_path)
            if not hp.is_file():
                raise FileNotFoundError(f"File not found in simulated sandbox: {virt}")
            return hp.read_text(encoding="utf-8", errors="replace")
        if resolved not in self.files:
            raise FileNotFoundError(f"File not found in simulated sandbox: {resolved}")
        return self.files[resolved]

    def _write_sync(self, raw_path: str, content: str, *, append: bool = False) -> str:
        resolved = self._resolve(raw_path)
        if self.workspace_host_dir is not None and (
            self._is_workspace_path(resolved) or not resolved.startswith("/tmp/")
        ):
            hp, virt = self._to_disk_path(raw_path)
            hp.parent.mkdir(parents=True, exist_ok=True)
            if append:
                with hp.open("a", encoding="utf-8", newline="") as fh:
                    fh.write(content)
            else:
                hp.write_text(content, encoding="utf-8")
            return virt
        if append and resolved in self.files:
            self.files[resolved] = self.files[resolved] + content
        else:
            self.files[resolved] = content
        return resolved

    def _expand_vars(self, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            var = m.group(1) or m.group(2)
            return self.env.get(var, "")

        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", repl, text
        )

    async def exec_command(
        self,
        command: str,
        *,
        timeout: int | None = None,
        window_name: str = "ctf_main",
    ) -> ExecResult:
        start = time.monotonic()
        # Support chained commands separated by `&&` or `;`
        segments = [
            seg.strip() for seg in re.split(r"\s*(?:&&|;)\s*", command.strip()) if seg.strip()
        ]
        outputs: list[str] = []
        exit_code = 0

        for seg in segments:
            # Handle simple pipeline `cmd1 | cmd2 | ...`
            pipe_parts = [p.strip() for p in seg.split("|")]
            stdin_data = ""
            seg_code = 0
            for part in pipe_parts:
                seg_code, stdin_data = self._run_single(part, stdin_data)
                if seg_code != 0:
                    break
            if stdin_data:
                outputs.append(stdin_data)
            exit_code = seg_code
            if exit_code != 0 and "&&" in command:
                break

        raw_out = "\n".join(o for o in outputs if o)
        trunc = self.truncator.truncate(raw_out, full_log_path="/tmp/agan_logs/sim_last.log")
        duration_ms = int((time.monotonic() - start) * 1000)
        return ExecResult(
            command=command,
            output=trunc.text,
            exit_code=exit_code,
            timed_out=False,
            truncated=trunc.truncated,
            total_lines=trunc.total_lines,
            total_chars=trunc.total_chars,
            full_log_path=trunc.full_log_path if trunc.truncated else None,
            cwd=self.cwd,
            duration_ms=duration_ms,
        )

    def _run_single(self, raw_cmd: str, stdin_data: str) -> tuple[int, str]:
        expanded = self._expand_vars(raw_cmd)
        try:
            tokens = shlex.split(expanded)
        except ValueError as exc:
            return 2, f"bash: syntax error: {exc}"
        if not tokens:
            return 0, ""

        cmd, args = tokens[0], tokens[1:]

        if cmd == "pwd":
            return 0, self.cwd

        if cmd == "cd":
            target = self._resolve(args[0] if args else "/workspace")
            if self.workspace_host_dir is not None and not self._is_workspace_path(target):
                return 1, f"cd: {target}: Access restricted to /workspace"
            self.cwd = target
            self.env["PWD"] = target
            return 0, ""

        if cmd == "export":
            for arg in args:
                if "=" in arg:
                    k, v = arg.split("=", 1)
                    self.env[k] = v
            return 0, ""

        if cmd == "env":
            return 0, "\n".join(f"{k}={v}" for k, v in sorted(self.env.items()))

        if cmd == "echo":
            if args and args[0] == "-n":
                args = args[1:]
            return 0, " ".join(args)

        if cmd == "ls":
            paths = [a for a in args if not a.startswith("-")]
            target = self._resolve(paths[0] if paths else self.cwd)
            if self.workspace_host_dir is not None and self._is_workspace_path(target):
                try:
                    hp, _ = self._to_disk_path(target)
                except PermissionError as exc:
                    return 2, f"ls: {exc}"
                if not hp.exists():
                    return 2, f"ls: cannot access '{target}': No such file or directory"
                if hp.is_file():
                    return 0, hp.name
                return 0, "\n".join(sorted(p.name for p in hp.iterdir()))

            prefix = target.rstrip("/") + "/"
            entries: set[str] = set()
            for fpath in self._iter_all_virtual_files():
                if fpath == target:
                    entries.add(posixpath.basename(fpath))
                elif fpath.startswith(prefix):
                    rel = fpath[len(prefix) :]
                    entries.add(rel.split("/", 1)[0])
            if not entries and target not in ("/workspace", "/workspace/challenge", "/"):
                return 2, f"ls: cannot access '{target}': No such file or directory"
            return 0, "\n".join(sorted(entries))

        if cmd == "find":
            target = self._resolve(
                args[0] if args and not args[0].startswith("-") else self.cwd
            )
            prefix = target.rstrip("/") + "/"
            found = sorted(
                fp
                for fp in self._iter_all_virtual_files()
                if fp == target or fp.startswith(prefix)
            )
            return 0, "\n".join(found)

        if cmd in ("cat", "strings"):
            if not args and stdin_data:
                return 0, stdin_data
            chunks: list[str] = []
            for a in args:
                if a.startswith("-"):
                    continue
                try:
                    chunks.append(self._read_sync(a))
                except (FileNotFoundError, PermissionError) as exc:
                    return 1, f"{cmd}: {a}: {exc}"
            return 0, "\n".join(chunks)

        if cmd in ("head", "tail"):
            n = 10
            file_args: list[str] = []
            i = 0
            while i < len(args):
                if args[i] == "-n" and i + 1 < len(args):
                    n = int(args[i + 1])
                    i += 2
                elif args[i].startswith("-") and args[i][1:].isdigit():
                    n = int(args[i][1:])
                    i += 1
                else:
                    file_args.append(args[i])
                    i += 1
            content = stdin_data
            if file_args:
                try:
                    content = self._read_sync(file_args[0])
                except (FileNotFoundError, PermissionError) as exc:
                    return 1, f"{cmd}: cannot open '{file_args[0]}': {exc}"
            lines = content.splitlines()
            sliced = lines[:n] if cmd == "head" else lines[-n:]
            return 0, "\n".join(sliced)

        if cmd == "grep":
            flags = [a for a in args if a.startswith("-")]
            rest = [a for a in args if not a.startswith("-")]
            if not rest:
                return 2, "grep: missing pattern"
            pattern = rest[0]
            re_flags = re.IGNORECASE if any("i" in f for f in flags) else 0
            show_num = any("n" in f for f in flags)
            sources: list[tuple[str, str]] = []
            if len(rest) > 1:
                for fn in rest[1:]:
                    try:
                        sources.append((fn, self._read_sync(fn)))
                    except (FileNotFoundError, PermissionError) as exc:
                        return 2, f"grep: {fn}: {exc}"
            else:
                sources.append(("", stdin_data))
            matches: list[str] = []
            for label, text in sources:
                for idx, line in enumerate(text.splitlines(), 1):
                    if re.search(pattern, line, re_flags):
                        prefix = f"{label}:" if label and len(sources) > 1 else ""
                        num = f"{idx}:" if show_num else ""
                        matches.append(f"{prefix}{num}{line}")
            return (0 if matches else 1), "\n".join(matches)

        if cmd == "base64":
            decode = any(a in ("-d", "--decode", "-D") for a in args)
            file_args = [a for a in args if not a.startswith("-")]
            src = stdin_data
            if file_args:
                try:
                    src = self._read_sync(file_args[0])
                except (FileNotFoundError, PermissionError) as exc:
                    return 1, f"base64: {file_args[0]}: {exc}"
            try:
                if decode:
                    cleaned = "".join(src.split())
                    decoded = base64.b64decode(cleaned).decode("utf-8", errors="replace")
                    return 0, decoded
                encoded = base64.b64encode(src.encode("utf-8")).decode("ascii")
                return 0, encoded
            except Exception as exc:
                return 1, f"base64: invalid input ({exc})"

        if cmd == "rev":
            src = stdin_data
            if args:
                try:
                    src = self._read_sync(args[0])
                except (FileNotFoundError, PermissionError):
                    src = ""
            return 0, "\n".join(line[::-1] for line in src.splitlines())

        if cmd == "file":
            out_lines = []
            for a in args:
                try:
                    txt = self._read_sync(a)
                    out_lines.append(f"{a}: ASCII/UTF-8 text ({len(txt.encode('utf-8'))} bytes)")
                except (FileNotFoundError, PermissionError):
                    out_lines.append(f"{a}: cannot open (No such file or directory)")
            return 0, "\n".join(out_lines)

        if cmd == "wc":
            src = stdin_data
            file_args = [a for a in args if not a.startswith("-")]
            if file_args:
                try:
                    src = self._read_sync(file_args[0])
                except (FileNotFoundError, PermissionError) as exc:
                    return 1, f"wc: {file_args[0]}: {exc}"
            return 0, f"{len(src.splitlines())} {len(src.split())} {len(src.encode('utf-8'))}"

        if cmd == "mkdir":
            if self.workspace_host_dir is not None:
                for a in args:
                    if not a.startswith("-"):
                        try:
                            hp, _ = self._to_disk_path(a)
                            hp.mkdir(parents=True, exist_ok=True)
                        except PermissionError as exc:
                            return 1, f"mkdir: {exc}"
            return 0, ""

        if cmd == "rm":
            for a in args:
                if a.startswith("-"):
                    continue
                resolved = self._resolve(a)
                if self.workspace_host_dir is not None and self._is_workspace_path(resolved):
                    try:
                        hp, _ = self._to_disk_path(resolved)
                        if hp.is_file():
                            hp.unlink(missing_ok=True)
                    except PermissionError as exc:
                        return 1, f"rm: {exc}"
                else:
                    self.files.pop(resolved, None)
            return 0, ""

        if cmd == "python3":
            return (
                1,
                "Simulated sandbox does not execute arbitrary host Python binaries for safety. "
                "Switch session sandbox_mode to `docker` for full Ubuntu 22.04 + Python execution, "
                "or use built-in shell commands (`ls`, `cat`, `grep`, `base64 -d`, `rev`, `head`, `tail`).",
            )

        return (
            127,
            f"Simulated sandbox: command '{cmd}' not emulated. "
            "Supported in simulated mode: ls, find, cat, head, tail, grep, base64, rev, strings, "
            "file, wc, pwd, cd, echo, export, env, mkdir, rm. Use `docker` mode for full Ubuntu CTF tools.",
        )

    async def read_file(
        self,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        content = self._read_sync(path)
        if start_line is not None or end_line is not None:
            lines = content.splitlines()
            s = max(1, start_line or 1) - 1
            e = end_line if end_line is not None else len(lines)
            return "\n".join(lines[s:e])
        return content

    async def write_file(self, path: str, content: str, *, append: bool = False) -> str:
        return self._write_sync(path, content, append=append)

    async def get_status(self) -> dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "mode": "simulated",
            "status": "running",
            "cwd": self.cwd,
            "file_count": len(self._iter_all_virtual_files()),
            "workspace_host_dir": str(self.workspace_host_dir) if self.workspace_host_dir else None,
        }

    async def close(self, *, remove: bool = True) -> None:
        return None
