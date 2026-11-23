from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.engine.skills.fs_loader import get_skill_summary
from app.models.agent import AgentConfig, AgentRole
from app.models.capability import ToolConfig
from app.models.llm import LLMModel, ModelType
from app.models.mcp import MCPServer
from app.schemas.agent import (
    AgentCreate,
    AgentListResponse,
    AgentMcpBrief,
    AgentResponse,
    AgentSkillBrief,
    AgentToolBrief,
    AgentUpdate,
)

router = APIRouter(prefix="/agents", tags=["Agents"])

VALID_AGENT_LOOPS = {"direct_chat", "react", "plan_execute", "reflexion"}


async def _to_response(db: AsyncSession, row: AgentConfig) -> AgentResponse:
    model_name = ""
    if row.chat_model_id:
        m = await db.get(LLMModel, row.chat_model_id)
        if m is not None:
            model_name = f"{m.name} ({m.model_id})"

    tools_brief = [
        AgentToolBrief(
            id=t.id,
            name=t.name,
            display_name=t.display_name,
            tool_type=t.tool_type,
            is_enabled=t.is_enabled,
        )
        for t in (row.tools or [])
    ]
    mcp_brief = [
        AgentMcpBrief(
            id=s.id,
            name=s.name,
            transport=s.transport,
            is_enabled=s.is_enabled,
            last_status=s.last_status,
            tool_count=len(s.discovered_tools or []),
        )
        for s in (row.mcp_servers or [])
    ]
    skills_root = settings.resolved_skills_dir
    raw_skill_names = [str(n).strip() for n in (row.skill_names or []) if str(n).strip()]
    skills_brief: list[AgentSkillBrief] = []
    for sname in raw_skill_names:
        summary = get_skill_summary(skills_root, sname, include_tree=False)
        if summary is not None:
            skills_brief.append(
                AgentSkillBrief(
                    name=summary.name,
                    title=summary.title,
                    description=summary.description,
                    file_count=summary.file_count,
                    exists=True,
                )
            )
        else:
            skills_brief.append(
                AgentSkillBrief(
                    name=sname,
                    title=sname,
                    description="",
                    file_count=0,
                    exists=False,
                )
            )
    return AgentResponse(
        id=row.id,
        name=row.name,
        role=AgentRole(row.role),
        description=row.description,
        chat_model_id=row.chat_model_id,
        chat_model_name=model_name,
        loop_engine=row.loop_engine,
        system_prompt=row.system_prompt,
        temperature=row.temperature,
        sandbox_mode=row.sandbox_mode,
        sandbox_image=row.sandbox_image,
        tool_ids=[t.id for t in tools_brief],
        tools=tools_brief,
        mcp_server_ids=[s.id for s in mcp_brief],
        mcp_servers=mcp_brief,
        mcp_tool_filters=dict(row.mcp_tool_filters or {}),
        skill_names=raw_skill_names,
        skills=skills_brief,
        guardrail_config=row.guardrail_config or {},
        memory_config=row.memory_config or {},
        is_active=row.is_active,
        is_default=row.is_default,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _resolve_tools(db: AsyncSession, tool_ids: list[str]) -> list[ToolConfig]:
    if not tool_ids:
        return []
    rows = list((await db.scalars(select(ToolConfig).where(ToolConfig.id.in_(tool_ids)))).all())
    return rows


def _sanitize_mcp_tool_filters(
    raw_filters: dict[str, list[str] | None] | None,
    valid_server_ids: set[str],
) -> dict[str, list[str]]:
    """Keep only explicit per-server tool allowlists for bound MCP server IDs."""
    if not raw_filters:
        return {}
    cleaned: dict[str, list[str]] = {}
    for sid, tool_list in raw_filters.items():
        if sid not in valid_server_ids or tool_list is None:
            continue
        cleaned[sid] = list(dict.fromkeys(str(name).strip() for name in tool_list if str(name).strip()))
    return cleaned


async def _resolve_mcp_servers(db: AsyncSession, server_ids: list[str]) -> list[MCPServer]:
    if not server_ids:
        return []
    rows = list((await db.scalars(select(MCPServer).where(MCPServer.id.in_(server_ids)))).all())
    return rows


def _sanitize_skill_names(raw_names: list[str] | None) -> list[str]:
    if not raw_names:
        return []
    return list(dict.fromkeys(str(n).strip() for n in raw_names if str(n).strip()))


@router.get("", response_model=AgentListResponse)
async def list_agents(
    role: AgentRole | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> AgentListResponse:
    role_rows = (
        await db.execute(
            select(AgentConfig.role, func.count(AgentConfig.id)).group_by(AgentConfig.role)
        )
    ).all()
    role_counts: dict[str, int] = {"all": 0, "active": 0}
    for r in AgentRole:
        role_counts[r.value] = 0
    for role_val, cnt in role_rows:
        role_counts[str(role_val)] = int(cnt)
        role_counts["all"] += int(cnt)
    active_cnt = await db.scalar(
        select(func.count(AgentConfig.id)).where(AgentConfig.is_active.is_(True))
    )
    role_counts["active"] = int(active_cnt or 0)

    filters = []
    if role is not None:
        filters.append(AgentConfig.role == role.value)
    if is_active is not None:
        filters.append(AgentConfig.is_active == is_active)
    if search and search.strip():
        pat = f"%{search.strip()}%"
        filters.append(
            or_(
                AgentConfig.name.ilike(pat),
                AgentConfig.description.ilike(pat),
                AgentConfig.role.ilike(pat),
            )
        )

    count_stmt = select(func.count(AgentConfig.id))
    data_stmt = select(AgentConfig).order_by(
        AgentConfig.is_default.desc(), AgentConfig.created_at.desc()
    )
    if filters:
        count_stmt = count_stmt.where(*filters)
        data_stmt = data_stmt.where(*filters)

    total = int((await db.scalar(count_stmt)) or 0)
    rows = list(
        (await db.execute(data_stmt.offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )
    items = [await _to_response(db, r) for r in rows]
    return AgentListResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        role_counts=role_counts,
    )


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    if payload.loop_engine not in VALID_AGENT_LOOPS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported loop engine '{payload.loop_engine}'.",
        )

    dup = await db.scalar(select(AgentConfig).where(AgentConfig.name == payload.name.strip()))
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An agent named '{payload.name}' already exists.",
        )

    model_obj = await db.get(LLMModel, payload.chat_model_id)
    if model_obj is None or model_obj.model_type != ModelType.CHAT.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected Chat LLM does not exist or is not a chat model.",
        )

    is_default = payload.is_default
    if is_default:
        await db.execute(update(AgentConfig).values(is_default=False))
    else:
        existing_count = await db.scalar(select(func.count(AgentConfig.id)))
        if not existing_count:
            is_default = True

    tools = await _resolve_tools(db, payload.tool_ids)
    mcp_servers = await _resolve_mcp_servers(db, payload.mcp_server_ids)
    skill_names = _sanitize_skill_names(payload.skill_names)
    valid_mcp_ids = {s.id for s in mcp_servers}
    agent = AgentConfig(
        name=payload.name.strip(),
        role=payload.role.value,
        description=payload.description.strip(),
        chat_model_id=model_obj.id,
        loop_engine=payload.loop_engine,
        system_prompt=payload.system_prompt,
        temperature=payload.temperature,
        sandbox_mode=payload.sandbox_mode,
        sandbox_image=payload.sandbox_image.strip() or "agan-ctf-sandbox:latest",
        guardrail_config=payload.guardrail_config.model_dump(),
        memory_config=payload.memory_config,
        mcp_tool_filters=_sanitize_mcp_tool_filters(payload.mcp_tool_filters, valid_mcp_ids),
        skill_names=skill_names,
        is_active=payload.is_active,
        is_default=is_default,
        tools=tools,
        mcp_servers=mcp_servers,
    )
    db.add(agent)
    await db.flush()
    await db.refresh(agent)
    return await _to_response(db, agent)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> AgentResponse:
    agent = await db.get(AgentConfig, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    return await _to_response(db, agent)


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    agent = await db.get(AgentConfig, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    updates = payload.model_dump(exclude_unset=True)

    if "loop_engine" in updates and updates["loop_engine"] not in VALID_AGENT_LOOPS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported loop engine '{updates['loop_engine']}'.",
        )

    if "name" in updates and updates["name"].strip() != agent.name:
        dup = await db.scalar(
            select(AgentConfig).where(AgentConfig.name == updates["name"].strip())
        )
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An agent named '{updates['name']}' already exists.",
            )
        updates["name"] = updates["name"].strip()

    if "chat_model_id" in updates and updates["chat_model_id"]:
        m = await db.get(LLMModel, updates["chat_model_id"])
        if m is None or m.model_type != ModelType.CHAT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected Chat LLM does not exist or is not a chat model.",
            )

    if updates.get("is_default") is True:
        await db.execute(
            update(AgentConfig).where(AgentConfig.id != agent.id).values(is_default=False)
        )

    tool_ids = updates.pop("tool_ids", None)
    if tool_ids is not None:
        agent.tools = await _resolve_tools(db, tool_ids)

    mcp_server_ids = updates.pop("mcp_server_ids", None)
    if mcp_server_ids is not None:
        agent.mcp_servers = await _resolve_mcp_servers(db, mcp_server_ids)

    raw_mcp_filters = updates.pop("mcp_tool_filters", None)
    if raw_mcp_filters is not None or mcp_server_ids is not None:
        active_ids = {s.id for s in (agent.mcp_servers or [])}
        source_filters = raw_mcp_filters if raw_mcp_filters is not None else agent.mcp_tool_filters
        agent.mcp_tool_filters = _sanitize_mcp_tool_filters(source_filters, active_ids)

    skill_names = updates.pop("skill_names", None)
    if skill_names is not None:
        agent.skill_names = _sanitize_skill_names(skill_names)

    guardrails = updates.pop("guardrail_config", None)
    if guardrails is not None:
        agent.guardrail_config = guardrails

    for k, v in updates.items():
        if k == "role" and isinstance(v, AgentRole):
            setattr(agent, k, v.value)
        else:
            setattr(agent, k, v)

    await db.flush()
    await db.refresh(agent)
    return await _to_response(db, agent)


@router.patch("/{agent_id}/toggle", response_model=AgentResponse)
async def toggle_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> AgentResponse:
    agent = await db.get(AgentConfig, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    agent.is_active = not agent.is_active
    await db.flush()
    await db.refresh(agent)
    return await _to_response(db, agent)


@router.patch("/{agent_id}/default", response_model=AgentResponse)
async def set_default_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> AgentResponse:
    agent = await db.get(AgentConfig, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    await db.execute(update(AgentConfig).values(is_default=False))
    agent.is_default = True
    await db.flush()
    await db.refresh(agent)
    return await _to_response(db, agent)


@router.post("/{agent_id}/clone", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def clone_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> AgentResponse:
    src = await db.get(AgentConfig, agent_id)
    if src is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    base_name = f"{src.name} (Copy)"
    candidate = base_name
    idx = 2
    while await db.scalar(select(AgentConfig).where(AgentConfig.name == candidate)) is not None:
        candidate = f"{src.name} (Copy {idx})"
        idx += 1

    cloned = AgentConfig(
        name=candidate,
        role=src.role,
        description=src.description,
        chat_model_id=src.chat_model_id,
        loop_engine=src.loop_engine,
        system_prompt=src.system_prompt,
        temperature=src.temperature,
        sandbox_mode=src.sandbox_mode,
        sandbox_image=src.sandbox_image,
        guardrail_config=dict(src.guardrail_config or {}),
        memory_config=dict(src.memory_config or {}),
        mcp_tool_filters={k: list(v) for k, v in (src.mcp_tool_filters or {}).items() if isinstance(v, list)},
        skill_names=list(src.skill_names or []),
        is_active=src.is_active,
        is_default=False,
        tools=list(src.tools or []),
        mcp_servers=list(src.mcp_servers or []),
    )
    db.add(cloned)
    await db.flush()
    await db.refresh(cloned)
    return await _to_response(db, cloned)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> None:
    agent = await db.get(AgentConfig, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    await db.delete(agent)
