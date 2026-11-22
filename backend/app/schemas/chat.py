from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import PaginatedResponse


class ChatConversationCreate(BaseModel):
    title: str = Field(default="New Chat", min_length=1, max_length=200)
    agent_id: str | None = None
    system_prompt: str = Field(default="", max_length=20000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class ChatConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    agent_id: str | None = None
    system_prompt: str | None = Field(default=None, max_length=20000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class ChatMessageDTO(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    reasoning_content: str = ""
    tool_calls_json: list[dict[str, Any]] = Field(default_factory=list)
    model_id_used: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    created_at: datetime


class ChatConversationSummary(BaseModel):
    id: str
    title: str
    agent_id: str | None
    agent_name_snapshot: str
    model_id: str | None
    model_name_snapshot: str
    system_prompt: str
    temperature: float | None
    message_count: int
    total_tokens: int
    total_cost_usd: float
    is_running: bool = False
    created_at: datetime
    updated_at: datetime


class ChatConversationDetail(ChatConversationSummary):
    messages: list[ChatMessageDTO] = Field(default_factory=list)
    context_tokens_estimate: int = 0
    context_window: int = 128_000
    has_compacted_summary: bool = False


class ChatConversationListResponse(PaginatedResponse[ChatConversationSummary]):
    pass


class SendMessageRequest(BaseModel):
    content: str = Field(default="", max_length=100_000)
    agent_id: str | None = None
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    regenerate: bool = False


class SteerChatRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20_000)


class CompactChatRequest(BaseModel):
    instructions: str = Field(default="", max_length=2000)
    keep_recent: int = Field(default=4, ge=1, le=20)


class CompactChatResponse(BaseModel):
    compacted: bool
    stage: str = "manual"
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    summarized_messages: int = 0
    summary: str = ""
    context_tokens_estimate: int = 0
    context_window: int = 128_000
    compact_message: ChatMessageDTO | None = None
