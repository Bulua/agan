from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class SessionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    SOLVED = "solved"
    FAILED = "failed"
    STOPPED = "stopped"


class SandboxMode(StrEnum):
    DOCKER = "docker"
    SIMULATED = "simulated"


class SolveSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted CTF solving session linking challenge brief, LLM model, loop engine, and sandbox."""

    __tablename__ = "solve_sessions"

    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="misc", index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_info: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    attachments: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    flag_regex: Mapped[str] = mapped_column(
        String(256), nullable=False, default=r"flag\{[^}\s]+\}"
    )

    chat_model_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    loop_engine: Mapped[str] = mapped_column(String(32), nullable=False, default="react")
    sandbox_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SandboxMode.SIMULATED.value
    )
    sandbox_image: Mapped[str] = mapped_column(
        String(200), nullable=False, default="agan-ctf-sandbox:latest"
    )
    sandbox_container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    system_prompt_override: Mapped[str] = mapped_column(Text, nullable=False, default="")
    guardrail_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SessionStatus.PENDING.value, index=True
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    detected_flag: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only full-chain observability trace log for replay & Writeup generation."""

    __tablename__ = "session_events"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("solve_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, default="main")
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SessionScratchpad(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Pinned short-term working memory per solving session."""

    __tablename__ = "session_scratchpads"

    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("solve_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    known_clues: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    endpoints_ports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    vulnerabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    ruled_out: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    notes_md: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Writeup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Generated Markdown CTF Writeup derived from a session's full trace."""

    __tablename__ = "writeups"

    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("solve_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_used: Mapped[str] = mapped_column(String(200), nullable=False, default="")
