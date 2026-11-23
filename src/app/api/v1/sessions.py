from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.engine.loops.factory import LoopEngineFactory
from app.engine.session_runner import session_runner
from app.engine.writeup_generator import generate_session_writeup
from app.models.llm import LLMModel, ModelType
from app.models.session import (
    SandboxMode,
    SessionEvent,
    SessionScratchpad,
    SessionStatus,
    SolveSession,
    Writeup,
)
from app.schemas.session import (
    GenerateWriteupRequest,
    ManualExecRequest,
    ManualExecResponse,
    ScratchpadDTO,
    SessionCreateRequest,
    SessionDetailResponse,
    SessionEventDTO,
    SessionListResponse,
    SessionSummaryResponse,
    SteerRequest,
    StopRequest,
    WriteupDTO,
)

router = APIRouter(prefix="/sessions", tags=["Solve Sessions"])


def _to_summary(obj: SolveSession) -> SessionSummaryResponse:
    max_steps = int((obj.guardrail_config or {}).get("max_steps", 25))
    return SessionSummaryResponse(
        id=obj.id,
        title=obj.title,
        category=obj.category,
        chat_model_id=obj.chat_model_id,
        model_name_snapshot=obj.model_name_snapshot,
        loop_engine=obj.loop_engine,
        sandbox_mode=SandboxMode(obj.sandbox_mode),
        sandbox_image=obj.sandbox_image,
        status=SessionStatus(obj.status),
        current_step=obj.current_step,
        max_steps=max_steps,
        total_prompt_tokens=obj.total_prompt_tokens,
        total_completion_tokens=obj.total_completion_tokens,
        total_tokens=obj.total_prompt_tokens + obj.total_completion_tokens,
        total_cost_usd=obj.total_cost_usd,
        detected_flag=obj.detected_flag,
        started_at=obj.started_at,
        finished_at=obj.finished_at,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
        is_live=session_runner.is_live(obj.id),
    )


async def _to_detail(db: AsyncSession, obj: SolveSession) -> SessionDetailResponse:
    sp = await db.scalar(
        select(SessionScratchpad).where(SessionScratchpad.session_id == obj.id)
    )
    wu = await db.scalar(select(Writeup).where(Writeup.session_id == obj.id))
    summary = _to_summary(obj)
    return SessionDetailResponse(
        **summary.model_dump(),
        description=obj.description,
        target_info=obj.target_info or {},
        attachments=list(obj.attachments or []),
        flag_regex=obj.flag_regex,
        sandbox_container_id=obj.sandbox_container_id,
        system_prompt_override=obj.system_prompt_override,
        guardrail_config=obj.guardrail_config or {},
        summary=obj.summary,
        error_message=obj.error_message,
        scratchpad=(
            ScratchpadDTO(
                known_clues=list(sp.known_clues or []),
                endpoints_ports=list(sp.endpoints_ports or []),
                vulnerabilities=list(sp.vulnerabilities or []),
                ruled_out=list(sp.ruled_out or []),
                notes_md=sp.notes_md or "",
            )
            if sp
            else ScratchpadDTO()
        ),
        writeup=(
            WriteupDTO(
                id=wu.id,
                session_id=wu.session_id,
                title=wu.title,
                content_md=wu.content_md,
                model_used=wu.model_used,
                created_at=wu.created_at,
                updated_at=wu.updated_at,
            )
            if wu
            else None
        ),
    )


@router.get("/engines")
async def list_available_engines() -> dict[str, list[str]]:
    return {"engines": LoopEngineFactory.list_engines()}


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    status_filter: SessionStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    status_rows = (
        await db.execute(
            select(SolveSession.status, func.count(SolveSession.id)).group_by(SolveSession.status)
        )
    ).all()
    status_counts = {
        "all": 0,
        "pending": 0,
        "running": 0,
        "paused": 0,
        "solved": 0,
        "failed": 0,
        "stopped": 0,
    }
    for st, cnt in status_rows:
        status_counts[str(st)] = int(cnt)
        status_counts["all"] += int(cnt)

    filters = []
    if status_filter is not None:
        filters.append(SolveSession.status == status_filter.value)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                SolveSession.title.ilike(pattern),
                SolveSession.category.ilike(pattern),
                SolveSession.description.ilike(pattern),
                SolveSession.detected_flag.ilike(pattern),
            )
        )

    count_stmt = select(func.count(SolveSession.id))
    data_stmt = select(SolveSession).order_by(SolveSession.created_at.desc())
    if filters:
        count_stmt = count_stmt.where(*filters)
        data_stmt = data_stmt.where(*filters)

    total = int((await db.scalar(count_stmt)) or 0)
    rows = list(
        (await db.execute(data_stmt.offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )

    return SessionListResponse.create(
        items=[_to_summary(s) for s in rows],
        total=total,
        page=page,
        page_size=page_size,
        status_counts=status_counts,
    )


@router.post("", response_model=SessionDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    if payload.loop_engine.lower() not in LoopEngineFactory.list_engines():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported loop engine '{payload.loop_engine}'.",
        )

    model_obj = await db.get(LLMModel, payload.chat_model_id)
    if model_obj is None or model_obj.model_type != ModelType.CHAT.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected Chat LLM model does not exist or is not a chat model.",
        )

    session_obj = SolveSession(
        title=payload.title.strip(),
        category=payload.category.strip().lower() or "misc",
        description=payload.description,
        target_info=payload.target_info,
        attachments=[],
        flag_regex=payload.flag_regex or r"flag\{[^}\s]+\}",
        chat_model_id=model_obj.id,
        model_name_snapshot=f"{model_obj.name} ({model_obj.model_id})",
        loop_engine=payload.loop_engine.lower(),
        sandbox_mode=payload.sandbox_mode.value,
        sandbox_image=payload.sandbox_image.strip() or "agan-ctf-sandbox:latest",
        system_prompt_override=payload.system_prompt_override,
        guardrail_config=payload.guardrail_config.model_dump(),
        status=SessionStatus.PENDING.value,
    )
    db.add(session_obj)
    await db.flush()

    db.add(SessionScratchpad(session_id=session_obj.id))
    await db.flush()
    await db.refresh(session_obj)
    return await _to_detail(db, session_obj)


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    return await _to_detail(db, obj)


@router.get("/{session_id}/events", response_model=list[SessionEventDTO])
async def get_session_events(
    session_id: str,
    after_seq: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[SessionEventDTO]:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    rows = list(
        (
            await db.execute(
                select(SessionEvent)
                .where(SessionEvent.session_id == session_id, SessionEvent.seq_id > after_seq)
                .order_by(SessionEvent.seq_id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        SessionEventDTO(
            seq_id=ev.seq_id,
            session_id=ev.session_id,
            step_index=ev.step_index,
            agent_name=ev.agent_name,
            event_type=ev.event_type,
            timestamp=ev.created_at,
            usage=(
                {
                    "prompt_tokens": ev.prompt_tokens,
                    "completion_tokens": ev.completion_tokens,
                    "total_tokens": ev.prompt_tokens + ev.completion_tokens,
                    "cost_usd": ev.cost_usd,
                    "latency_ms": ev.latency_ms,
                }
                if (ev.prompt_tokens or ev.completion_tokens or ev.latency_ms)
                else None
            ),
            payload=ev.payload or {},
        )
        for ev in rows
    ]


@router.post("/{session_id}/attachments", response_model=SessionDetailResponse)
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    safe_name = Path(file.filename or "attachment.bin").name
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe_name) or "attachment.bin"

    att_dir = session_runner.attachments_dir(session_id)
    dest = att_dir / safe_name
    content = await file.read()
    dest.write_bytes(content)

    existing_list = list(obj.attachments or [])
    if safe_name not in existing_list:
        existing_list.append(safe_name)
        obj.attachments = existing_list
    await db.flush()
    await db.refresh(obj)
    return await _to_detail(db, obj)


@router.post("/{session_id}/start", response_model=SessionDetailResponse)
async def start_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    try:
        await session_runner.start_session(session_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start session sandbox: {exc}",
        ) from exc

    await db.refresh(obj)
    return await _to_detail(db, obj)


@router.post("/{session_id}/pause", response_model=SessionDetailResponse)
async def pause_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    try:
        await session_runner.pause_session(session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.refresh(obj)
    return await _to_detail(db, obj)


@router.post("/{session_id}/resume", response_model=SessionDetailResponse)
async def resume_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    try:
        await session_runner.resume_session(session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.refresh(obj)
    return await _to_detail(db, obj)


@router.post("/{session_id}/steer", response_model=dict[str, str])
async def steer_session(
    session_id: str,
    payload: SteerRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    try:
        await session_runner.steer_session(
            session_id,
            payload.message.strip(),
            immediate_resume=payload.immediate_resume,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "queued"}


@router.post("/{session_id}/stop", response_model=SessionDetailResponse)
async def stop_session(
    session_id: str,
    payload: StopRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> SessionDetailResponse:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    keep = payload.keep_container if payload else False
    await session_runner.stop_session(session_id, keep_container=keep)
    await db.refresh(obj)
    return await _to_detail(db, obj)


@router.put("/{session_id}/scratchpad", response_model=ScratchpadDTO)
async def update_scratchpad(
    session_id: str,
    payload: ScratchpadDTO,
    db: AsyncSession = Depends(get_db),
) -> ScratchpadDTO:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")

    sp = await db.scalar(
        select(SessionScratchpad).where(SessionScratchpad.session_id == session_id)
    )
    if sp is None:
        sp = SessionScratchpad(session_id=session_id)
        db.add(sp)

    sp.known_clues = payload.known_clues
    sp.endpoints_ports = payload.endpoints_ports
    sp.vulnerabilities = payload.vulnerabilities
    sp.ruled_out = payload.ruled_out
    sp.notes_md = payload.notes_md
    await db.flush()

    await session_runner.update_live_scratchpad(session_id, payload.model_dump())
    return payload


@router.post("/{session_id}/sandbox/exec", response_model=ManualExecResponse)
async def exec_sandbox_command(
    session_id: str,
    payload: ManualExecRequest,
    db: AsyncSession = Depends(get_db),
) -> ManualExecResponse:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    try:
        res = await session_runner.exec_manual_command(
            session_id, payload.command, timeout=payload.timeout
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ManualExecResponse(
        command=res.command,
        output=res.output,
        exit_code=res.exit_code,
        timed_out=res.timed_out,
        truncated=res.truncated,
        cwd=res.cwd,
        duration_ms=res.duration_ms,
    )


@router.post("/{session_id}/writeup/generate", response_model=WriteupDTO)
async def generate_writeup(
    session_id: str,
    payload: GenerateWriteupRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> WriteupDTO:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    req = payload or GenerateWriteupRequest()
    try:
        wu = await generate_session_writeup(
            db, obj, model_id=req.model_id, language=req.language
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Writeup generation failed: {exc}",
        ) from exc

    return WriteupDTO(
        id=wu.id,
        session_id=wu.session_id,
        title=wu.title,
        content_md=wu.content_md,
        model_used=wu.model_used,
        created_at=wu.created_at,
        updated_at=wu.updated_at,
    )


@router.get("/{session_id}/writeup/export")
async def export_writeup(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    wu = await db.scalar(select(Writeup).where(Writeup.session_id == session_id))
    if wu is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Writeup has not been generated for this session yet.",
        )
    safe_title = re.sub(r"[^\w.-]+", "_", wu.title).strip("_") or "writeup"
    encoded_name = quote(f"{safe_title}.md")
    return PlainTextResponse(
        content=wu.content_md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    obj = await db.get(SolveSession, session_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if session_runner.is_live(session_id):
        await session_runner.stop_session(session_id, keep_container=False)
    await db.delete(obj)
