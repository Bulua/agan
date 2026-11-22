from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.mcp import MCPTransport
from app.schemas.common import PaginatedResponse


class MCPDiscoveredTool(BaseModel):
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = Field(default_factory=dict)


class MCPServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    transport: MCPTransport = MCPTransport.STDIO
    command: str = Field(default="", max_length=255)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = Field(default="", max_length=500)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_sec: int = Field(default=30, ge=3, le=180)
    is_enabled: bool = True


class MCPServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    transport: MCPTransport | None = None
    command: str | None = Field(default=None, max_length=255)
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = Field(default=None, max_length=500)
    headers: dict[str, str] | None = None
    timeout_sec: int | None = Field(default=None, ge=3, le=180)
    is_enabled: bool | None = None


class MCPServerResponse(BaseModel):
    id: str
    name: str
    description: str
    transport: MCPTransport
    command: str
    args: list[str] = Field(default_factory=list)
    env_masked: dict[str, str] = Field(default_factory=dict)
    has_env: bool = False
    url: str
    headers_masked: dict[str, str] = Field(default_factory=dict)
    has_headers: bool = False
    timeout_sec: int
    is_enabled: bool
    last_status: str
    last_error: str
    last_latency_ms: int | None
    last_synced_at: datetime | None
    discovered_tools: list[MCPDiscoveredTool] = Field(default_factory=list)
    tool_count: int = 0
    created_at: datetime
    updated_at: datetime


class MCPServerListResponse(PaginatedResponse[MCPServerResponse]):
    transport_counts: dict[str, int] = Field(default_factory=dict)


class MCPToolCallTestRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class MCPToolCallTestResponse(BaseModel):
    success: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPImportRequest(BaseModel):
    """Accepts standard `{"mcpServers": {name: {...}}}` JSON configuration."""

    mcpServers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    overwrite_existing: bool = False


class MCPImportResponse(BaseModel):
    created: int
    updated: int
    skipped: int
    items: list[MCPServerResponse] = Field(default_factory=list)
