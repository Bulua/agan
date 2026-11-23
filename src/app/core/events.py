from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TraceEventType(StrEnum):
    STATUS = "status"
    THOUGHT = "thought"
    PLAN_UPDATE = "plan_update"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    RAG_RETRIEVAL = "rag_retrieval"
    SKILL_LOADED = "skill_loaded"
    MCP_CALL = "mcp_call"
    REFLEXION = "reflexion"
    SUBAGENT_DISPATCH = "subagent_dispatch"
    HUMAN_STEERING = "human_steering"
    FLAG_DETECTED = "flag_detected"
    GUARDRAIL = "guardrail"
    TOKEN_USAGE = "token_usage"
    SCRATCHPAD_UPDATE = "scratchpad_update"
    ERROR = "error"


class TraceUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class TraceEvent(BaseModel):
    seq_id: int
    session_id: str
    step_index: int = 0
    agent_name: str = "main"
    event_type: TraceEventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    usage: TraceUsage | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


EventListener = Callable[[TraceEvent], Coroutine[Any, Any, None]]


class SessionEventBus:
    """In-memory async EventBus with monotonic per-session seq_id and replay buffer."""

    def __init__(self, max_history_per_session: int = 2000) -> None:
        self._max_history = max_history_per_session
        self._seq_counters: dict[str, int] = defaultdict(int)
        self._history: dict[str, list[TraceEvent]] = defaultdict(list)
        self._subscribers: dict[str, set[asyncio.Queue[TraceEvent]]] = defaultdict(set)
        self._hooks: list[EventListener] = []
        self._lock = asyncio.Lock()

    def add_hook(self, listener: EventListener) -> None:
        """Register a persistent async hook (e.g., DB event persister in Stage 3)."""
        self._hooks.append(listener)

    async def emit(
        self,
        session_id: str,
        event_type: TraceEventType,
        payload: dict[str, Any] | None = None,
        *,
        step_index: int = 0,
        agent_name: str = "main",
        usage: TraceUsage | None = None,
    ) -> TraceEvent:
        async with self._lock:
            self._seq_counters[session_id] += 1
            seq_id = self._seq_counters[session_id]
            event = TraceEvent(
                seq_id=seq_id,
                session_id=session_id,
                step_index=step_index,
                agent_name=agent_name,
                event_type=event_type,
                usage=usage,
                payload=payload or {},
            )
            hist = self._history[session_id]
            hist.append(event)
            if len(hist) > self._max_history:
                del hist[: len(hist) - self._max_history]
            queues = list(self._subscribers.get(session_id, ()))

        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

        for hook in self._hooks:
            await hook(event)

        return event

    def get_history(self, session_id: str, after_seq: int = 0) -> list[TraceEvent]:
        return [e for e in self._history.get(session_id, []) if e.seq_id > after_seq]

    async def subscribe(
        self, session_id: str, after_seq: int = 0
    ) -> AsyncIterator[TraceEvent]:
        """Yield missed events (seq_id > after_seq), then stream live events."""
        queue: asyncio.Queue[TraceEvent] = asyncio.Queue(maxsize=500)
        async with self._lock:
            backlog = [e for e in self._history.get(session_id, []) if e.seq_id > after_seq]
            self._subscribers[session_id].add(queue)
            last_sent = backlog[-1].seq_id if backlog else after_seq

        try:
            for item in backlog:
                yield item
            while True:
                event = await queue.get()
                if event.seq_id > last_sent:
                    last_sent = event.seq_id
                    yield event
        finally:
            async with self._lock:
                self._subscribers[session_id].discard(queue)

    def clear_session(self, session_id: str) -> None:
        self._history.pop(session_id, None)
        self._seq_counters.pop(session_id, None)
        self._subscribers.pop(session_id, None)


event_bus = SessionEventBus()
