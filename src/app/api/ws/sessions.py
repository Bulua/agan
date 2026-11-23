from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.events import event_bus
from app.models.session import SessionEvent, SolveSession

ws_router = APIRouter()


@ws_router.websocket("/ws/sessions/{session_id}")
async def session_trace_ws(
    websocket: WebSocket,
    session_id: str,
    after_seq: int = Query(default=0, ge=0),
) -> None:
    """Stream full-chain TraceEvents for a solve session with DB backlog replay."""
    async with async_session_factory() as db:
        session_obj = await db.get(SolveSession, session_id)
        if session_obj is None:
            await websocket.close(code=4404, reason="Session not found")
            return

        backlog_rows = list(
            (
                await db.execute(
                    select(SessionEvent)
                    .where(
                        SessionEvent.session_id == session_id,
                        SessionEvent.seq_id > after_seq,
                    )
                    .order_by(SessionEvent.seq_id.asc())
                )
            )
            .scalars()
            .all()
        )

    await websocket.accept()

    last_sent_seq = after_seq
    for ev in backlog_rows:
        msg = {
            "seq_id": ev.seq_id,
            "session_id": ev.session_id,
            "step_index": ev.step_index,
            "agent_name": ev.agent_name,
            "event_type": ev.event_type,
            "timestamp": ev.created_at.isoformat(),
            "usage": (
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
            "payload": ev.payload or {},
        }
        await websocket.send_json(msg)
        last_sent_seq = max(last_sent_seq, ev.seq_id)

    async def forward_live_events() -> None:
        async for ev in event_bus.subscribe(session_id, after_seq=last_sent_seq):
            await websocket.send_json(
                {
                    "seq_id": ev.seq_id,
                    "session_id": ev.session_id,
                    "step_index": ev.step_index,
                    "agent_name": ev.agent_name,
                    "event_type": ev.event_type.value,
                    "timestamp": ev.timestamp.isoformat(),
                    "usage": ev.usage.model_dump() if ev.usage else None,
                    "payload": ev.payload,
                }
            )

    forward_task = asyncio.create_task(forward_live_events())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        forward_task.cancel()
        try:
            await forward_task
        except asyncio.CancelledError:
            pass
