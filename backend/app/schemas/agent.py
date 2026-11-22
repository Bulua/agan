from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.agent import AgentRole
from app.schemas.common import PaginatedResponse


class AgentToolBrief(BaseModel):
    id: str
    name: str
    display_name: str
    tool_type: str
    is_enabled: bool


class AgentMcpBrief(BaseModel):
    id: str
    name: str
    transport: str
    is_enabled: bool
    last_status: str = "unknown"
    tool_count: int = 0


class AgentSkillBrief(BaseModel):
    name: str
    title: str
    description: str = ""
    file_count: int = 0
    exists: bool = True


class AgentGuardrails(BaseModel):
    max_steps: int = Field(default=20, ge=1, le=200)
    step_timeout_sec: int = Field(default=120, ge=10, le=600)
    token_budget: int = Field(default=200_000, ge=0, le=50_000_000)
    cost_budget_usd: float = Field(default=5.0, ge=0.0, le=500.0)
    repeat_threshold: int = Field(default=3, ge=2, le=10)


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    role: AgentRole = AgentRole.GENERAL
    description: str = Field(default="", max_length=2000)
    chat_model_id: str = Field(..., min_length=1)
    loop_engine: str = Field(default="react")
    system_prompt: str = Field(default="", max_length=20000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    sandbox_mode: str = Field(default="simulated")
    sandbox_image: str = Field(default="agan-ctf-sandbox:latest", max_length=200)
    tool_ids: list[str] = Field(default_factory=list)
    mcp_server_ids: list[str] = Field(default_factory=list)
    mcp_tool_filters: dict[str, list[str] | None] = Field(default_factory=dict)
    skill_names: list[str] = Field(default_factory=list)
    guardrail_config: AgentGuardrails = Field(default_factory=AgentGuardrails)
    memory_config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    is_default: bool = False


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: AgentRole | None = None
    description: str | None = Field(default=None, max_length=2000)
    chat_model_id: str | None = None
    loop_engine: str | None = None
    system_prompt: str | None = Field(default=None, max_length=20000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    sandbox_mode: str | None = None
    sandbox_image: str | None = Field(default=None, max_length=200)
    tool_ids: list[str] | None = None
    mcp_server_ids: list[str] | None = None
    mcp_tool_filters: dict[str, list[str] | None] | None = None
    skill_names: list[str] | None = None
    guardrail_config: AgentGuardrails | None = None
    memory_config: dict[str, Any] | None = None
    is_active: bool | None = None
    is_default: bool | None = None


class AgentResponse(BaseModel):
    id: str
    name: str
    role: AgentRole
    description: str
    chat_model_id: str | None
    chat_model_name: str = ""
    loop_engine: str
    system_prompt: str
    temperature: float | None
    sandbox_mode: str
    sandbox_image: str
    tool_ids: list[str] = Field(default_factory=list)
    tools: list[AgentToolBrief] = Field(default_factory=list)
    mcp_server_ids: list[str] = Field(default_factory=list)
    mcp_servers: list[AgentMcpBrief] = Field(default_factory=list)
    mcp_tool_filters: dict[str, list[str] | None] = Field(default_factory=dict)
    skill_names: list[str] = Field(default_factory=list)
    skills: list[AgentSkillBrief] = Field(default_factory=list)
    guardrail_config: dict[str, Any] = Field(default_factory=dict)
    memory_config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime


class AgentListResponse(PaginatedResponse[AgentResponse]):
    role_counts: dict[str, int] = Field(default_factory=dict)
