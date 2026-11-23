from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.llm import ModelType
from app.schemas.common import PaginatedResponse


class LLMModelBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    model_type: ModelType = ModelType.CHAT
    provider: str = Field(default="openai_compat", max_length=64)
    base_url: str = Field(..., min_length=1, max_length=512)
    model_id: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)

    context_window: int = Field(default=128_000, ge=256, le=10_000_000)
    max_output_tokens: int = Field(default=4096, ge=1, le=200_000)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)

    input_price_per_1m: float = Field(default=0.0, ge=0.0)
    output_price_per_1m: float = Field(default=0.0, ge=0.0)
    extra_params: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    is_default: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError("base_url must start with http:// or https://")
        return cleaned.rstrip("/")


class LLMModelCreate(LLMModelBase):
    api_key: str = Field(default="", max_length=1024)


class LLMModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    model_type: ModelType | None = None
    provider: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = Field(default=None, max_length=1024)
    model_id: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    context_window: int | None = Field(default=None, ge=256, le=10_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=200_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    input_price_per_1m: float | None = Field(default=None, ge=0.0)
    output_price_per_1m: float | None = Field(default=None, ge=0.0)
    extra_params: dict[str, Any] | None = None
    is_active: bool | None = None
    is_default: bool | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError("base_url must start with http:// or https://")
        return cleaned.rstrip("/")


class LLMModelResponse(LLMModelBase):
    id: str
    api_key_masked: str = ""
    has_api_key: bool = False
    last_test_status: str = "untested"
    last_test_latency_ms: int | None = None
    last_test_message: str = ""
    last_tested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LLMModelTestRequest(BaseModel):
    prompt: str = Field(
        default="Reply concisely with 'OK' and confirm your model name.",
        max_length=2000,
    )
    query: str = Field(
        default="Flask Jinja2 Server-Side Template Injection payload",
        max_length=1000,
    )
    documents: list[str] = Field(
        default_factory=lambda: [
            "Flask Jinja2 SSTI allows remote code execution via {{config.__class__.__init__.__globals__}}.",
            "RSA Wiener attack recovers small private exponent d using continued fractions.",
            "Buffer overflow with NX disabled allows shellcode execution on the stack.",
        ]
    )


class LLMModelUnsavedTestRequest(LLMModelCreate):
    test_input: LLMModelTestRequest = Field(default_factory=LLMModelTestRequest)


class LLMModelTestResponse(BaseModel):
    ok: bool
    model_type: ModelType
    latency_ms: int = 0
    detail: str = ""
    preview: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)


class LLMModelListResponse(PaginatedResponse[LLMModelResponse]):
    category_counts: dict[str, int] = Field(default_factory=dict)


class LLMUsageSummary(BaseModel):
    total_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    by_model: list[dict[str, Any]] = Field(default_factory=list)
