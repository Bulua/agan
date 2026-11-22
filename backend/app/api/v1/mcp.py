from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.engine.mcp.manager import (
    call_mcp_tool,
    decrypt_kv_dict,
    discover_mcp_tools,
    encrypt_kv_dict,
    mask_kv_dict,
)
from app.models.mcp import MCPServer, MCPTransport
from app.schemas.mcp import (
    MCPDiscoveredTool,
    MCPImportRequest,
    MCPImportResponse,
    MCPServerCreate,
    MCPServerListResponse,
    MCPServerResponse,
    MCPServerUpdate,
    MCPToolCallTestRequest,
    MCPToolCallTestResponse,
)

router = APIRouter(prefix="/mcp", tags=["MCP Servers"])


def _to_response(row: MCPServer) -> MCPServerResponse:
    env_plain = decrypt_kv_dict(row.env_encrypted)
    headers_plain = decrypt_kv_dict(row.headers_encrypted)
    tools = [
        MCPDiscoveredTool(
            name=str(t.get("name", "")),
            description=str(t.get("description", "")),
            inputSchema=t.get("inputSchema") or {},
        )
        for t in (row.discovered_tools or [])
        if isinstance(t, dict) and t.get("name")
    ]
    return MCPServerResponse(
        id=row.id,
        name=row.name,
        description=row.description,
        transport=MCPTransport(row.transport),
        command=row.command,
        args=list(row.args or []),
        env_masked=mask_kv_dict(env_plain),
        has_env=bool(env_plain),
        url=row.url,
        headers_masked=mask_kv_dict(headers_plain),
        has_headers=bool(headers_plain),
        timeout_sec=row.timeout_sec,
        is_enabled=row.is_enabled,
        last_status=row.last_status,
        last_error=row.last_error,
        last_latency_ms=row.last_latency_ms,
        last_synced_at=row.last_synced_at,
        discovered_tools=tools,
        tool_count=len(tools),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_transport_fields(transport: MCPTransport, command: str, url: str) -> None:
    if transport == MCPTransport.STDIO and not command.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stdio MCP server requires a non-empty `command` (e.g. npx, uvx, python).",
        )
    if transport in (MCPTransport.STREAMABLE_HTTP, MCPTransport.SSE) and not url.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{transport.value} MCP server requires a non-empty `url`.",
        )


@router.get("", response_model=MCPServerListResponse)
async def list_mcp_servers(
    transport: MCPTransport | None = Query(default=None),
    is_enabled: bool | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> MCPServerListResponse:
    t_rows = (
        await db.execute(
            select(MCPServer.transport, func.count(MCPServer.id)).group_by(MCPServer.transport)
        )
    ).all()
    transport_counts = {
        "all": 0,
        "stdio": 0,
        "streamable_http": 0,
        "sse": 0,
        "enabled": 0,
    }
    for t_val, cnt in t_rows:
        transport_counts[str(t_val)] = int(cnt)
        transport_counts["all"] += int(cnt)
    enabled_cnt = await db.scalar(
        select(func.count(MCPServer.id)).where(MCPServer.is_enabled.is_(True))
    )
    transport_counts["enabled"] = int(enabled_cnt or 0)

    filters = []
    if transport is not None:
        filters.append(MCPServer.transport == transport.value)
    if is_enabled is not None:
        filters.append(MCPServer.is_enabled == is_enabled)
    if search and search.strip():
        pat = f"%{search.strip()}%"
        filters.append(
            or_(
                MCPServer.name.ilike(pat),
                MCPServer.description.ilike(pat),
                MCPServer.command.ilike(pat),
                MCPServer.url.ilike(pat),
            )
        )

    count_stmt = select(func.count(MCPServer.id))
    data_stmt = select(MCPServer).order_by(MCPServer.created_at.desc())
    if filters:
        count_stmt = count_stmt.where(*filters)
        data_stmt = data_stmt.where(*filters)

    total = int((await db.scalar(count_stmt)) or 0)
    rows = list(
        (await db.execute(data_stmt.offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )
    return MCPServerListResponse.create(
        items=[_to_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        transport_counts=transport_counts,
    )


@router.post("", response_model=MCPServerResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    payload: MCPServerCreate,
    db: AsyncSession = Depends(get_db),
) -> MCPServerResponse:
    name_clean = payload.name.strip()
    dup = await db.scalar(select(MCPServer).where(MCPServer.name == name_clean))
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An MCP server named '{name_clean}' already exists.",
        )

    _validate_transport_fields(payload.transport, payload.command, payload.url)

    row = MCPServer(
        name=name_clean,
        description=payload.description.strip(),
        transport=payload.transport.value,
        command=payload.command.strip(),
        args=[str(a) for a in payload.args],
        env_encrypted=encrypt_kv_dict(payload.env),
        url=payload.url.strip(),
        headers_encrypted=encrypt_kv_dict(payload.headers),
        timeout_sec=payload.timeout_sec,
        is_enabled=payload.is_enabled,
        last_status="unknown",
        last_error="",
        discovered_tools=[],
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return _to_response(row)


@router.post("/import", response_model=MCPImportResponse)
async def import_mcp_servers(
    payload: MCPImportRequest,
    db: AsyncSession = Depends(get_db),
) -> MCPImportResponse:
    """Import servers from standard Claude Desktop / Cursor `{"mcpServers": {...}}` JSON."""
    if not payload.mcpServers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`mcpServers` object cannot be empty.",
        )

    created = 0
    updated = 0
    skipped = 0
    saved_rows: list[MCPServer] = []

    for raw_name, cfg in payload.mcpServers.items():
        if not isinstance(cfg, dict):
            skipped += 1
            continue
        name = str(raw_name).strip()
        if not name:
            skipped += 1
            continue

        raw_url = str(cfg.get("url") or cfg.get("serverUrl") or "").strip()
        raw_cmd = str(cfg.get("command") or "").strip()
        raw_type = str(cfg.get("transport") or cfg.get("type") or "").strip().lower()

        if raw_type in ("sse", "streamable_http", "stdio"):
            transport = MCPTransport(raw_type)
        elif raw_type in ("http", "streamable-http", "streamablehttp"):
            transport = MCPTransport.STREAMABLE_HTTP
        elif raw_url and not raw_cmd:
            transport = (
                MCPTransport.SSE if raw_url.rstrip("/").endswith("/sse") else MCPTransport.STREAMABLE_HTTP
            )
        else:
            transport = MCPTransport.STDIO

        if transport == MCPTransport.STDIO and not raw_cmd:
            skipped += 1
            continue
        if transport != MCPTransport.STDIO and not raw_url:
            skipped += 1
            continue

        raw_args = cfg.get("args") or []
        args_list = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
        raw_env = cfg.get("env") or {}
        env_dict = (
            {str(k): str(v) for k, v in raw_env.items()} if isinstance(raw_env, dict) else {}
        )
        raw_headers = cfg.get("headers") or {}
        headers_dict = (
            {str(k): str(v) for k, v in raw_headers.items()}
            if isinstance(raw_headers, dict)
            else {}
        )
        desc = str(cfg.get("description") or "").strip()

        existing = await db.scalar(select(MCPServer).where(MCPServer.name == name))
        if existing is not None:
            if not payload.overwrite_existing:
                skipped += 1
                continue
            existing.description = desc or existing.description
            existing.transport = transport.value
            existing.command = raw_cmd
            existing.args = args_list
            if env_dict:
                existing.env_encrypted = encrypt_kv_dict(env_dict)
            existing.url = raw_url
            if headers_dict:
                existing.headers_encrypted = encrypt_kv_dict(headers_dict)
            updated += 1
            saved_rows.append(existing)
        else:
            row = MCPServer(
                name=name,
                description=desc,
                transport=transport.value,
                command=raw_cmd,
                args=args_list,
                env_encrypted=encrypt_kv_dict(env_dict),
                url=raw_url,
                headers_encrypted=encrypt_kv_dict(headers_dict),
                timeout_sec=30,
                is_enabled=True,
                last_status="unknown",
                last_error="",
                discovered_tools=[],
            )
            db.add(row)
            created += 1
            saved_rows.append(row)

    await db.flush()
    for r in saved_rows:
        await db.refresh(r)

    return MCPImportResponse(
        created=created,
        updated=updated,
        skipped=skipped,
        items=[_to_response(r) for r in saved_rows],
    )


@router.get("/{server_id}", response_model=MCPServerResponse)
async def get_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
) -> MCPServerResponse:
    row = await db.get(MCPServer, server_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found.")
    return _to_response(row)


@router.put("/{server_id}", response_model=MCPServerResponse)
async def update_mcp_server(
    server_id: str,
    payload: MCPServerUpdate,
    db: AsyncSession = Depends(get_db),
) -> MCPServerResponse:
    row = await db.get(MCPServer, server_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found.")

    updates = payload.model_dump(exclude_unset=True)

    if "name" in updates and updates["name"].strip() != row.name:
        dup = await db.scalar(select(MCPServer).where(MCPServer.name == updates["name"].strip()))
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An MCP server named '{updates['name']}' already exists.",
            )
        updates["name"] = updates["name"].strip()

    new_transport = (
        MCPTransport(updates["transport"]) if "transport" in updates else MCPTransport(row.transport)
    )
    new_command = str(updates.get("command", row.command) or "")
    new_url = str(updates.get("url", row.url) or "")
    _validate_transport_fields(new_transport, new_command, new_url)

    if "env" in updates:
        env_val = updates.pop("env")
        if env_val is not None:
            row.env_encrypted = encrypt_kv_dict(env_val)
    if "headers" in updates:
        headers_val = updates.pop("headers")
        if headers_val is not None:
            row.headers_encrypted = encrypt_kv_dict(headers_val)

    for k, v in updates.items():
        if k == "transport" and isinstance(v, MCPTransport):
            setattr(row, k, v.value)
        elif k in ("command", "url", "description") and isinstance(v, str):
            setattr(row, k, v.strip())
        else:
            setattr(row, k, v)

    await db.flush()
    await db.refresh(row)
    return _to_response(row)


@router.patch("/{server_id}/toggle", response_model=MCPServerResponse)
async def toggle_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
) -> MCPServerResponse:
    row = await db.get(MCPServer, server_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found.")
    row.is_enabled = not row.is_enabled
    await db.flush()
    await db.refresh(row)
    return _to_response(row)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await db.get(MCPServer, server_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found.")
    await db.delete(row)


@router.post("/{server_id}/refresh", response_model=MCPServerResponse)
async def refresh_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
) -> MCPServerResponse:
    """Connect to the MCP server, perform handshake + tools/list, and cache discovered tools."""
    row = await db.get(MCPServer, server_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found.")

    try:
        tools, latency_ms = await discover_mcp_tools(row)
        row.discovered_tools = tools
        row.last_status = "connected"
        row.last_error = ""
        row.last_latency_ms = latency_ms
        row.last_synced_at = datetime.now(UTC)
    except Exception as exc:
        row.last_status = "error"
        row.last_error = f"{type(exc).__name__}: {exc}"
        row.last_synced_at = datetime.now(UTC)

    await db.flush()
    await db.refresh(row)
    return _to_response(row)


@router.post("/{server_id}/tools/{tool_name}/test", response_model=MCPToolCallTestResponse)
async def test_mcp_server_tool(
    server_id: str,
    tool_name: str,
    payload: MCPToolCallTestRequest,
    db: AsyncSession = Depends(get_db),
) -> MCPToolCallTestResponse:
    """Execute a live `tools/call` dry-run against a discovered MCP server tool."""
    row = await db.get(MCPServer, server_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found.")

    res = await call_mcp_tool(row, tool_name, payload.arguments)
    return MCPToolCallTestResponse(
        success=res.success,
        output=res.output,
        error=res.error,
        metadata=res.metadata,
    )
