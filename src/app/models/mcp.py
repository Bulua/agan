from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class MCPTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


class MCPServer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted Model Context Protocol (MCP) server configuration and discovered tool cache."""

    __tablename__ = "mcp_servers"

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    transport: Mapped[str] = mapped_column(
        String(24), nullable=False, default=MCPTransport.STDIO.value, index=True
    )

    # Stdio transport fields
    command: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    args: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    env_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # HTTP / SSE transport fields
    url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    headers_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")

    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Cached discovery & health status
    last_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_tools: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
