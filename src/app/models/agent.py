from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, Column, Float, ForeignKey, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.capability import ToolConfig
from app.models.mcp import MCPServer


class AgentRole(StrEnum):
    GENERAL = "general"
    ROUTER = "router"
    WEB = "web"
    PWN = "pwn"
    CRYPTO = "crypto"
    REVERSE = "reverse"
    MISC = "misc"


agent_tools_table = Table(
    "agent_tools",
    Base.metadata,
    Column(
        "agent_id",
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tool_id",
        String(36),
        ForeignKey("tools.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

agent_mcp_servers_table = Table(
    "agent_mcp_servers",
    Base.metadata,
    Column(
        "agent_id",
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "mcp_server_id",
        String(36),
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class AgentConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Configurable specialist or general AI Agent binding LLM, Prompt, Loop, and Tools."""

    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AgentRole.GENERAL.value, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    chat_model_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    loop_engine: Mapped[str] = mapped_column(String(32), nullable=False, default="react")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)

    sandbox_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="simulated")
    sandbox_image: Mapped[str] = mapped_column(
        String(200), nullable=False, default="agan-ctf-sandbox:latest"
    )
    guardrail_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    memory_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    mcp_tool_filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    skill_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    tools: Mapped[list[ToolConfig]] = relationship(
        ToolConfig,
        secondary=agent_tools_table,
        lazy="selectin",
    )
    mcp_servers: Mapped[list[MCPServer]] = relationship(
        MCPServer,
        secondary=agent_mcp_servers_table,
        lazy="selectin",
    )
