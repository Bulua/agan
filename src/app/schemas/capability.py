from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.capability import ToolType
from app.schemas.common import PaginatedResponse


class ToolBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=150)
    description: str = Field(default="", max_length=2000)
    tool_type: ToolType = ToolType.CUSTOM_HTTP
    category: str = Field(default="custom", max_length=32)
    parameters_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []}
    )
    implementation_config: dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    requires_sandbox: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, val: str) -> str:
        cleaned = val.strip()
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{1,99}", cleaned):
            raise ValueError(
                "Tool name must start with a letter and contain only letters, digits, '_' or '-'."
            )
        return cleaned


class ToolCreate(ToolBase):
    pass


class ToolUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=32)
    parameters_schema: dict[str, Any] | None = None
    implementation_config: dict[str, Any] | None = None
    is_enabled: bool | None = None
    requires_sandbox: bool | None = None


class ToolResponse(ToolBase):
    id: str
    is_builtin: bool
    created_at: datetime
    updated_at: datetime


class ToolListResponse(PaginatedResponse[ToolResponse]):
    type_counts: dict[str, int] = Field(default_factory=dict)


class ToolTestRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolTestResponse(BaseModel):
    success: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
