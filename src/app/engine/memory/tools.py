from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine.memory.store import (
    build_memory_prompt_block,
    delete_memory_file,
    edit_memory_file,
    list_memory_files,
    read_memory_file,
    resolve_scope_dir,
    search_memory_files,
    write_memory_file,
)
from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult
from app.engine.tools.registry import ToolRegistry


@dataclass
class MemoryRuntimeContext:
    memories_root: Path
    agent_id: str
    agent_name: str
    shared_dir: Path | None = None
    include_global: bool = True
    memory_mode: str = "read_write"  # "read_write" | "read_only"

    def resolve_dir(self, scope: str, *, create: bool = False) -> Path:
        clean = str(scope or "").strip().lower()
        if clean == "global" and not self.include_global:
            raise PermissionError("Global memory access is disabled for this Agent.")
        return resolve_scope_dir(
            self.memories_root,
            clean,
            scope_id=self.agent_id if clean == "agent" else None,
            shared_dir=self.shared_dir if clean == "shared" else None,
            create=create,
        )

    def scope_label(self, scope: str) -> str:
        clean = str(scope or "").strip().lower()
        if clean == "global":
            return "Global Memory"
        if clean == "shared":
            return "Shared Task Blackboard"
        return f"Agent Memory ({self.agent_name})"

    def check_write_allowed(self, scope: str) -> None:
        clean = str(scope or "").strip().lower()
        # Shared task blackboard remains writable for multi-agent coordination even when long-term memory is read-only
        if self.memory_mode == "read_only" and clean in ("global", "agent"):
            raise PermissionError(
                f"Writing to '{clean}' memory is disabled for this Agent (memory_mode='read_only'). "
                "You may still write task handoffs to scope='shared'."
            )

    def readable_scopes(self, requested_scope: str = "all") -> list[tuple[str, Path]]:
        req = str(requested_scope or "all").strip().lower()
        pairs: list[tuple[str, Path]] = []
        if req in ("all", "shared") and self.shared_dir is not None:
            pairs.append(("shared", self.shared_dir.resolve()))
        if req in ("all", "agent"):
            pairs.append(("agent", self.resolve_dir("agent", create=False)))
        if req in ("all", "global") and self.include_global:
            pairs.append(("global", self.resolve_dir("global", create=False)))
        return pairs


class MemorySearchTool(BaseTool):
    name = "memory_search"
    requires_sandbox = False
    description = (
        "Search or list Markdown memories across `shared` (multi-agent task blackboard), "
        "`agent` (this agent's long-term memory), and `global` (shared user-wide memory). "
        "Pass an empty query or '*' to list notes in the selected scope(s)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords to search for (or empty/'*' to list available memory notes).",
                "default": "",
            },
            "scope": {
                "type": "string",
                "enum": ["all", "shared", "agent", "global"],
                "description": "Which memory scope to search (default 'all').",
                "default": "all",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return (default 10, max 30).",
                "minimum": 1,
                "maximum": 30,
                "default": 10,
            },
        },
    }

    def __init__(self, mem_ctx: MemoryRuntimeContext) -> None:
        self._mem = mem_ctx

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        scope = str(arguments.get("scope") or "all").strip().lower()
        limit = max(1, min(int(arguments.get("limit") or 10), 30))

        try:
            scope_dirs = self._mem.readable_scopes(scope)
        except (PermissionError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))

        if not query or query == "*":
            lines: list[str] = []
            total = 0
            for sc_name, sdir in scope_dirs:
                notes = list_memory_files(sdir, include_index=False)
                if not notes:
                    continue
                lines.append(f"### Scope: `{sc_name}` ({len(notes)} note(s))")
                for item in notes[:limit]:
                    author_str = f" [by {item.author}]" if item.author else ""
                    lines.append(f"- `{item.path}` — **{item.title}**{author_str}: {item.summary}")
                    total += 1
            if not lines:
                return ToolResult(
                    success=True,
                    output=f"No memory notes found in scope `{scope}`.",
                    metadata={"scope": scope, "count": 0},
                )
            return ToolResult(
                success=True,
                output="\n".join(lines),
                metadata={"scope": scope, "count": total},
            )

        hits = search_memory_files(scope_dirs, query, limit=limit)
        if not hits:
            return ToolResult(
                success=True,
                output=f"No memories matched `{query}` in scope `{scope}`.",
                metadata={"query": query, "scope": scope, "count": 0},
            )

        lines = [f"Found {len(hits)} memory match(es) for `{query}`:"]
        for h in hits:
            author_str = f" · by {h['author']}" if h.get("author") else ""
            lines.append(
                f"- `[{h['scope']}] {h['path']}` — **{h['title']}**{author_str}\n  {h['snippet']}"
            )
        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={"query": query, "scope": scope, "count": len(hits), "matches": hits},
        )


class MemoryReadTool(BaseTool):
    name = "memory_read"
    requires_sandbox = False
    description = (
        "Read the full Markdown content of a specific memory note from `shared`, `agent`, or `global` scope."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative memory filename (e.g. 'findings.md', 'user-preferences.md', or 'MEMORY.md').",
            },
            "scope": {
                "type": "string",
                "enum": ["shared", "agent", "global"],
                "description": "Memory scope to read from (default 'agent').",
                "default": "agent",
            },
        },
        "required": ["path"],
    }

    def __init__(self, mem_ctx: MemoryRuntimeContext) -> None:
        self._mem = mem_ctx

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        path = str(arguments.get("path") or "").strip()
        scope = str(arguments.get("scope") or "agent").strip().lower()
        if not path:
            return ToolResult(success=False, error="Parameter 'path' is required.")

        try:
            sdir = self._mem.resolve_dir(scope, create=False)
            data = read_memory_file(sdir, path)
            return ToolResult(
                success=True,
                output=f"# [{scope}] {data['path']}\n\n{data['content']}",
                metadata={
                    "scope": scope,
                    "path": data["path"],
                    "title": data["title"],
                    "bytes": data["size"],
                },
            )
        except (FileNotFoundError, PermissionError, ValueError, IsADirectoryError) as exc:
            return ToolResult(success=False, error=str(exc))


class MemoryWriteTool(BaseTool):
    name = "memory_write"
    requires_sandbox = False
    description = (
        "Create or overwrite a Markdown memory note and automatically update the scope's `MEMORY.md` index. "
        "Use `scope='shared'` to publish verified findings, artifact paths, or handoffs visible to all "
        "parent/subagent teammates; use `scope='agent'` or `'global'` only for durable long-term knowledge."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Filename inside the scope (e.g. 'findings.md', 'user-preferences.md').",
            },
            "content": {
                "type": "string",
                "description": "Markdown body content to store.",
            },
            "scope": {
                "type": "string",
                "enum": ["shared", "agent", "global"],
                "description": "Target memory scope (`shared` for current multi-agent task, `agent` or `global` for long-term).",
                "default": "agent",
            },
            "title": {
                "type": "string",
                "description": "Optional short human-readable title for frontmatter & index.",
            },
            "memory_type": {
                "type": "string",
                "description": "Optional category tag (e.g. 'preference', 'lesson', 'finding', 'handoff', 'ruled_out').",
                "default": "note",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional short keywords for faster search.",
            },
        },
        "required": ["path", "content"],
    }

    def __init__(self, mem_ctx: MemoryRuntimeContext) -> None:
        self._mem = mem_ctx

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        path = str(arguments.get("path") or "").strip()
        content = str(arguments.get("content") or "")
        scope = str(arguments.get("scope") or "agent").strip().lower()
        title = str(arguments.get("title") or "").strip()
        memory_type = str(arguments.get("memory_type") or "note").strip()
        tags = arguments.get("tags")
        tag_list = [str(x).strip() for x in tags] if isinstance(tags, list) else None

        if not path:
            return ToolResult(success=False, error="Parameter 'path' is required.")
        if not content.strip():
            return ToolResult(success=False, error="Parameter 'content' must be non-empty.")

        try:
            self._mem.check_write_allowed(scope)
            sdir = self._mem.resolve_dir(scope, create=True)
            res = write_memory_file(
                sdir,
                path,
                content,
                title=title,
                memory_type=memory_type,
                tags=tag_list,
                author=self._mem.agent_name,
                scope_label=self._mem.scope_label(scope),
            )
            return ToolResult(
                success=True,
                output=f"Saved memory `[{scope}] {res['path']}` ({res['size']} bytes) and refreshed MEMORY.md.",
                metadata={
                    "scope": scope,
                    "path": res["path"],
                    "title": res["title"],
                    "bytes": res["size"],
                    "lines_written": len(content.splitlines()),
                },
            )
        except (PermissionError, ValueError, IsADirectoryError) as exc:
            return ToolResult(success=False, error=str(exc))


class MemoryEditTool(BaseTool):
    name = "memory_edit"
    requires_sandbox = False
    description = (
        "Incrementally update (`action='edit'`) or remove (`action='delete'`) an existing memory note "
        "in `shared`, `agent`, or `global` scope without overwriting unrelated sections."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["edit", "delete"],
                "description": "Operation: 'edit' (exact string replacement) or 'delete' (remove obsolete note).",
                "default": "edit",
            },
            "path": {
                "type": "string",
                "description": "Relative memory filename (e.g. 'findings.md').",
            },
            "scope": {
                "type": "string",
                "enum": ["shared", "agent", "global"],
                "description": "Memory scope containing the note (default 'agent').",
                "default": "agent",
            },
            "old_text": {
                "type": "string",
                "description": "Exact existing text to replace (required for action='edit').",
            },
            "new_text": {
                "type": "string",
                "description": "Replacement text (required for action='edit').",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence of `old_text` (default false).",
                "default": False,
            },
        },
        "required": ["path"],
    }

    def __init__(self, mem_ctx: MemoryRuntimeContext) -> None:
        self._mem = mem_ctx

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        action = str(arguments.get("action") or "edit").strip().lower()
        path = str(arguments.get("path") or "").strip()
        scope = str(arguments.get("scope") or "agent").strip().lower()

        if action not in ("edit", "delete"):
            return ToolResult(success=False, error="Parameter 'action' must be 'edit' or 'delete'.")
        if not path:
            return ToolResult(success=False, error="Parameter 'path' is required.")

        try:
            self._mem.check_write_allowed(scope)
            sdir = self._mem.resolve_dir(scope, create=False)
            if action == "delete":
                removed = delete_memory_file(sdir, path, scope_label=self._mem.scope_label(scope))
                return ToolResult(
                    success=True,
                    output=f"Deleted memory `[{scope}] {removed}` and refreshed MEMORY.md.",
                    metadata={"action": "delete", "scope": scope, "path": removed},
                )

            old_text = str(arguments.get("old_text") or "")
            new_text = str(arguments.get("new_text") or "")
            replace_all = bool(arguments.get("replace_all", False))
            res = edit_memory_file(
                sdir,
                path,
                old_text,
                new_text,
                replace_all=replace_all,
                scope_label=self._mem.scope_label(scope),
            )
            return ToolResult(
                success=True,
                output=(
                    f"Edited memory `[{scope}] {res['path']}`: {res['replacements']} replacement(s) "
                    f"(+{res['lines_added']} -{res['lines_removed']} lines)."
                ),
                metadata={
                    "action": "edit",
                    "scope": scope,
                    "path": res["path"],
                    "replacements": res["replacements"],
                    "lines_added": res["lines_added"],
                    "lines_removed": res["lines_removed"],
                },
            )
        except (FileNotFoundError, PermissionError, ValueError, IsADirectoryError) as exc:
            return ToolResult(success=False, error=str(exc))


def attach_memory_to_agent_run(
    tool_registry: ToolRegistry,
    *,
    memories_root: Path,
    agent_id: str,
    agent_name: str,
    shared_dir: Path | None,
    memory_config: dict[str, Any] | None = None,
) -> str:
    """Register memory tools onto `tool_registry` and return `<memory_context>` prompt injection."""
    cfg = memory_config or {}
    if cfg.get("enable_long_term_memory", True) is False:
        return ""

    include_global = cfg.get("include_global_memory", True) is not False
    memory_mode = str(cfg.get("memory_mode") or "read_write").strip().lower()
    if memory_mode not in ("read_write", "read_only"):
        memory_mode = "read_write"

    mem_ctx = MemoryRuntimeContext(
        memories_root=Path(memories_root).resolve(),
        agent_id=agent_id,
        agent_name=agent_name,
        shared_dir=Path(shared_dir).resolve() if shared_dir is not None else None,
        include_global=include_global,
        memory_mode=memory_mode,
    )

    tool_registry.register(MemorySearchTool(mem_ctx), enabled=True)
    tool_registry.register(MemoryReadTool(mem_ctx), enabled=True)
    tool_registry.register(MemoryWriteTool(mem_ctx), enabled=True)
    tool_registry.register(MemoryEditTool(mem_ctx), enabled=True)

    global_dir = (
        resolve_scope_dir(mem_ctx.memories_root, "global", create=False) if include_global else None
    )
    agent_dir = resolve_scope_dir(
        mem_ctx.memories_root, "agent", scope_id=agent_id, create=False
    )
    return build_memory_prompt_block(
        global_dir=global_dir,
        agent_dir=agent_dir,
        shared_dir=mem_ctx.shared_dir,
        agent_name=agent_name,
        memory_mode=memory_mode,
    )
