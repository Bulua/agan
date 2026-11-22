from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import secret_box
from app.engine.chat_runner import ChatRunSpec, chat_run_manager
from app.engine.guardrails.compactor import ContextCompactor
from app.engine.llm.chat_client import OpenAICompatChatClient
from app.engine.llm.types import LLMMessage, LLMModelConfig
from app.engine.mcp.manager import attach_mcp_tools_to_registry
from app.engine.memory.store import build_memory_prompt_block, resolve_scope_dir
from app.engine.memory.tools import attach_memory_to_agent_run
from app.engine.skills.runtime import attach_skills_to_agent_run
from app.engine.tools.db_loader import build_tool_registry_from_db
from app.models.agent import AgentConfig
from app.models.chat import ChatConversation, ChatMessage
from app.models.llm import LLMModel, ModelType
from app.schemas.chat import (
    ChatConversationCreate,
    ChatConversationDetail,
    ChatConversationListResponse,
    ChatConversationSummary,
    ChatConversationUpdate,
    ChatMessageDTO,
    CompactChatRequest,
    CompactChatResponse,
    SendMessageRequest,
    SteerChatRequest,
)

router = APIRouter(prefix="/chat", tags=["Chat Conversations"])

DEFAULT_TITLES = {"new chat", "新对话", "untitled"}
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _to_summary(conv: ChatConversation) -> ChatConversationSummary:
    return ChatConversationSummary(
        id=conv.id,
        title=conv.title,
        agent_id=conv.agent_id,
        agent_name_snapshot=conv.agent_name_snapshot,
        model_id=conv.model_id,
        model_name_snapshot=conv.model_name_snapshot,
        system_prompt=conv.system_prompt,
        temperature=conv.temperature,
        message_count=conv.message_count,
        total_tokens=conv.total_tokens,
        total_cost_usd=conv.total_cost_usd,
        is_running=chat_run_manager.is_running(conv.id),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


def _to_msg_dto(msg: ChatMessage) -> ChatMessageDTO:
    return ChatMessageDTO(
        id=msg.id,
        conversation_id=msg.conversation_id,
        role=msg.role,
        content=msg.content,
        reasoning_content=msg.reasoning_content,
        tool_calls_json=list(msg.tool_calls_json or []),
        model_id_used=msg.model_id_used,
        prompt_tokens=msg.prompt_tokens,
        completion_tokens=msg.completion_tokens,
        cost_usd=msg.cost_usd,
        latency_ms=msg.latency_ms,
        created_at=msg.created_at,
    )


def _derive_title(text: str) -> str:
    clean = " ".join(text.strip().split())
    if not clean:
        return "New Chat"
    return clean[:38] + ("…" if len(clean) > 38 else "")


def _sse_line(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _resolve_agent(db: AsyncSession, agent_id: str | None) -> AgentConfig | None:
    if agent_id:
        return await db.get(AgentConfig, agent_id)
    return await db.scalar(
        select(AgentConfig)
        .where(AgentConfig.is_active.is_(True))
        .order_by(AgentConfig.is_default.desc(), AgentConfig.created_at.desc())
    )


def _active_history_slice(
    conv: ChatConversation,
    history_rows: list[ChatMessage],
) -> list[ChatMessage]:
    """Return non-compact chat messages strictly after the last compaction checkpoint."""
    non_markers = [r for r in history_rows if r.role in ("user", "assistant", "system")]
    cutoff_id = conv.compacted_before_message_id
    if not cutoff_id:
        return non_markers
    for idx, row in enumerate(non_markers):
        if row.id == cutoff_id:
            return non_markers[idx + 1 :]
    return non_markers


def _build_llm_messages(
    conv: ChatConversation,
    history_rows: list[ChatMessage],
    effective_sys: str = "",
) -> list[LLMMessage]:
    """Assemble LLM messages respecting any persisted conversation compaction summary."""
    msgs: list[LLMMessage] = []
    if effective_sys.strip():
        msgs.append(LLMMessage(role="system", content=effective_sys.strip()))
    if (conv.compacted_summary or "").strip():
        msgs.append(
            LLMMessage(
                role="user",
                content=(
                    "[CONVERSATION CONTEXT SUMMARY — EARLIER TURNS COMPACTED]\n\n"
                    f"{conv.compacted_summary.strip()}"
                ),
            )
        )
    for row in _active_history_slice(conv, history_rows):
        msgs.append(LLMMessage(role=row.role, content=row.content))  # type: ignore[arg-type]
    return msgs


async def _resolve_context_window(db: AsyncSession, conv: ChatConversation) -> int:
    model_id = conv.model_id
    if not model_id and conv.agent_id:
        agent_obj = await db.get(AgentConfig, conv.agent_id)
        if agent_obj and agent_obj.chat_model_id:
            model_id = agent_obj.chat_model_id
    if model_id:
        m = await db.get(LLMModel, model_id)
        if m and m.context_window:
            return int(m.context_window)
    return 128_000


@router.get("/conversations", response_model=ChatConversationListResponse)
async def list_conversations(
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ChatConversationListResponse:
    filters = []
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        filters.append(ChatConversation.title.ilike(pattern))

    count_stmt = select(func.count(ChatConversation.id))
    data_stmt = select(ChatConversation).order_by(ChatConversation.updated_at.desc())
    if filters:
        count_stmt = count_stmt.where(*filters)
        data_stmt = data_stmt.where(*filters)

    total = int((await db.scalar(count_stmt)) or 0)
    rows = list(
        (await db.execute(data_stmt.offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )
    return ChatConversationListResponse.create(
        items=[_to_summary(c) for c in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/conversations",
    response_model=ChatConversationDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: ChatConversationCreate,
    db: AsyncSession = Depends(get_db),
) -> ChatConversationDetail:
    agent_obj = await _resolve_agent(db, payload.agent_id)
    model_obj: LLMModel | None = None
    if agent_obj and agent_obj.chat_model_id:
        model_obj = await db.get(LLMModel, agent_obj.chat_model_id)

    conv = ChatConversation(
        title=payload.title.strip() or "New Chat",
        agent_id=agent_obj.id if agent_obj else None,
        agent_name_snapshot=agent_obj.name if agent_obj else "",
        model_id=model_obj.id if model_obj else None,
        model_name_snapshot=f"{model_obj.name} ({model_obj.model_id})" if model_obj else "",
        system_prompt=payload.system_prompt,
        temperature=payload.temperature,
    )
    db.add(conv)
    await db.flush()
    await db.refresh(conv)
    return ChatConversationDetail(**_to_summary(conv).model_dump(), messages=[])


@router.get("/conversations/{conversation_id}", response_model=ChatConversationDetail)
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
) -> ChatConversationDetail:
    conv = await db.get(ChatConversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found."
        )
    msgs = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    context_window = await _resolve_context_window(db, conv)
    active_llm_msgs = _build_llm_messages(conv, msgs, effective_sys=conv.system_prompt or "")
    context_tokens_est = ContextCompactor.estimate_tokens(active_llm_msgs) if active_llm_msgs else 0

    return ChatConversationDetail(
        **_to_summary(conv).model_dump(),
        messages=[_to_msg_dto(m) for m in msgs],
        context_tokens_estimate=context_tokens_est,
        context_window=context_window,
        has_compacted_summary=bool((conv.compacted_summary or "").strip()),
    )


@router.patch("/conversations/{conversation_id}", response_model=ChatConversationSummary)
async def update_conversation(
    conversation_id: str,
    payload: ChatConversationUpdate,
    db: AsyncSession = Depends(get_db),
) -> ChatConversationSummary:
    conv = await db.get(ChatConversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found."
        )
    updates = payload.model_dump(exclude_unset=True)

    if "agent_id" in updates and updates["agent_id"]:
        agent_obj = await db.get(AgentConfig, updates["agent_id"])
        if agent_obj is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Selected Agent does not exist."
            )
        conv.agent_id = agent_obj.id
        conv.agent_name_snapshot = agent_obj.name
        if agent_obj.chat_model_id:
            m = await db.get(LLMModel, agent_obj.chat_model_id)
            if m:
                conv.model_id = m.id
                conv.model_name_snapshot = f"{m.name} ({m.model_id})"
        updates.pop("agent_id")

    for key, val in updates.items():
        setattr(conv, key, val)

    conv.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(conv)
    return _to_summary(conv)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    conv = await db.get(ChatConversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found."
        )
    if chat_run_manager.is_running(conversation_id):
        await chat_run_manager.stop_run(conversation_id)
    await db.delete(conv)


@router.post("/conversations/{conversation_id}/stop")
async def stop_conversation_run(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Explicitly cancel a background chat task for this conversation."""
    conv = await db.get(ChatConversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found."
        )
    stopped = await chat_run_manager.stop_run(conversation_id)
    return {"stopped": stopped}


@router.post("/conversations/{conversation_id}/steer")
async def steer_conversation_run(
    conversation_id: str,
    payload: SteerChatRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Immediately inject mid-flight steering guidance into a running chat task."""
    conv = await db.get(ChatConversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found."
        )
    try:
        record = chat_run_manager.steer_run(conversation_id, payload.content)
        return {"steered": True, "record": record}
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/conversations/{conversation_id}/compact", response_model=CompactChatResponse)
async def compact_conversation(
    conversation_id: str,
    payload: CompactChatRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> CompactChatResponse:
    """Manually compact earlier conversation messages into a persistent handoff summary."""
    req = payload or CompactChatRequest()
    conv = await db.get(ChatConversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found."
        )
    if chat_run_manager.is_running(conversation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot compact while an Agent task is running. Stop or wait for completion first.",
        )

    history_rows = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    active_rows = _active_history_slice(conv, history_rows)
    if len(active_rows) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Not enough messages since last compaction to compact yet.",
        )

    keep_tail = (
        req.keep_recent
        if len(active_rows) > req.keep_recent
        else max(1, len(active_rows) // 2)
    )
    to_summarize = active_rows[:-keep_tail]
    if not to_summarize:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Not enough earlier messages to summarize.",
        )

    context_window = await _resolve_context_window(db, conv)
    before_msgs = _build_llm_messages(conv, history_rows, effective_sys=conv.system_prompt or "")
    tokens_before = ContextCompactor.estimate_tokens(before_msgs)

    # Build LLM messages to fold (previous compacted summary + slice to summarize)
    source_msgs: list[LLMMessage] = []
    if (conv.compacted_summary or "").strip():
        source_msgs.append(
            LLMMessage(
                role="user",
                content=f"[Previous Context Summary]\n{conv.compacted_summary.strip()}",
            )
        )
    for row in to_summarize:
        source_msgs.append(LLMMessage(role=row.role, content=row.content))  # type: ignore[arg-type]

    # Resolve optional Chat LLM for high-quality structured summarization
    llm_client: OpenAICompatChatClient | None = None
    agent_obj = await _resolve_agent(db, conv.agent_id)
    model_id = (agent_obj.chat_model_id if agent_obj else None) or conv.model_id
    model_obj = await db.get(LLMModel, model_id) if model_id else None
    if model_obj is not None and model_obj.model_type == ModelType.CHAT.value:
        runtime_cfg = LLMModelConfig(
            name=model_obj.name,
            base_url=model_obj.base_url,
            api_key=secret_box.decrypt(model_obj.api_key_encrypted),
            model_id=model_obj.model_id,
            context_window=model_obj.context_window,
            max_output_tokens=model_obj.max_output_tokens,
            temperature=0.1,
            input_price_per_1m=model_obj.input_price_per_1m,
            output_price_per_1m=model_obj.output_price_per_1m,
            extra_params=model_obj.extra_params or {},
        )
        llm_client = OpenAICompatChatClient(runtime_cfg)

    compactor = ContextCompactor(context_window=context_window, keep_recent=keep_tail)
    try:
        summary_text = await compactor._summarize_middle(
            source_msgs,
            llm_client,
            extra_instructions=req.instructions,
        )
    finally:
        if llm_client is not None:
            await llm_client.close()

    conv.compacted_summary = summary_text
    conv.compacted_before_message_id = to_summarize[-1].id
    conv.updated_at = datetime.now(UTC)

    after_msgs = _build_llm_messages(conv, history_rows, effective_sys=conv.system_prompt or "")
    tokens_after = ContextCompactor.estimate_tokens(after_msgs)
    tokens_saved = max(0, tokens_before - tokens_after)

    marker = ChatMessage(
        conversation_id=conversation_id,
        role="compact",
        content=summary_text,
        tool_calls_json=[
            {
                "stage": "manual",
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "tokens_saved": tokens_saved,
                "summarized_messages": len(to_summarize),
                "kept_recent": keep_tail,
                "instructions": req.instructions.strip(),
            }
        ],
    )
    db.add(marker)
    await db.flush()
    await db.refresh(marker)

    return CompactChatResponse(
        compacted=True,
        stage="manual",
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        tokens_saved=tokens_saved,
        summarized_messages=len(to_summarize),
        summary=summary_text,
        context_tokens_estimate=tokens_after,
        context_window=context_window,
        compact_message=_to_msg_dto(marker),
    )


@router.get("/conversations/{conversation_id}/stream")
async def attach_conversation_stream(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Reattach to a running (or recently completed) background chat task over SSE."""
    conv = await db.get(ChatConversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found."
        )

    existing = chat_run_manager.get_run(conversation_id)
    if existing is None:
        # Task already finished & cleaned up: emit latest persisted assistant reply if present
        latest_msg = await db.scalar(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.role == "assistant",
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        msg_payload = _to_msg_dto(latest_msg).model_dump(mode="json") if latest_msg else None

        async def finished_stream() -> AsyncIterator[str]:
            yield _sse_line(
                {
                    "type": "done",
                    "title": conv.title,
                    "assistant_message": msg_payload,
                }
            )

        return StreamingResponse(
            finished_stream(),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    async def replay_stream() -> AsyncIterator[str]:
        async for evt in chat_run_manager.subscribe_existing(conversation_id):
            yield _sse_line(evt)

    return StreamingResponse(
        replay_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_conversation_message(
    conversation_id: str,
    payload: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Launch an independent background Agent chat turn and stream its live SSE events.

    Disconnecting the client stream never cancels the background run; clients may
    reconnect anytime via `GET /conversations/{id}/stream`.
    """
    conv = await db.get(ChatConversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found."
        )

    if chat_run_manager.is_running(conversation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This conversation already has an active run in progress.",
        )

    agent_obj = await _resolve_agent(db, payload.agent_id or conv.agent_id)
    if agent_obj is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active Agent configured. Please create an Agent in Agent Studio first.",
        )

    model_obj = (
        await db.get(LLMModel, agent_obj.chat_model_id) if agent_obj.chat_model_id else None
    )
    if model_obj is None or model_obj.model_type != ModelType.CHAT.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent '{agent_obj.name}' does not have a valid Chat LLM bound.",
        )

    conv.agent_id = agent_obj.id
    conv.agent_name_snapshot = agent_obj.name
    conv.model_id = model_obj.id
    conv.model_name_snapshot = f"{model_obj.name} ({model_obj.model_id})"
    if payload.system_prompt is not None:
        conv.system_prompt = payload.system_prompt
    if payload.temperature is not None:
        conv.temperature = payload.temperature

    # Build hot-reloaded ToolRegistry for bound enabled tools + bound MCP servers + bound Skills
    bound_tool_ids = [t.id for t in (agent_obj.tools or []) if t.is_enabled]
    tool_registry = await build_tool_registry_from_db(db, tool_ids=bound_tool_ids)
    bound_mcp_servers = [s for s in (agent_obj.mcp_servers or []) if s.is_enabled]
    if bound_mcp_servers:
        await attach_mcp_tools_to_registry(
            tool_registry,
            bound_mcp_servers,
            tool_filters=agent_obj.mcp_tool_filters or {},
        )
    bound_skill_names = list(agent_obj.skill_names or [])
    skills_prompt = attach_skills_to_agent_run(
        bound_skill_names, settings.resolved_skills_dir, tool_registry
    )

    shared_memory_dir = (
        settings.workspaces_dir / f"chat-{conversation_id}" / ".agan" / "shared"
    ).resolve()
    mem_cfg = agent_obj.memory_config or {}
    if agent_obj.loop_engine != "direct_chat":
        memory_prompt = attach_memory_to_agent_run(
            tool_registry,
            memories_root=settings.resolved_memories_dir,
            agent_id=agent_obj.id,
            agent_name=agent_obj.name,
            shared_dir=shared_memory_dir,
            memory_config=mem_cfg,
        )
    elif mem_cfg.get("enable_long_term_memory", True) is not False:
        include_global = mem_cfg.get("include_global_memory", True) is not False
        memory_prompt = build_memory_prompt_block(
            global_dir=(
                resolve_scope_dir(settings.resolved_memories_dir, "global", create=False)
                if include_global
                else None
            ),
            agent_dir=resolve_scope_dir(
                settings.resolved_memories_dir, "agent", scope_id=agent_obj.id, create=False
            ),
            shared_dir=shared_memory_dir,
            agent_name=agent_obj.name,
            memory_mode="read_only",
        )
    else:
        memory_prompt = ""

    enable_subagents = mem_cfg.get("enable_subagents", True) is not False
    available_agents: list[dict[str, str]] = []
    if enable_subagents and agent_obj.loop_engine != "direct_chat":
        other_agents = list(
            (
                await db.scalars(
                    select(AgentConfig)
                    .where(AgentConfig.is_active.is_(True))
                    .order_by(AgentConfig.name.asc())
                )
            ).all()
        )
        available_agents = [
            {"id": a.id, "name": a.name, "description": a.description or ""}
            for a in other_agents
            if a.id != agent_obj.id
        ]

    use_agent_tools = agent_obj.loop_engine != "direct_chat" and (
        len(tool_registry.list_tools()) > 0 or enable_subagents
    )

    history_rows = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    saved_user_msg: ChatMessage | None = None
    auto_title_prompt = ""
    if payload.regenerate:
        # Remove the latest non-compact assistant reply if present
        non_compact = [r for r in history_rows if r.role != "compact"]
        if non_compact and non_compact[-1].role == "assistant":
            target_del = non_compact[-1]
            await db.delete(target_del)
            history_rows = [r for r in history_rows if r.id != target_del.id]
            conv.message_count = max(0, conv.message_count - 1)
    else:
        content_clean = payload.content.strip()
        if not content_clean:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Message content cannot be empty."
            )
        user_msg = ChatMessage(
            conversation_id=conversation_id,
            role="user",
            content=content_clean,
        )
        db.add(user_msg)
        await db.flush()
        await db.refresh(user_msg)
        history_rows.append(user_msg)
        saved_user_msg = user_msg
        conv.message_count = sum(1 for r in history_rows if r.role != "compact")

        if conv.title.strip().lower() in DEFAULT_TITLES or conv.message_count == 1:
            conv.title = _derive_title(content_clean)
            auto_title_prompt = content_clean

    conv.updated_at = datetime.now(UTC)
    await db.commit()

    sys_parts = [
        p.strip()
        for p in (agent_obj.system_prompt, memory_prompt, skills_prompt, conv.system_prompt)
        if p and p.strip()
    ]
    effective_sys = "\n\n".join(sys_parts)
    llm_messages = _build_llm_messages(conv, history_rows, effective_sys=effective_sys)

    effective_temp = (
        conv.temperature
        if conv.temperature is not None
        else (agent_obj.temperature if agent_obj.temperature is not None else model_obj.temperature)
    )
    runtime_cfg = LLMModelConfig(
        name=model_obj.name,
        base_url=model_obj.base_url,
        api_key=secret_box.decrypt(model_obj.api_key_encrypted),
        model_id=model_obj.model_id,
        context_window=model_obj.context_window,
        max_output_tokens=model_obj.max_output_tokens,
        temperature=effective_temp,
        input_price_per_1m=model_obj.input_price_per_1m,
        output_price_per_1m=model_obj.output_price_per_1m,
        extra_params=model_obj.extra_params or {},
    )

    mem_cfg = agent_obj.memory_config or {}
    compact_ratio = float(mem_cfg.get("context_compact_ratio", settings.default_compact_ratio))
    keep_recent_messages = int(mem_cfg.get("keep_recent_messages", 10))

    spec = ChatRunSpec(
        conversation_id=conversation_id,
        title=conv.title,
        agent_name=agent_obj.name,
        model_db_id=model_obj.id,
        runtime_cfg=runtime_cfg,
        llm_messages=llm_messages,
        tool_registry=tool_registry,
        use_agent_tools=use_agent_tools,
        sandbox_mode=agent_obj.sandbox_mode,
        sandbox_image=agent_obj.sandbox_image,
        max_tool_rounds=min(int((agent_obj.guardrail_config or {}).get("max_steps", 8)), 15),
        user_message=(
            _to_msg_dto(saved_user_msg).model_dump(mode="json") if saved_user_msg else None
        ),
        compact_ratio=compact_ratio,
        keep_recent_messages=keep_recent_messages,
        shared_memory_dir=shared_memory_dir,
        parent_agent_id=agent_obj.id,
        enable_subagents=enable_subagents,
        available_agents=available_agents,
        auto_title_prompt=auto_title_prompt,
    )

    run, initial_q = chat_run_manager.start_run(spec)

    async def event_stream() -> AsyncIterator[str]:
        async for evt in chat_run_manager.stream_from_queue(run, initial_q):
            yield _sse_line(evt)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
