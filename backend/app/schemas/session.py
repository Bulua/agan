from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.session import SandboxMode, SessionStatus
from app.schemas.common import PaginatedResponse


class GuardrailInput(BaseModel):
    max_steps: int = Field(default=25, ge=1, le=200)
    step_timeout_sec: int = Field(default=120, ge=10, le=600)
    token_budget: int = Field(default=200_000, ge=0, le=50_000_000)
    cost_budget_usd: float = Field(default=5.0, ge=0.0, le=500.0)
    repeat_threshold: int = Field(default=3, ge=2, le=10)


class ScratchpadDTO(BaseModel):
    known_clues: list[str] = Field(default_factory=list)
    endpoints_ports: list[str] = Field(default_factory=list)
    vulnerabilities: list[str] = Field(default_factory=list)
    ruled_out: list[str] = Field(default_factory=list)
    notes_md: str = ""


class WriteupDTO(BaseModel):
    id: str
    session_id: str
    title: str
    content_md: str
    model_used: str
    created_at: datetime
    updated_at: datetime


class SessionCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    category: str = Field(default="misc", max_length=32)
    description: str = Field(default="", max_length=20000)
    target_info: dict[str, Any] = Field(default_factory=dict)
    flag_regex: str = Field(default=r"flag\{[^}\s]+\}", max_length=256)
    chat_model_id: str = Field(..., min_length=1)
    loop_engine: str = Field(default="react")
    sandbox_mode: SandboxMode = SandboxMode.SIMULATED
    sandbox_image: str = Field(default="agan-ctf-sandbox:latest", max_length=200)
    system_prompt_override: str = Field(default="", max_length=20000)
    guardrail_config: GuardrailInput = Field(default_factory=GuardrailInput)


class SessionSummaryResponse(BaseModel):
    id: str
    title: str
    category: str
    chat_model_id: str | None
    model_name_snapshot: str
    loop_engine: str
    sandbox_mode: SandboxMode
    sandbox_image: str
    status: SessionStatus
    current_step: int
    max_steps: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost_usd: float
    detected_flag: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    is_live: bool = False


class SessionDetailResponse(SessionSummaryResponse):
    description: str
    target_info: dict[str, Any]
    attachments: list[str]
    flag_regex: str
    sandbox_container_id: str | None
    system_prompt_override: str
    guardrail_config: dict[str, Any]
    summary: str
    error_message: str
    scratchpad: ScratchpadDTO
    writeup: WriteupDTO | None = None


class SessionListResponse(PaginatedResponse[SessionSummaryResponse]):
    status_counts: dict[str, int] = Field(default_factory=dict)


class SessionEventDTO(BaseModel):
    seq_id: int
    session_id: str
    step_index: int
    agent_name: str
    event_type: str
    timestamp: datetime
    usage: dict[str, Any] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SteerRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    immediate_resume: bool = True


class StopRequest(BaseModel):
    keep_container: bool = False


class ManualExecRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=4000)
    timeout: int = Field(default=30, ge=1, le=180)


class ManualExecResponse(BaseModel):
    command: str
    output: str
    exit_code: int
    timed_out: bool
    truncated: bool
    cwd: str | None = None
    duration_ms: int = 0


class GenerateWriteupRequest(BaseModel):
    model_id: str | None = None
    language: str = Field(default="zh-CN")
