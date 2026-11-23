from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ChatConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Claude-style multi-turn chat conversation bound to a configurable Agent."""

    __tablename__ = "chat_conversations"

    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New Chat", index=True)
    agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    model_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)

    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    compacted_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    compacted_before_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ChatMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Individual user or assistant turn belonging to a ChatConversation."""

    __tablename__ = "chat_messages"

    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reasoning_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    model_id_used: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
