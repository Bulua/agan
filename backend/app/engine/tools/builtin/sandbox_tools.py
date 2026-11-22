from __future__ import annotations

import json
import posixpath
import re
import shlex
import uuid
from pathlib import Path
from typing import Any

from app.engine.sandbox.path_guard import (
    _match_glob,
    edit_jailed_text_file,
    grep_jailed_files,
    list_jailed_files,
    normalize_extensions,
    read_jailed_text_file,
    resolve_jailed_path,
    validate_extension,
    validate_file_size,
    write_jailed_text_file,
)
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult


class BashExecTool(BaseTool):
    name = "bash_exec"
    description = (
        "Execute a shell command in the persistent sandbox terminal (tmux-backed in Docker mode). "
        "Working directory (`cd`), environment variables, and background processes persist "
        "across calls. Large outputs are truncated and saved to a log file inside the sandbox."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute inside the sandbox.",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds (default 30, max 180).",
                "minimum": 1,
                "maximum": 180,
            },
        },
        "required": ["command"],
    }

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        command = str(arguments.get("command", "")).strip()
        if not command:
            return ToolResult(success=False, error="Parameter 'command' must be non-empty.")
        timeout = arguments.get("timeout")
        res = await ctx.sandbox.exec_command(
            command,
            timeout=int(timeout) if timeout is not None else None,
            window_name=ctx.window_name,
        )
        header = f"[exit_code={res.exit_code} | cwd={res.cwd or '?'} | {res.duration_ms}ms]"
        body = res.output if res.output else "(no output)"
        return ToolResult(
            success=(res.exit_code == 0 and not res.timed_out),
            output=f"{header}\n{body}",
            metadata={
                "exit_code": res.exit_code,
                "timed_out": res.timed_out,
                "truncated": res.truncated,
                "full_log_path": res.full_log_path,
                "cwd": res.cwd,
                "duration_ms": res.duration_ms,
            },
        )


class ReadFileTool(BaseTool):
    name = "read_file"
    description = (
        "Read a UTF-8 text file inside the sandbox workspace (or configured allowed root_dir). "
        "Supports optional 1-based line range (`start_line`, `end_line`) and optional line numbers. "
        "Strictly jailed to the allowed workspace directory."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path inside workspace (relative to /workspace or prefixed with /workspace/).",
            },
            "start_line": {
                "type": "integer",
                "description": "Optional 1-based start line (inclusive).",
                "minimum": 1,
            },
            "end_line": {
                "type": "integer",
                "description": "Optional 1-based end line (inclusive).",
                "minimum": 1,
            },
            "show_line_numbers": {
                "type": "boolean",
                "description": "Prefix each returned line with its 1-based line number.",
                "default": False,
            },
        },
        "required": ["path"],
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.root_dir: str = str(cfg.get("root_dir") or "").strip()
        self.max_file_size_kb: int = max(1, min(int(cfg.get("max_file_size_kb") or 512), 10240))
        self.allowed_extensions: list[str] = normalize_extensions(cfg.get("allowed_extensions"))

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        path = str(arguments.get("path", "")).strip()
        if not path:
            return ToolResult(success=False, error="Parameter 'path' is required.")
        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line")
        show_line_numbers = bool(arguments.get("show_line_numbers", False))
        s_line = int(start_line) if start_line is not None else None
        e_line = int(end_line) if end_line is not None else None

        try:
            # 1. Explicit configured root_dir or disk-backed SimulatedSandbox
            jailed_root: Path | None = None
            virtual_cwd = "/workspace"
            if self.root_dir:
                jailed_root = Path(self.root_dir).expanduser().resolve()
            elif isinstance(ctx.sandbox, SimulatedSandbox) and ctx.sandbox.workspace_host_dir:
                jailed_root = ctx.sandbox.workspace_host_dir
                virtual_cwd = ctx.sandbox.cwd

            if jailed_root is not None:
                host_path, virt_path = resolve_jailed_path(
                    path, jailed_root, virtual_cwd=virtual_cwd
                )
                content, meta = read_jailed_text_file(
                    host_path,
                    start_line=s_line,
                    end_line=e_line,
                    show_line_numbers=show_line_numbers,
                    max_file_size_kb=self.max_file_size_kb,
                    allowed_extensions=self.allowed_extensions,
                )
                return ToolResult(
                    success=True,
                    output=content,
                    metadata={"path": virt_path, **meta},
                )

            # 2. Docker sandbox or pure in-memory SimulatedSandbox
            validate_extension(Path(posixpath.basename(path.replace("\\", "/"))), self.allowed_extensions)
            content = await ctx.sandbox.read_file(
                path,
                start_line=s_line,
                end_line=e_line,
            )
            if show_line_numbers and content:
                first_no = s_line or 1
                content = "\n".join(
                    f"{idx}: {line}"
                    for idx, line in enumerate(content.splitlines(), start=first_no)
                )
            return ToolResult(
                success=True,
                output=content,
                metadata={"path": path, "start_line": s_line, "end_line": e_line},
            )
        except (PermissionError, FileNotFoundError, IsADirectoryError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))


class WriteFileTool(BaseTool):
    name = "write_file"
    description = (
        "Create, overwrite, or append a UTF-8 text file (code, scripts, notes, configs) "
        "inside the workspace (or configured allowed root_dir) cleanly without shell-escaping "
        "issues. Strictly jailed to the allowed workspace directory."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Target path inside workspace (relative to /workspace or prefixed with /workspace/).",
            },
            "content": {
                "type": "string",
                "description": "Text content to write or append.",
            },
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "description": "Write mode: 'overwrite' (default) replaces the file; 'append' appends to the end.",
                "default": "overwrite",
            },
        },
        "required": ["path", "content"],
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.root_dir: str = str(cfg.get("root_dir") or "").strip()
        self.max_file_size_kb: int = max(1, min(int(cfg.get("max_file_size_kb") or 512), 10240))
        self.allow_overwrite: bool = bool(cfg.get("allow_overwrite", True))
        self.allowed_extensions: list[str] = normalize_extensions(cfg.get("allowed_extensions"))

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        path = str(arguments.get("path", "")).strip()
        content = str(arguments.get("content", ""))
        mode = str(arguments.get("mode", "overwrite") or "overwrite").strip().lower()
        if not path:
            return ToolResult(success=False, error="Parameter 'path' is required.")
        if mode not in ("overwrite", "append"):
            return ToolResult(
                success=False, error="Parameter 'mode' must be 'overwrite' or 'append'."
            )

        try:
            # 1. Explicit configured root_dir or disk-backed SimulatedSandbox
            jailed_root: Path | None = None
            virtual_cwd = "/workspace"
            if self.root_dir:
                jailed_root = Path(self.root_dir).expanduser().resolve()
            elif isinstance(ctx.sandbox, SimulatedSandbox) and ctx.sandbox.workspace_host_dir:
                jailed_root = ctx.sandbox.workspace_host_dir
                virtual_cwd = ctx.sandbox.cwd

            if jailed_root is not None:
                host_path, virt_path = resolve_jailed_path(
                    path, jailed_root, virtual_cwd=virtual_cwd
                )
                meta = write_jailed_text_file(
                    host_path,
                    content,
                    mode=mode,
                    allow_overwrite=self.allow_overwrite,
                    max_file_size_kb=self.max_file_size_kb,
                    allowed_extensions=self.allowed_extensions,
                )
                verb = "appended" if mode == "append" else "wrote"
                return ToolResult(
                    success=True,
                    output=(
                        f"Successfully {verb} {meta['bytes_written']} bytes to {virt_path} "
                        f"(total {meta['total_bytes']} bytes)."
                    ),
                    metadata={"path": virt_path, **meta},
                )

            # 2. Docker sandbox or pure in-memory SimulatedSandbox
            validate_extension(Path(posixpath.basename(path.replace("\\", "/"))), self.allowed_extensions)
            raw_bytes = content.encode("utf-8")
            validate_file_size(len(raw_bytes), self.max_file_size_kb)

            final_content = content
            if mode == "append":
                try:
                    existing = await ctx.sandbox.read_file(path)
                    final_content = existing + content
                    validate_file_size(len(final_content.encode("utf-8")), self.max_file_size_kb)
                except FileNotFoundError:
                    final_content = content
            elif not self.allow_overwrite:
                try:
                    await ctx.sandbox.read_file(path)
                    return ToolResult(
                        success=False,
                        error=(
                            f"Overwriting existing file '{path}' is disabled by tool policy "
                            "(allow_overwrite=false)."
                        ),
                    )
                except FileNotFoundError:
                    pass

            written_path = await ctx.sandbox.write_file(path, final_content)
            verb = "appended" if mode == "append" else "wrote"
            return ToolResult(
                success=True,
                output=f"Successfully {verb} {len(raw_bytes)} bytes to {written_path}.",
                metadata={
                    "path": written_path,
                    "mode": mode,
                    "bytes_written": len(raw_bytes),
                    "total_bytes": len(final_content.encode("utf-8")),
                },
            )
        except (PermissionError, FileExistsError, IsADirectoryError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))


class PythonExecTool(BaseTool):
    name = "python_exec"
    description = (
        "Write and execute a Python 3 script inside the sandbox workspace. "
        "Useful for data processing, automation, mathematical computation, and prototyping."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python 3 source code to run.",
            },
            "filename": {
                "type": "string",
                "description": "Optional script filename under /workspace (default auto-generated).",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in seconds (default 30, max 180).",
                "minimum": 1,
                "maximum": 180,
            },
        },
        "required": ["code"],
    }

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        code = str(arguments.get("code", ""))
        if not code.strip():
            return ToolResult(success=False, error="Parameter 'code' must be non-empty.")
        filename = str(arguments.get("filename") or f"/workspace/_solve_{uuid.uuid4().hex[:8]}.py")
        timeout = arguments.get("timeout")

        script_path = await ctx.sandbox.write_file(filename, code)
        res = await ctx.sandbox.exec_command(
            f"python3 {shlex.quote(script_path)}",
            timeout=int(timeout) if timeout is not None else None,
            window_name=ctx.window_name,
        )
        header = f"[python3 {script_path} | exit_code={res.exit_code} | {res.duration_ms}ms]"
        return ToolResult(
            success=(res.exit_code == 0 and not res.timed_out),
            output=f"{header}\n{res.output or '(no output)'}",
            metadata={
                "script_path": script_path,
                "exit_code": res.exit_code,
                "timed_out": res.timed_out,
                "truncated": res.truncated,
                "full_log_path": res.full_log_path,
            },
        )


class HttpRequestTool(BaseTool):
    name = "http_request"
    description = (
        "Send a structured HTTP request from inside the sandbox container (so container-reachable "
        "targets and cookies work seamlessly) and inspect status, headers, and response body."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
                "default": "GET",
            },
            "url": {"type": "string", "description": "Target HTTP/HTTPS URL."},
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional HTTP headers.",
            },
            "body": {
                "type": "string",
                "description": "Optional raw request body (or JSON string).",
            },
            "follow_redirects": {
                "type": "boolean",
                "default": False,
            },
            "timeout": {
                "type": "integer",
                "default": 20,
                "minimum": 1,
                "maximum": 120,
            },
        },
        "required": ["url"],
    }

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        url = str(arguments.get("url", "")).strip()
        if not url:
            return ToolResult(success=False, error="Parameter 'url' is required.")
        method = str(arguments.get("method", "GET")).upper()
        headers = arguments.get("headers") or {}
        body = arguments.get("body")
        follow_redirects = bool(arguments.get("follow_redirects", False))
        timeout = int(arguments.get("timeout", 20))

        payload = {
            "method": method,
            "url": url,
            "headers": headers,
            "body": body,
            "follow_redirects": follow_redirects,
            "timeout": timeout,
        }
        helper_code = (
            "import json, requests\n"
            f"cfg = json.loads({json.dumps(json.dumps(payload))})\n"
            "resp = requests.request(\n"
            "    cfg['method'], cfg['url'],\n"
            "    headers=cfg['headers'] or None,\n"
            "    data=cfg['body'].encode('utf-8') if cfg['body'] is not None else None,\n"
            "    allow_redirects=cfg['follow_redirects'],\n"
            "    timeout=cfg['timeout'],\n"
            "    verify=False,\n"
            ")\n"
            "print(f'HTTP {resp.status_code} {resp.reason}')\n"
            "for k, v in resp.headers.items():\n"
            "    print(f'{k}: {v}')\n"
            "print()\n"
            "print(resp.text)\n"
        )
        script_path = await ctx.sandbox.write_file(
            f"/tmp/agan_cmds/http_{uuid.uuid4().hex[:8]}.py", helper_code
        )
        res = await ctx.sandbox.exec_command(
            f"python3 {shlex.quote(script_path)} && rm -f {shlex.quote(script_path)}",
            timeout=timeout + 5,
            window_name=ctx.window_name,
        )
        return ToolResult(
            success=(res.exit_code == 0 and not res.timed_out),
            output=res.output or "(empty response)",
            metadata={
                "method": method,
                "url": url,
                "exit_code": res.exit_code,
                "truncated": res.truncated,
                "full_log_path": res.full_log_path,
            },
        )


def _resolve_tool_jailed_root(
    configured_root_dir: str, ctx: ToolExecutionContext
) -> tuple[Path | None, str]:
    """Return `(host_root_dir, virtual_cwd)` when operating on a host-jailed directory."""
    if configured_root_dir:
        return Path(configured_root_dir).expanduser().resolve(), "/workspace"
    if isinstance(ctx.sandbox, SimulatedSandbox) and ctx.sandbox.workspace_host_dir:
        return ctx.sandbox.workspace_host_dir, ctx.sandbox.cwd
    return None, "/workspace"


class EditFileTool(BaseTool):
    name = "edit_file"
    description = (
        "Apply a targeted exact-string replacement inside an existing UTF-8 text file without "
        "rewriting the entire file. `old_text` must match verbatim including whitespace; if it "
        "appears multiple times, provide more surrounding lines or set `replace_all=true`."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path inside workspace (relative to /workspace or prefixed with /workspace/).",
            },
            "old_text": {
                "type": "string",
                "description": "Exact existing text block to replace (including leading whitespace).",
            },
            "new_text": {
                "type": "string",
                "description": "Replacement text. Pass empty string to delete `old_text`.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence of `old_text` instead of requiring a unique match.",
                "default": False,
            },
        },
        "required": ["path", "old_text", "new_text"],
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.root_dir: str = str(cfg.get("root_dir") or "").strip()
        self.max_file_size_kb: int = max(1, min(int(cfg.get("max_file_size_kb") or 512), 10240))
        self.allowed_extensions: list[str] = normalize_extensions(cfg.get("allowed_extensions"))

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        path = str(arguments.get("path", "")).strip()
        old_text = str(arguments.get("old_text", ""))
        new_text = str(arguments.get("new_text", ""))
        replace_all = bool(arguments.get("replace_all", False))
        if not path:
            return ToolResult(success=False, error="Parameter 'path' is required.")
        if not old_text:
            return ToolResult(success=False, error="Parameter 'old_text' must be non-empty.")

        try:
            jailed_root, virtual_cwd = _resolve_tool_jailed_root(self.root_dir, ctx)
            if jailed_root is not None:
                host_path, virt_path = resolve_jailed_path(
                    path, jailed_root, virtual_cwd=virtual_cwd
                )
                meta = edit_jailed_text_file(
                    host_path,
                    old_text,
                    new_text,
                    replace_all=replace_all,
                    max_file_size_kb=self.max_file_size_kb,
                    allowed_extensions=self.allowed_extensions,
                )
                return ToolResult(
                    success=True,
                    output=(
                        f"Edited {virt_path}: {meta['replacements']} replacement(s) "
                        f"(+{meta['lines_added']} -{meta['lines_removed']} lines)."
                    ),
                    metadata={"path": virt_path, **meta},
                )

            # Docker sandbox or pure in-memory SimulatedSandbox
            validate_extension(Path(posixpath.basename(path.replace("\\", "/"))), self.allowed_extensions)
            original = await ctx.sandbox.read_file(path)
            occurrences = original.count(old_text)
            if occurrences == 0:
                return ToolResult(
                    success=False,
                    error=(
                        f"Target `old_text` was not found in '{path}'. "
                        "Read the file first to verify exact whitespace and indentation."
                    ),
                )
            if occurrences > 1 and not replace_all:
                return ToolResult(
                    success=False,
                    error=(
                        f"Target `old_text` matched {occurrences} times in '{path}'. "
                        "Provide more surrounding context or pass `replace_all=true`."
                    ),
                )
            updated = (
                original.replace(old_text, new_text)
                if replace_all
                else original.replace(old_text, new_text, 1)
            )
            replacements = occurrences if replace_all else 1
            updated_bytes = updated.encode("utf-8")
            validate_file_size(len(updated_bytes), self.max_file_size_kb)
            written_path = await ctx.sandbox.write_file(path, updated)

            old_line_cnt = len(old_text.splitlines()) or 1
            new_line_cnt = len(new_text.splitlines()) if new_text else 0
            lines_added = new_line_cnt * replacements
            lines_removed = old_line_cnt * replacements
            return ToolResult(
                success=True,
                output=(
                    f"Edited {written_path}: {replacements} replacement(s) "
                    f"(+{lines_added} -{lines_removed} lines)."
                ),
                metadata={
                    "path": written_path,
                    "replacements": replacements,
                    "lines_added": lines_added,
                    "lines_removed": lines_removed,
                    "total_bytes": len(updated_bytes),
                    "total_lines": len(updated.splitlines()),
                },
            )
        except (PermissionError, FileNotFoundError, IsADirectoryError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))


class ListFilesTool(BaseTool):
    name = "list_files"
    description = (
        "List files and directories inside the workspace (or configured root_dir) with optional "
        "glob filtering (e.g. '*.py', '**/*.md'). Automatically skips .git, node_modules, and caches."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path inside workspace to inspect (default '.').",
                "default": ".",
            },
            "pattern": {
                "type": "string",
                "description": "Optional glob pattern (e.g. '*.py', '**/*.ts', '*config*').",
                "default": "*",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum directory traversal depth (default 4, max 10).",
                "minimum": 1,
                "maximum": 10,
                "default": 4,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum entries to return (default 100, max 500).",
                "minimum": 1,
                "maximum": 500,
                "default": 100,
            },
        },
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.root_dir: str = str(cfg.get("root_dir") or "").strip()

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        raw_path = str(arguments.get("path") or ".").strip() or "."
        pattern = str(arguments.get("pattern") or "*").strip() or "*"
        max_depth = max(1, min(int(arguments.get("max_depth") or 4), 10))
        max_results = max(1, min(int(arguments.get("max_results") or 100), 500))

        try:
            jailed_root, virtual_cwd = _resolve_tool_jailed_root(self.root_dir, ctx)
            if jailed_root is not None:
                base_host, base_virt = resolve_jailed_path(
                    raw_path, jailed_root, virtual_cwd=virtual_cwd
                )
                res = list_jailed_files(
                    base_host,
                    jailed_root,
                    pattern=pattern,
                    max_depth=max_depth,
                    max_results=max_results,
                )
                return self._format_result(base_virt, pattern, res)

            # In-memory SimulatedSandbox
            if isinstance(ctx.sandbox, SimulatedSandbox):
                base_virt = ctx.sandbox._resolve(raw_path).rstrip("/") or "/workspace"
                entries: list[dict[str, Any]] = []
                prefix = base_virt + "/"
                for fp in sorted(ctx.sandbox.files.keys()):
                    if not fp.startswith(prefix):
                        continue
                    rel = fp[len(prefix) :]
                    if rel.count("/") >= max_depth:
                        continue
                    fn = posixpath.basename(fp)
                    if not _match_glob(rel, fn, pattern):
                        continue
                    entries.append(
                        {
                            "path": fp,
                            "rel_path": rel,
                            "kind": "file",
                            "size": len(ctx.sandbox.files[fp].encode("utf-8")),
                        }
                    )
                    if len(entries) >= max_results:
                        break
                return self._format_result(
                    base_virt,
                    pattern,
                    {"entries": entries, "count": len(entries), "truncated": len(entries) >= max_results},
                )

            # Docker sandbox fallback via Python
            payload = json.dumps(
                {
                    "path": raw_path,
                    "pattern": pattern,
                    "max_depth": max_depth,
                    "max_results": max_results,
                }
            )
            helper = (
                "import fnmatch, json, os, posixpath\n"
                f"cfg = json.loads({json.dumps(payload)})\n"
                "IGNORE = {'.git','node_modules','__pycache__','.pytest_cache','.venv','venv','dist'}\n"
                "base = os.path.abspath(cfg['path'] if os.path.isabs(cfg['path']) else os.path.join('/workspace', cfg['path']))\n"
                "entries = []\n"
                "trunc = False\n"
                "pat = cfg['pattern']\n"
                "for dp, dns, fns in os.walk(base):\n"
                "    rel_d = os.path.relpath(dp, base)\n"
                "    depth = 0 if rel_d == '.' else len(rel_d.split(os.sep))\n"
                "    dns[:] = sorted(d for d in dns if d not in IGNORE and not d.startswith('.'))\n"
                "    if depth >= cfg['max_depth']: dns[:] = []\n"
                "    for fn in sorted(fns):\n"
                "        if fn.startswith('.'): continue\n"
                "        full = os.path.join(dp, fn)\n"
                "        rel = os.path.relpath(full, base).replace(os.sep, '/')\n"
                "        if pat in ('*', '**', '**/*') or fnmatch.fnmatch(fn, pat) or fnmatch.fnmatch(rel, pat):\n"
                "            try: sz = os.path.getsize(full)\n"
                "            except OSError: sz = 0\n"
                "            entries.append({'path': full, 'rel_path': rel, 'kind': 'file', 'size': sz})\n"
                "            if len(entries) >= cfg['max_results']: trunc = True; break\n"
                "    if trunc: break\n"
                "print(json.dumps({'entries': entries, 'count': len(entries), 'truncated': trunc}))\n"
            )
            script_path = await ctx.sandbox.write_file(
                f"/tmp/agan_cmds/list_{uuid.uuid4().hex[:8]}.py", helper
            )
            cmd_res = await ctx.sandbox.exec_command(
                f"python3 {shlex.quote(script_path)} && rm -f {shlex.quote(script_path)}",
                timeout=15,
                window_name=ctx.window_name,
            )
            if cmd_res.exit_code != 0:
                return ToolResult(success=False, error=cmd_res.output or "Failed to list files.")
            data = json.loads(cmd_res.output.strip().splitlines()[-1])
            return self._format_result(raw_path, pattern, data)
        except (PermissionError, FileNotFoundError, NotADirectoryError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))

    @staticmethod
    def _format_result(base_virt: str, pattern: str, res: dict[str, Any]) -> ToolResult:
        entries = res.get("entries", [])
        if not entries:
            return ToolResult(
                success=True,
                output=f"No files matched `{pattern}` under {base_virt}.",
                metadata={"path": base_virt, "pattern": pattern, "count": 0, "truncated": False},
            )
        lines = [f"Directory: {base_virt} (pattern: `{pattern}`, {len(entries)} item(s))"]
        for item in entries:
            if item["kind"] == "dir":
                lines.append(f"[dir]  {item['rel_path']}/")
            else:
                lines.append(f"[file] {item['rel_path']} ({item['size']} B)")
        if res.get("truncated"):
            lines.append("... [results truncated; narrow `path` or `pattern`]")
        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "path": base_virt,
                "pattern": pattern,
                "count": len(entries),
                "truncated": bool(res.get("truncated")),
            },
        )


class GrepSearchTool(BaseTool):
    name = "grep_search"
    description = (
        "Search UTF-8 text files inside the workspace (or configured root_dir) for a literal "
        "substring or regular expression, returning matching file paths and 1-based line numbers."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Text or regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "File or directory inside workspace to search (default '.').",
                "default": ".",
            },
            "glob": {
                "type": "string",
                "description": "Optional filename glob filter (e.g. '*.py', '*.ts', '*.md').",
            },
            "is_regex": {
                "type": "boolean",
                "description": "Interpret `pattern` as a Python regex instead of a literal string.",
                "default": False,
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Case-sensitive search (default false).",
                "default": False,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum matching lines to return (default 50, max 200).",
                "minimum": 1,
                "maximum": 200,
                "default": 50,
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.root_dir: str = str(cfg.get("root_dir") or "").strip()
        self.max_file_size_kb: int = max(1, min(int(cfg.get("max_file_size_kb") or 512), 10240))

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        pattern = str(arguments.get("pattern", ""))
        raw_path = str(arguments.get("path") or ".").strip() or "."
        file_glob = str(arguments.get("glob") or "").strip() or None
        is_regex = bool(arguments.get("is_regex", False))
        case_sensitive = bool(arguments.get("case_sensitive", False))
        max_results = max(1, min(int(arguments.get("max_results") or 50), 200))

        if not pattern:
            return ToolResult(success=False, error="Parameter 'pattern' must be non-empty.")

        try:
            jailed_root, virtual_cwd = _resolve_tool_jailed_root(self.root_dir, ctx)
            if jailed_root is not None:
                target_host, target_virt = resolve_jailed_path(
                    raw_path, jailed_root, virtual_cwd=virtual_cwd
                )
                res = grep_jailed_files(
                    target_host,
                    jailed_root,
                    pattern=pattern,
                    file_glob=file_glob,
                    is_regex=is_regex,
                    case_sensitive=case_sensitive,
                    max_results=max_results,
                    max_file_size_kb=self.max_file_size_kb,
                )
                return self._format_result(target_virt, pattern, res)

            # Pure in-memory SimulatedSandbox
            if isinstance(ctx.sandbox, SimulatedSandbox):
                flags = 0 if case_sensitive else re.IGNORECASE
                try:
                    compiled = re.compile(pattern if is_regex else re.escape(pattern), flags)
                except re.error as exc:
                    return ToolResult(success=False, error=f"Invalid regex '{pattern}': {exc}")
                base_virt = ctx.sandbox._resolve(raw_path).rstrip("/") or "/workspace"
                matches: list[dict[str, Any]] = []
                matched_files: set[str] = set()
                files_searched = 0
                for fp in sorted(ctx.sandbox.files.keys()):
                    if fp != base_virt and not fp.startswith(base_virt + "/"):
                        continue
                    rel = fp[len("/workspace/") :] if fp.startswith("/workspace/") else fp
                    if file_glob and not _match_glob(rel, posixpath.basename(fp), file_glob):
                        continue
                    files_searched += 1
                    for ln, line in enumerate(ctx.sandbox.files[fp].splitlines(), start=1):
                        if compiled.search(line):
                            matches.append(
                                {
                                    "path": fp,
                                    "rel_path": rel,
                                    "line": ln,
                                    "text": line[:300],
                                }
                            )
                            matched_files.add(fp)
                            if len(matches) >= max_results:
                                break
                    if len(matches) >= max_results:
                        break
                return self._format_result(
                    base_virt,
                    pattern,
                    {
                        "matches": matches,
                        "match_count": len(matches),
                        "files_searched": files_searched,
                        "files_matched": len(matched_files),
                        "truncated": len(matches) >= max_results,
                    },
                )

            # Docker sandbox fallback via Python
            payload = json.dumps(
                {
                    "path": raw_path,
                    "pattern": pattern,
                    "glob": file_glob,
                    "is_regex": is_regex,
                    "case_sensitive": case_sensitive,
                    "max_results": max_results,
                    "max_bytes": self.max_file_size_kb * 1024,
                }
            )
            helper = (
                "import fnmatch, json, os, re\n"
                f"cfg = json.loads({json.dumps(payload)})\n"
                "IGNORE = {'.git','node_modules','__pycache__','.pytest_cache','.venv','venv','dist'}\n"
                "flags = 0 if cfg['case_sensitive'] else re.IGNORECASE\n"
                "rx = re.compile(cfg['pattern'] if cfg['is_regex'] else re.escape(cfg['pattern']), flags)\n"
                "target = os.path.abspath(cfg['path'] if os.path.isabs(cfg['path']) else os.path.join('/workspace', cfg['path']))\n"
                "files = [target] if os.path.isfile(target) else []\n"
                "if os.path.isdir(target):\n"
                "    for dp, dns, fns in os.walk(target):\n"
                "        dns[:] = sorted(d for d in dns if d not in IGNORE and not d.startswith('.'))\n"
                "        for fn in sorted(fns):\n"
                "            if fn.startswith('.'): continue\n"
                "            if cfg['glob'] and not fnmatch.fnmatch(fn, cfg['glob']): continue\n"
                "            files.append(os.path.join(dp, fn))\n"
                "matches, matched, searched, trunc = [], set(), 0, False\n"
                "for fp in files:\n"
                "    try:\n"
                "        if os.path.getsize(fp) > cfg['max_bytes']: continue\n"
                "        raw = open(fp, 'rb').read()\n"
                "    except OSError: continue\n"
                "    if b'\\x00' in raw[:4096]: continue\n"
                "    searched += 1\n"
                "    rel = os.path.relpath(fp, '/workspace').replace(os.sep, '/')\n"
                "    for i, line in enumerate(raw.decode('utf-8', 'replace').splitlines(), 1):\n"
                "        if rx.search(line):\n"
                "            matches.append({'path': fp, 'rel_path': rel, 'line': i, 'text': line[:300]})\n"
                "            matched.add(fp)\n"
                "            if len(matches) >= cfg['max_results']: trunc = True; break\n"
                "    if trunc: break\n"
                "print(json.dumps({'matches': matches, 'match_count': len(matches), 'files_searched': searched, 'files_matched': len(matched), 'truncated': trunc}))\n"
            )
            script_path = await ctx.sandbox.write_file(
                f"/tmp/agan_cmds/grep_{uuid.uuid4().hex[:8]}.py", helper
            )
            cmd_res = await ctx.sandbox.exec_command(
                f"python3 {shlex.quote(script_path)} && rm -f {shlex.quote(script_path)}",
                timeout=20,
                window_name=ctx.window_name,
            )
            if cmd_res.exit_code != 0:
                return ToolResult(success=False, error=cmd_res.output or "Grep search failed.")
            data = json.loads(cmd_res.output.strip().splitlines()[-1])
            return self._format_result(raw_path, pattern, data)
        except (PermissionError, FileNotFoundError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))

    @staticmethod
    def _format_result(target_virt: str, pattern: str, res: dict[str, Any]) -> ToolResult:
        matches = res.get("matches", [])
        if not matches:
            return ToolResult(
                success=True,
                output=(
                    f"No matches found for `{pattern}` in {target_virt} "
                    f"({res.get('files_searched', 0)} file(s) searched)."
                ),
                metadata={
                    "path": target_virt,
                    "pattern": pattern,
                    "match_count": 0,
                    "files_searched": res.get("files_searched", 0),
                    "files_matched": 0,
                    "truncated": False,
                },
            )
        lines = [
            f"Found {res['match_count']} match(es) across {res['files_matched']} file(s) for `{pattern}`:"
        ]
        for m in matches:
            lines.append(f"{m['rel_path']}:{m['line']}: {m['text']}")
        if res.get("truncated"):
            lines.append("... [results truncated; narrow `path` or `glob`]")
        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={
                "path": target_virt,
                "pattern": pattern,
                "match_count": res["match_count"],
                "files_searched": res.get("files_searched", 0),
                "files_matched": res["files_matched"],
                "truncated": bool(res.get("truncated")),
            },
        )
