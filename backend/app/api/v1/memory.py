from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.engine.memory.store import (
    delete_memory_file,
    list_memory_files,
    read_memory_file,
    resolve_scope_dir,
    search_memory_files,
    validate_scope_id,
    write_memory_file,
)
from app.models.agent import AgentConfig
from app.models.chat import ChatConversation
from app.schemas.memory import (
    MemoryFileContentDTO,
    MemoryFileItemDTO,
    MemoryFileSaveRequest,
    MemoryScopeGroupDTO,
    MemoryScopesOverviewDTO,
    MemoryScopeType,
    MemorySearchHitDTO,
    MemorySearchResponseDTO,
)

router = APIRouter(prefix="/memories", tags=["Memories"])


def _memories_root() -> Path:
    return settings.resolved_memories_dir


def _shared_dir_for_conv(conv_id: str) -> Path:
    clean = validate_scope_id(conv_id)
    ws_root = settings.workspaces_dir
    target = (ws_root / f"chat-{clean}" / ".agan" / "shared").resolve()
    if not target.is_relative_to(ws_root):
        raise PermissionError("Invalid shared blackboard path.")
    return target


def _resolve_api_scope_dir(
    scope: MemoryScopeType,
    scope_id: str,
    *,
    create: bool = False,
) -> tuple[Path, str]:
    if scope == "global":
        return (
            resolve_scope_dir(_memories_root(), "global", create=create),
            "Global Memory",
        )
    if scope == "agent":
        if not scope_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parameter 'scope_id' (agent ID) is required for scope='agent'.",
            )
        return (
            resolve_scope_dir(
                _memories_root(), "agent", scope_id=scope_id.strip(), create=create
            ),
            f"Agent Memory ({scope_id.strip()[:8]})",
        )
    # shared
    if not scope_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parameter 'scope_id' (conversation ID) is required for scope='shared'.",
        )
    sdir = _shared_dir_for_conv(scope_id.strip())
    return (
        resolve_scope_dir(_memories_root(), "shared", shared_dir=sdir, create=create),
        "Shared Task Blackboard",
    )


def _build_group(
    *,
    scope: MemoryScopeType,
    scope_id: str,
    label: str,
    subtitle: str,
    scope_dir: Path,
) -> MemoryScopeGroupDTO:
    files = list_memory_files(scope_dir, include_index=True)
    non_index = [f for f in files if not f.is_index]
    return MemoryScopeGroupDTO(
        scope=scope,
        scope_id=scope_id,
        label=label,
        subtitle=subtitle,
        file_count=len(non_index),
        total_size=sum(f.size for f in files),
        files=[
            MemoryFileItemDTO(
                path=f.path,
                title=f.title,
                memory_type=f.memory_type,
                summary=f.summary,
                tags=f.tags,
                author=f.author,
                size=f.size,
                is_index=f.is_index,
                updated_at=f.updated_at,
            )
            for f in files
        ],
    )


@router.get("/scopes", response_model=MemoryScopesOverviewDTO)
async def list_memory_scopes(
    db: AsyncSession = Depends(get_db),
) -> MemoryScopesOverviewDTO:
    """List Global, per-Agent, and active Shared Blackboard Markdown memory scopes."""
    root = _memories_root()

    # 1. Global scope
    global_dir = resolve_scope_dir(root, "global", create=True)
    global_group = _build_group(
        scope="global",
        scope_id="",
        label="Global Memory",
        subtitle="global/",
        scope_dir=global_dir,
    )

    # 2. Agent scopes (all configured agents + any orphan agent folders on disk)
    db_agents = list(
        (await db.scalars(select(AgentConfig).order_by(AgentConfig.is_default.desc(), AgentConfig.name.asc()))).all()
    )
    agent_map: dict[str, AgentConfig] = {a.id: a for a in db_agents}
    agent_groups: list[MemoryScopeGroupDTO] = []
    seen_agent_ids: set[str] = set()

    for ag in db_agents:
        seen_agent_ids.add(ag.id)
        adir = resolve_scope_dir(root, "agent", scope_id=ag.id, create=False)
        agent_groups.append(
            _build_group(
                scope="agent",
                scope_id=ag.id,
                label=ag.name,
                subtitle=f"agents/{ag.id[:8]}/",
                scope_dir=adir,
            )
        )

    agents_parent = root / "agents"
    if agents_parent.is_dir():
        for entry in sorted(agents_parent.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir() or entry.name in seen_agent_ids or entry.name.startswith("."):
                continue
            if entry.name in agent_map:
                continue
            grp = _build_group(
                scope="agent",
                scope_id=entry.name,
                label=f"Agent ({entry.name[:8]})",
                subtitle=f"agents/{entry.name}/",
                scope_dir=entry,
            )
            if grp.files:
                agent_groups.append(grp)

    # 3. Shared task blackboards (conversations that have `.agan/shared/` files on disk)
    convs = list(
        (
            await db.scalars(
                select(ChatConversation).order_by(ChatConversation.updated_at.desc()).limit(50)
            )
        ).all()
    )
    shared_groups: list[MemoryScopeGroupDTO] = []
    for conv in convs:
        try:
            sdir = _shared_dir_for_conv(conv.id)
        except (ValueError, PermissionError):
            continue
        if not sdir.is_dir():
            continue
        grp = _build_group(
            scope="shared",
            scope_id=conv.id,
            label=conv.title or f"Chat {conv.id[:8]}",
            subtitle=conv.agent_name_snapshot or f"chat-{conv.id[:8]}",
            scope_dir=sdir,
        )
        if grp.files:
            shared_groups.append(grp)

    return MemoryScopesOverviewDTO(
        root_dir=str(root),
        global_scope=global_group,
        agent_scopes=agent_groups,
        shared_scopes=shared_groups,
    )


@router.get("/file", response_model=MemoryFileContentDTO)
async def get_memory_file_api(
    scope: MemoryScopeType = Query(...),
    scope_id: str = Query(default=""),
    path: str = Query(..., min_length=1),
) -> MemoryFileContentDTO:
    try:
        sdir, _ = _resolve_api_scope_dir(scope, scope_id, create=False)
        data = read_memory_file(sdir, path)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (ValueError, IsADirectoryError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return MemoryFileContentDTO(scope=scope, scope_id=scope_id.strip(), **data)


@router.put("/file", response_model=MemoryFileContentDTO)
async def save_memory_file_api(payload: MemoryFileSaveRequest) -> MemoryFileContentDTO:
    try:
        sdir, label = _resolve_api_scope_dir(payload.scope, payload.scope_id, create=True)
        data = write_memory_file(
            sdir,
            payload.path,
            payload.content,
            title=payload.title,
            memory_type=payload.memory_type,
            tags=payload.tags,
            author="User",
            raw_mode=payload.raw_mode,
            scope_label=label,
        )
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (ValueError, IsADirectoryError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return MemoryFileContentDTO(scope=payload.scope, scope_id=payload.scope_id.strip(), **data)


@router.delete("/file", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory_file_api(
    scope: MemoryScopeType = Query(...),
    scope_id: str = Query(default=""),
    path: str = Query(..., min_length=1),
) -> None:
    try:
        sdir, label = _resolve_api_scope_dir(scope, scope_id, create=False)
        delete_memory_file(sdir, path, scope_label=label)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (ValueError, IsADirectoryError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/search", response_model=MemorySearchResponseDTO)
async def search_memories_api(
    q: str = Query(..., min_length=1),
    scope: str = Query(default="all"),
    scope_id: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> MemorySearchResponseDTO:
    root = _memories_root()
    targets: list[tuple[str, str, str, Path]] = []  # (scope, scope_id, label, dir)

    if scope in ("all", "global"):
        targets.append(("global", "", "Global Memory", resolve_scope_dir(root, "global", create=False)))

    if scope in ("all", "agent"):
        if scope_id.strip():
            ag = await db.get(AgentConfig, scope_id.strip())
            label = ag.name if ag else scope_id.strip()[:8]
            targets.append(
                (
                    "agent",
                    scope_id.strip(),
                    label,
                    resolve_scope_dir(root, "agent", scope_id=scope_id.strip(), create=False),
                )
            )
        else:
            db_agents = list((await db.scalars(select(AgentConfig))).all())
            for ag in db_agents:
                targets.append(
                    (
                        "agent",
                        ag.id,
                        ag.name,
                        resolve_scope_dir(root, "agent", scope_id=ag.id, create=False),
                    )
                )

    if scope in ("all", "shared"):
        if scope_id.strip():
            targets.append(
                ("shared", scope_id.strip(), f"Shared ({scope_id.strip()[:8]})", _shared_dir_for_conv(scope_id.strip()))
            )
        else:
            convs = list(
                (
                    await db.scalars(
                        select(ChatConversation).order_by(ChatConversation.updated_at.desc()).limit(30)
                    )
                ).all()
            )
            for c in convs:
                try:
                    sdir = _shared_dir_for_conv(c.id)
                except (ValueError, PermissionError):
                    continue
                if sdir.is_dir():
                    targets.append(("shared", c.id, c.title or c.id[:8], sdir))

    all_hits: list[MemorySearchHitDTO] = []
    for sc, sid, lbl, sdir in targets:
        raw_hits = search_memory_files([(sc, sdir)], q, limit=limit)
        for h in raw_hits:
            all_hits.append(
                MemorySearchHitDTO(
                    scope=sc,
                    scope_id=sid,
                    scope_label=lbl,
                    path=h["path"],
                    title=h["title"],
                    memory_type=h["memory_type"],
                    tags=h["tags"],
                    author=h["author"],
                    snippet=h["snippet"],
                    score=h["score"],
                )
            )

    all_hits.sort(key=lambda x: (-x.score, x.scope, x.path))
    trimmed = all_hits[:limit]
    return MemorySearchResponseDTO(query=q, total=len(trimmed), items=trimmed)
