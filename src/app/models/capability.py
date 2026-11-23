from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ToolType(StrEnum):
    BUILTIN = "builtin"
    CUSTOM_HTTP = "custom_http"
    CUSTOM_PYTHON = "custom_python"


class ToolConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted tool registry entry for built-in and user-defined custom tools."""

    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ToolType.BUILTIN.value, index=True
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="sandbox")
    parameters_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    implementation_config: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    requires_sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
