from __future__ import annotations

import json
import re
import shlex
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import mask_secret, secret_box
from app.engine.sandbox.path_guard import normalize_extensions
from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult
from app.engine.tools.builtin.agent_tools import ScratchpadUpdateTool, SubmitFlagTool
from app.engine.tools.builtin.sandbox_tools import (
    BashExecTool,
    EditFileTool,
    GrepSearchTool,
    HttpRequestTool,
    ListFilesTool,
    PythonExecTool,
    ReadFileTool,
    WriteFileTool,
)
from app.engine.tools.builtin.skill_tools import CreateSkillTool, EditSkillFileTool
from app.engine.tools.builtin.web_tools import WebFetchTool, WebSearchTool
from app.engine.tools.registry import ToolRegistry
from app.models.capability import ToolConfig, ToolType

# (tool_instance, display_name, category, default_enabled_on_seed)
BUILTIN_TOOL_DEFS: list[tuple[BaseTool, str, str, bool]] = [
    (ReadFileTool(), "Read File", "filesystem", True),
    (WriteFileTool(), "Write File", "filesystem", True),
    (EditFileTool(), "Edit File", "filesystem", True),
    (ListFilesTool(), "List Files", "filesystem", True),
    (GrepSearchTool(), "Grep Search", "filesystem", True),
    (BashExecTool(), "Persistent Shell", "sandbox", True),
    (PythonExecTool(), "Python 3 Runner", "sandbox", True),
    (WebSearchTool(), "Web Search", "web", True),
    (WebFetchTool(), "Web Fetch", "web", True),
    (HttpRequestTool(), "HTTP Client", "web", True),
    (CreateSkillTool(), "Create Skill", "skill", True),
    (EditSkillFileTool(), "Edit Skill File", "skill", True),
    (ScratchpadUpdateTool(), "Working Scratchpad", "agent", True),
    (SubmitFlagTool(), "Submit CTF Flag", "ctf", False),
]

BUILTIN_TOOL_MAP: dict[str, BaseTool] = {tool.name: tool for tool, _, _, _ in BUILTIN_TOOL_DEFS}

FILE_BUILTIN_TOOL_NAMES: frozenset[str] = frozenset(
    {"read_file", "write_file", "edit_file", "list_files", "grep_search"}
)
WEB_BUILTIN_TOOL_NAMES: frozenset[str] = frozenset({"web_search", "web_fetch"})

DEFAULT_BUILTIN_CONFIGS: dict[str, dict[str, Any]] = {
    "read_file": {
        "root_dir": "",
        "max_file_size_kb": 512,
        "allowed_extensions": [],
    },
    "write_file": {
        "root_dir": "",
        "max_file_size_kb": 512,
        "allow_overwrite": True,
        "allowed_extensions": [],
    },
    "edit_file": {
        "root_dir": "",
        "max_file_size_kb": 512,
        "allowed_extensions": [],
    },
    "list_files": {
        "root_dir": "",
    },
    "grep_search": {
        "root_dir": "",
        "max_file_size_kb": 512,
    },
    "web_search": {
        "provider": "auto",
        "base_url": "",
        "api_key_encrypted": "",
        "default_max_results": 5,
        "timeout": 15,
    },
    "web_fetch": {
        "default_max_chars": 12000,
        "timeout": 20,
    },
}


def sanitize_builtin_file_config(tool_name: str, raw_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize editable security boundary config for file/search built-in tools."""
    defaults = DEFAULT_BUILTIN_CONFIGS.get(tool_name, {})
    incoming = raw_cfg or {}
    root_dir = str(incoming.get("root_dir", defaults.get("root_dir", "")) or "").strip()
    if tool_name == "list_files":
        return {"root_dir": root_dir}

    try:
        max_kb = int(incoming.get("max_file_size_kb", defaults.get("max_file_size_kb", 512)))
    except (TypeError, ValueError):
        max_kb = 512
    max_kb = max(1, min(max_kb, 10240))

    if tool_name == "grep_search":
        return {"root_dir": root_dir, "max_file_size_kb": max_kb}

    allowed_exts = normalize_extensions(
        incoming.get("allowed_extensions", defaults.get("allowed_extensions", []))
    )
    sanitized: dict[str, Any] = {
        "root_dir": root_dir,
        "max_file_size_kb": max_kb,
        "allowed_extensions": allowed_exts,
    }
    if tool_name == "write_file":
        sanitized["allow_overwrite"] = bool(
            incoming.get("allow_overwrite", defaults.get("allow_overwrite", True))
        )
    return sanitized


def sanitize_builtin_web_config(
    tool_name: str,
    raw_cfg: dict[str, Any] | None,
    existing_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and normalize editable provider/timeout config for web_search & web_fetch."""
    defaults = DEFAULT_BUILTIN_CONFIGS.get(tool_name, {})
    incoming = raw_cfg or {}
    prev = existing_cfg or {}

    if tool_name == "web_fetch":
        try:
            max_chars = int(
                incoming.get("default_max_chars", defaults.get("default_max_chars", 12000))
            )
        except (TypeError, ValueError):
            max_chars = 12000
        try:
            timeout = int(incoming.get("timeout", defaults.get("timeout", 20)))
        except (TypeError, ValueError):
            timeout = 20
        return {
            "default_max_chars": max(1000, min(max_chars, 40000)),
            "timeout": max(3, min(timeout, 60)),
        }

    # web_search
    provider = str(incoming.get("provider", prev.get("provider", "auto")) or "auto").strip().lower()
    if provider not in ("auto", "duckduckgo", "tavily", "searxng", "brave"):
        provider = "auto"
    base_url = str(incoming.get("base_url", prev.get("base_url", "")) or "").strip().rstrip("/")
    try:
        max_results = int(
            incoming.get("default_max_results", prev.get("default_max_results", 5))
        )
    except (TypeError, ValueError):
        max_results = 5
    try:
        timeout = int(incoming.get("timeout", prev.get("timeout", 15)))
    except (TypeError, ValueError):
        timeout = 15

    # Encrypt new api_key if supplied, clear if requested, otherwise preserve existing secret
    raw_api_key = str(incoming.get("api_key") or "").strip()
    clear_api_key = bool(incoming.get("clear_api_key", False))
    if clear_api_key:
        enc_key = ""
    elif raw_api_key:
        enc_key = secret_box.encrypt(raw_api_key)
    else:
        enc_key = str(prev.get("api_key_encrypted") or "")

    return {
        "provider": provider,
        "base_url": base_url,
        "api_key_encrypted": enc_key,
        "default_max_results": max(1, min(max_results, 10)),
        "timeout": max(3, min(timeout, 60)),
    }


def present_tool_implementation_config(row: ToolConfig) -> dict[str, Any]:
    """Return a display-safe implementation_config without leaking encrypted secrets."""
    cfg = dict(row.implementation_config or {})
    if row.is_builtin and row.name == "web_search":
        enc = str(cfg.pop("api_key_encrypted", "") or "")
        plain = secret_box.decrypt(enc) if enc else ""
        cfg["has_api_key"] = bool(plain)
        cfg["api_key_masked"] = mask_secret(plain) if plain else ""
    return cfg


def _render_template(template: str, arguments: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        val = arguments.get(key, "")
        return val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)

    return re.sub(r"\{\{\s*([^{}\s]+)\s*\}\}", repl, template)


class CustomHttpTool(BaseTool):
    """User-defined HTTP webhook tool with {{variable}} template interpolation."""

    requires_sandbox = False

    def __init__(self, row: ToolConfig) -> None:
        self.name = row.name
        self.description = row.description
        self.parameters_schema = row.parameters_schema or {
            "type": "object",
            "properties": {},
        }
        self._cfg = row.implementation_config or {}

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        method = str(self._cfg.get("method", "GET")).upper()
        url_tpl = str(self._cfg.get("url", "")).strip()
        if not url_tpl:
            return ToolResult(success=False, error="Custom HTTP tool missing `url` config.")
        url = _render_template(url_tpl, arguments)
        raw_headers = self._cfg.get("headers") or {}
        headers = {str(k): _render_template(str(v), arguments) for k, v in raw_headers.items()}
        body_tpl = self._cfg.get("body_template")
        body = _render_template(str(body_tpl), arguments) if body_tpl else None
        timeout = float(self._cfg.get("timeout", 20))

        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            resp = await client.request(
                method,
                url,
                headers=headers or None,
                content=body.encode("utf-8") if body is not None else None,
            )
            text = resp.text
            if len(text) > 8000:
                text = text[:8000] + "\n... [truncated]"
            return ToolResult(
                success=(200 <= resp.status_code < 400),
                output=f"HTTP {resp.status_code}\n{text}",
                metadata={"status_code": resp.status_code, "url": url},
            )


class CustomPythonTool(BaseTool):
    """User-defined Python snippet tool executed inside the session sandbox."""

    requires_sandbox = True

    def __init__(self, row: ToolConfig) -> None:
        self.name = row.name
        self.description = row.description
        self.parameters_schema = row.parameters_schema or {
            "type": "object",
            "properties": {},
        }
        self._cfg = row.implementation_config or {}

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        user_code = str(self._cfg.get("code", "")).strip()
        if not user_code:
            return ToolResult(success=False, error="Custom Python tool missing `code` config.")
        timeout = int(self._cfg.get("timeout", 30))
        wrapper = (
            "import json\n"
            f"ARGS = json.loads({json.dumps(json.dumps(arguments, ensure_ascii=False))})\n"
            f"{user_code}\n"
        )
        script_path = await ctx.sandbox.write_file(
            f"/tmp/agan_cmds/custom_{uuid.uuid4().hex[:8]}.py", wrapper
        )
        res = await ctx.sandbox.exec_command(
            f"python3 {shlex.quote(script_path)} && rm -f {shlex.quote(script_path)}",
            timeout=timeout,
            window_name=ctx.window_name,
        )
        return ToolResult(
            success=(res.exit_code == 0 and not res.timed_out),
            output=res.output or "(no output)",
            metadata={"exit_code": res.exit_code, "duration_ms": res.duration_ms},
        )


async def seed_builtin_tools(db: AsyncSession) -> None:
    """Idempotently ensure all built-in tools exist and stay schema-synced in `tools`."""
    existing_rows = {
        row.name: row for row in (await db.scalars(select(ToolConfig))).all()
    }
    for tool_inst, display_name, category, default_enabled in BUILTIN_TOOL_DEFS:
        default_cfg = dict(DEFAULT_BUILTIN_CONFIGS.get(tool_inst.name, {}))
        existing = existing_rows.get(tool_inst.name)
        if existing is None:
            db.add(
                ToolConfig(
                    name=tool_inst.name,
                    display_name=display_name,
                    description=tool_inst.description,
                    tool_type=ToolType.BUILTIN.value,
                    category=category,
                    parameters_schema=tool_inst.parameters_schema,
                    implementation_config=default_cfg,
                    is_enabled=default_enabled,
                    requires_sandbox=tool_inst.requires_sandbox,
                    is_builtin=True,
                )
            )
        elif existing.is_builtin:
            # Keep built-in schema up-to-date and backfill any missing default config keys
            if existing.parameters_schema != tool_inst.parameters_schema:
                existing.parameters_schema = tool_inst.parameters_schema
            if existing.requires_sandbox != tool_inst.requires_sandbox:
                existing.requires_sandbox = tool_inst.requires_sandbox
            if default_cfg:
                merged_cfg = {**default_cfg, **(existing.implementation_config or {})}
                if merged_cfg != (existing.implementation_config or {}):
                    existing.implementation_config = merged_cfg
    await db.flush()


_CONFIGURABLE_BUILTIN_CLASSES: dict[str, type[BaseTool]] = {
    "read_file": ReadFileTool,
    "write_file": WriteFileTool,
    "edit_file": EditFileTool,
    "list_files": ListFilesTool,
    "grep_search": GrepSearchTool,
    "web_search": WebSearchTool,
    "web_fetch": WebFetchTool,
}


def instantiate_tool_from_row(row: ToolConfig) -> BaseTool | None:
    if row.tool_type == ToolType.BUILTIN.value:
        cls = _CONFIGURABLE_BUILTIN_CLASSES.get(row.name)
        if cls is not None:
            inst: BaseTool = cls(config=row.implementation_config)  # type: ignore[call-arg]
            if row.description:
                inst.description = row.description
            return inst
        return BUILTIN_TOOL_MAP.get(row.name)
    if row.tool_type == ToolType.CUSTOM_HTTP.value:
        return CustomHttpTool(row)
    if row.tool_type == ToolType.CUSTOM_PYTHON.value:
        return CustomPythonTool(row)
    return None


async def build_tool_registry_from_db(
    db: AsyncSession,
    *,
    tool_ids: list[str] | None = None,
) -> ToolRegistry:
    """Hot-build a ToolRegistry from enabled DB tool rows (optionally filtered by IDs)."""
    stmt = select(ToolConfig).where(ToolConfig.is_enabled.is_(True))
    if tool_ids is not None:
        if not tool_ids:
            return ToolRegistry()
        stmt = stmt.where(ToolConfig.id.in_(tool_ids))

    rows = list((await db.scalars(stmt)).all())
    registry = ToolRegistry()
    for row in rows:
        inst = instantiate_tool_from_row(row)
        if inst is not None:
            registry.register(inst, enabled=True)
    return registry
