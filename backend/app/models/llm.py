from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ModelType(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class LLMModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted configuration for Chat LLM, Embedding, and Rerank models."""

    __tablename__ = "llm_models"

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    model_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="openai_compat")
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    context_window: Mapped[int] = mapped_column(Integer, nullable=False, default=128_000)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)

    # USD per 1M tokens for transparent cost accounting
    input_price_per_1m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_price_per_1m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    extra_params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Latest connectivity test diagnostics
    last_test_status: Mapped[str] = mapped_column(String(20), nullable=False, default="untested")
    last_test_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_test_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TokenUsageLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-call token & USD billing log aggregated in dashboard statistics."""

    __tablename__ = "token_usage_logs"

    model_db_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_type: Mapped[str] = mapped_column(String(20), nullable=False, default="chat")
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
