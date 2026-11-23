from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class LLMModelConfig(BaseModel):
    """Runtime configuration for an OpenAI-compatible Chat LLM."""

    name: str = "default-chat"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model_id: str = "gpt-4o"
    context_window: int = 128_000
    max_output_tokens: int = 4096
    temperature: float = 0.2
    input_price_per_1m: float = 0.0
    output_price_per_1m: float = 0.0
    timeout_sec: float = 120.0
    max_retries: int = 3
    extra_params: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: str = "{}"


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)

    def to_openai_dict(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.name:
            msg["name"] = self.name
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": (
                            tc.raw_arguments
                            if tc.raw_arguments and tc.raw_arguments != "{}"
                            else json.dumps(tc.arguments, ensure_ascii=False)
                        ),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: TokenUsage) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cost_usd += other.cost_usd


class LLMResponse(BaseModel):
    content: str = ""
    reasoning_content: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = 0
    model: str = ""
