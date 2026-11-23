from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.tools.base import ToolExecutionContext
from app.engine.tools.db_loader import (
    FILE_BUILTIN_TOOL_NAMES,
    WEB_BUILTIN_TOOL_NAMES,
    instantiate_tool_from_row,
    present_tool_implementation_config,
    sanitize_builtin_file_config,
    sanitize_builtin_web_config,
)
from app.models.capability import ToolConfig, ToolType
from app.schemas.capability import (
    ToolCreate,
    ToolListResponse,
    ToolResponse,
    ToolTestRequest,
    ToolTestResponse,
    ToolUpdate,
)

router = APIRouter(prefix="/tools", tags=["Tools"])


def _to_response(row: ToolConfig) -> ToolResponse:
    return ToolResponse(
        id=row.id,
        name=row.name,
        display_name=row.display_name,
        description=row.description,
        tool_type=ToolType(row.tool_type),
        category=row.category,
        parameters_schema=row.parameters_schema or {},
        implementation_config=present_tool_implementation_config(row),
        is_enabled=row.is_enabled,
        requires_sandbox=row.requires_sandbox,
        is_builtin=row.is_builtin,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=ToolListResponse)
async def list_tools(
    tool_type: ToolType | None = Query(default=None),
    is_enabled: bool | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ToolListResponse:
    type_rows = (
        await db.execute(
            select(ToolConfig.tool_type, func.count(ToolConfig.id)).group_by(ToolConfig.tool_type)
        )
    ).all()
    type_counts = {"all": 0, "builtin": 0, "custom_http": 0, "custom_python": 0, "enabled": 0}
    for t_type, cnt in type_rows:
        type_counts[str(t_type)] = int(cnt)
        type_counts["all"] += int(cnt)
    enabled_cnt = await db.scalar(
        select(func.count(ToolConfig.id)).where(ToolConfig.is_enabled.is_(True))
    )
    type_counts["enabled"] = int(enabled_cnt or 0)

    filters = []
    if tool_type is not None:
        filters.append(ToolConfig.tool_type == tool_type.value)
    if is_enabled is not None:
        filters.append(ToolConfig.is_enabled == is_enabled)
    if search and search.strip():
        pat = f"%{search.strip()}%"
        filters.append(
            or_(
                ToolConfig.name.ilike(pat),
                ToolConfig.display_name.ilike(pat),
                ToolConfig.description.ilike(pat),
                ToolConfig.category.ilike(pat),
            )
        )

    count_stmt = select(func.count(ToolConfig.id))
    data_stmt = select(ToolConfig).order_by(ToolConfig.is_builtin.desc(), ToolConfig.name.asc())
    if filters:
        count_stmt = count_stmt.where(*filters)
        data_stmt = data_stmt.where(*filters)

    total = int((await db.scalar(count_stmt)) or 0)
    rows = list(
        (await db.execute(data_stmt.offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )
    return ToolListResponse.create(
        items=[_to_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        type_counts=type_counts,
    )


@router.post("", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def create_tool(
    payload: ToolCreate,
    db: AsyncSession = Depends(get_db),
) -> ToolResponse:
    if payload.tool_type == ToolType.BUILTIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Custom tools must use `custom_http` or `custom_python` type.",
        )

    dup = await db.scalar(select(ToolConfig).where(ToolConfig.name == payload.name))
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A tool named '{payload.name}' already exists.",
        )

    row = ToolConfig(
        name=payload.name,
        display_name=payload.display_name.strip(),
        description=payload.description.strip(),
        tool_type=payload.tool_type.value,
        category=payload.category.strip() or "custom",
        parameters_schema=payload.parameters_schema,
        implementation_config=payload.implementation_config,
        is_enabled=payload.is_enabled,
        requires_sandbox=(payload.tool_type == ToolType.CUSTOM_PYTHON or payload.requires_sandbox),
        is_builtin=False,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return _to_response(row)


@router.get("/{tool_id}", response_model=ToolResponse)
async def get_tool(tool_id: str, db: AsyncSession = Depends(get_db)) -> ToolResponse:
    row = await db.get(ToolConfig, tool_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found.")
    return _to_response(row)


@router.put("/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: str,
    payload: ToolUpdate,
    db: AsyncSession = Depends(get_db),
) -> ToolResponse:
    row = await db.get(ToolConfig, tool_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found.")

    updates = payload.model_dump(exclude_unset=True)
    # Protect built-in tool schemas from breaking edits while allowing safe config on file & web tools
    if row.is_builtin:
        updates.pop("parameters_schema", None)
        if row.name in FILE_BUILTIN_TOOL_NAMES and "implementation_config" in updates:
            sanitized = sanitize_builtin_file_config(row.name, updates["implementation_config"])
            if sanitized["root_dir"]:
                candidate = Path(sanitized["root_dir"]).expanduser()
                if candidate.exists() and not candidate.is_dir():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Configured root_dir '{sanitized['root_dir']}' exists but is not a directory.",
                    )
            updates["implementation_config"] = sanitized
        elif row.name in WEB_BUILTIN_TOOL_NAMES and "implementation_config" in updates:
            updates["implementation_config"] = sanitize_builtin_web_config(
                row.name,
                updates["implementation_config"],
                existing_cfg=row.implementation_config,
            )
        else:
            updates.pop("implementation_config", None)

    for k, v in updates.items():
        setattr(row, k, v)

    await db.flush()
    await db.refresh(row)
    return _to_response(row)


@router.patch("/{tool_id}/toggle", response_model=ToolResponse)
async def toggle_tool(tool_id: str, db: AsyncSession = Depends(get_db)) -> ToolResponse:
    row = await db.get(ToolConfig, tool_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found.")
    row.is_enabled = not row.is_enabled
    await db.flush()
    await db.refresh(row)
    return _to_response(row)


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(tool_id: str, db: AsyncSession = Depends(get_db)) -> None:
    row = await db.get(ToolConfig, tool_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found.")
    if row.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Built-in tools cannot be deleted; disable them instead.",
        )
    await db.delete(row)


@router.post("/{tool_id}/test", response_model=ToolTestResponse)
async def test_tool(
    tool_id: str,
    payload: ToolTestRequest,
    db: AsyncSession = Depends(get_db),
) -> ToolTestResponse:
    """Run a safe dry-run execution of a tool against a temporary SimulatedSandbox."""
    row = await db.get(ToolConfig, tool_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found.")

    inst = instantiate_tool_from_row(row)
    if inst is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to instantiate tool '{row.name}'.",
        )

    sim = SimulatedSandbox(
        "tool-test",
        workspace_host_dir=settings.workspaces_dir / "_tool_test",
        seed_demo_if_empty=True,
    )
    ctx = ToolExecutionContext(
        session_id="tool-test",
        sandbox=sim,
        scratchpad=WorkingScratchpad(),
        flag_regex=r"flag\{[^}\s]+\}",
    )
    try:
        res = await inst.execute(payload.arguments, ctx)
        return ToolTestResponse(
            success=res.success,
            output=res.output,
            error=res.error,
            metadata=res.metadata,
        )
    except Exception as exc:
        return ToolTestResponse(
            success=False,
            output="",
            error=f"{type(exc).__name__}: {exc}",
        )
