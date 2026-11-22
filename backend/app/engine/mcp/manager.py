from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from app.core.security import mask_secret, secret_box
from app.engine.mcp.client import MCPClientError, MCPServerRuntimeConfig, create_mcp_client
from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult
from app.engine.tools.registry import ToolRegistry
from app.models.mcp import MCPServer


def encrypt_kv_dict(data: dict[str, str] | None) -> str:
    if not data:
        return ""
    clean = {str(k): str(v) for k, v in data.items() if str(k).strip()}
    if not clean:
        return ""
    return secret_box.encrypt(json.dumps(clean, ensure_ascii=False))


def decrypt_kv_dict(encrypted: str | None) -> dict[str, str]:
    if not encrypted:
        return {}
    raw = secret_box.decrypt(encrypted)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except json.JSONDecodeError:
        pass
    return {}


def mask_kv_dict(data: dict[str, str]) -> dict[str, str]:
    return {k: mask_secret(v) for k, v in data.items()}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()).strip("_")
    return slug or "server"


def make_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Create an OpenAI-safe namespaced tool identifier: `mcp__{server}__{tool}` (<=64 chars)."""
    s_slug = _slugify(server_name)[:20]
    t_slug = _slugify(tool_name)[:34]
    candidate = f"mcp__{s_slug}__{t_slug}"
    if len(candidate) <= 64:
        return candidate
    digest = hashlib.sha1(f"{server_name}:{tool_name}".encode()).hexdigest()[:6]
    return f"{candidate[:57]}_{digest}"


def build_runtime_config(server: MCPServer) -> MCPServerRuntimeConfig:
    return MCPServerRuntimeConfig(
        name=server.name,
        transport=server.transport,
        command=server.command or "",
        args=[str(a) for a in (server.args or [])],
        env=decrypt_kv_dict(server.env_encrypted),
        url=server.url or "",
        headers=decrypt_kv_dict(server.headers_encrypted),
        timeout_sec=max(3, min(int(server.timeout_sec or 30), 180)),
    )


def _format_mcp_tool_output(raw_result: dict[str, Any]) -> tuple[bool, str]:
    is_error = bool(raw_result.get("isError", False))
    content_blocks = raw_result.get("content")
    parts: list[str] = []

    if isinstance(content_blocks, list):
        for block in content_blocks:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(str(block.get("text", "")))
            elif btype == "resource":
                res = block.get("resource") or {}
                if isinstance(res, dict) and res.get("text"):
                    parts.append(str(res["text"]))
                else:
                    parts.append(json.dumps(res, ensure_ascii=False))
            elif btype == "image":
                mime = block.get("mimeType", "image/*")
                parts.append(f"[image content: {mime}]")
            else:
                parts.append(json.dumps(block, ensure_ascii=False))
    elif "structuredContent" in raw_result:
        parts.append(json.dumps(raw_result["structuredContent"], ensure_ascii=False, indent=2))
    else:
        parts.append(json.dumps(raw_result, ensure_ascii=False, indent=2))

    rendered = "\n".join(p for p in parts if p).strip() or "(empty)"
    if len(rendered) > 12000:
        rendered = rendered[:12000] + "\n... [MCP output truncated]"
    return (not is_error), rendered


async def discover_mcp_tools(server: MCPServer) -> tuple[list[dict[str, Any]], int]:
    """Connect to an MCP server, run initialize + tools/list, and return (tools, latency_ms)."""
    cfg = build_runtime_config(server)
    start = time.monotonic()
    async with create_mcp_client(cfg) as client:
        await client.initialize()
        tools = await client.list_tools()
    latency_ms = int((time.monotonic() - start) * 1000)
    return tools, latency_ms


async def call_mcp_tool(
    server: MCPServer,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    """Invoke a single tool on an MCP server and normalize the result into ToolResult."""
    cfg = build_runtime_config(server)
    start = time.monotonic()
    try:
        async with create_mcp_client(cfg) as client:
            await client.initialize()
            raw = await client.call_tool(tool_name, arguments)
        duration_ms = int((time.monotonic() - start) * 1000)
        ok, rendered = _format_mcp_tool_output(raw)
        return ToolResult(
            success=ok,
            output=rendered if ok else "",
            error=None if ok else rendered,
            metadata={
                "mcp_server": server.name,
                "mcp_tool": tool_name,
                "duration_ms": duration_ms,
            },
        )
    except MCPClientError as exc:
        return ToolResult(
            success=False,
            output="",
            error=str(exc),
            metadata={"mcp_server": server.name, "mcp_tool": tool_name},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            output="",
            error=f"{type(exc).__name__}: {exc}",
            metadata={"mcp_server": server.name, "mcp_tool": tool_name},
        )


class MCPToolAdapter(BaseTool):
    """Bridges an external MCP server tool into the AGAN BaseTool & OpenAI Function Calling interface."""

    requires_sandbox = False

    def __init__(self, server_cfg: MCPServerRuntimeConfig, tool_spec: dict[str, Any]) -> None:
        self._server_cfg = server_cfg
        self._original_name = str(tool_spec.get("name") or "")
        self.name = make_mcp_tool_name(server_cfg.name, self._original_name)
        raw_desc = str(tool_spec.get("description") or "").strip()
        self.description = (
            f"[MCP:{server_cfg.name}] {raw_desc}"
            if raw_desc
            else f"[MCP:{server_cfg.name}] Invoke MCP tool '{self._original_name}'."
        )
        schema = tool_spec.get("inputSchema")
        self.parameters_schema = (
            schema if isinstance(schema, dict) and schema.get("type") else {"type": "object", "properties": {}}
        )

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        start = time.monotonic()
        try:
            async with create_mcp_client(self._server_cfg) as client:
                await client.initialize()
                raw = await client.call_tool(self._original_name, arguments)
            duration_ms = int((time.monotonic() - start) * 1000)
            ok, rendered = _format_mcp_tool_output(raw)
            return ToolResult(
                success=ok,
                output=rendered if ok else "",
                error=None if ok else rendered,
                metadata={
                    "mcp_server": self._server_cfg.name,
                    "mcp_tool": self._original_name,
                    "duration_ms": duration_ms,
                },
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"MCP({self._server_cfg.name}/{self._original_name}) failed: {exc}",
                metadata={
                    "mcp_server": self._server_cfg.name,
                    "mcp_tool": self._original_name,
                },
            )


async def attach_mcp_tools_to_registry(
    registry: ToolRegistry,
    mcp_servers: list[MCPServer],
    tool_filters: dict[str, list[str] | None] | None = None,
) -> int:
    """Register enabled MCP server tools into `registry` respecting optional per-server allowlists.

    If `tool_filters` has no entry (or `None`) for `server.id`, all discovered tools from that
    server are attached. If it has a list (even empty `[]`), only tools whose original MCP tool
    name is in that list are attached.
    """
    attached = 0
    filters_map = tool_filters or {}
    for server in mcp_servers:
        if not server.is_enabled:
            continue
        allowed_list = filters_map.get(server.id)
        if allowed_list is not None and len(allowed_list) == 0:
            continue
        allowed_set = set(allowed_list) if allowed_list is not None else None

        cfg = build_runtime_config(server)
        specs = list(server.discovered_tools or [])
        if not specs:
            # Best-effort live discovery if the server has never been synced yet
            try:
                quick_cfg = MCPServerRuntimeConfig(
                    name=cfg.name,
                    transport=cfg.transport,
                    command=cfg.command,
                    args=cfg.args,
                    env=cfg.env,
                    url=cfg.url,
                    headers=cfg.headers,
                    timeout_sec=min(cfg.timeout_sec, 10),
                )
                async with create_mcp_client(quick_cfg) as client:
                    await client.initialize()
                    specs = await client.list_tools()
            except Exception:
                specs = []

        for spec in specs:
            if not isinstance(spec, dict) or not spec.get("name"):
                continue
            orig_name = str(spec["name"])
            if allowed_set is not None and orig_name not in allowed_set:
                continue
            adapter = MCPToolAdapter(cfg, spec)
            registry.register(adapter, enabled=True)
            attached += 1
    return attached
