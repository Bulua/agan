from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from app.engine.llm.types import (
    LLMMessage,
    LLMModelConfig,
    LLMResponse,
    TokenUsage,
    ToolCallRequest,
)


class BaseChatClient(ABC):
    """Abstract async chat client interface."""

    config: LLMModelConfig

    @abstractmethod
    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send messages to the LLM and return a structured response."""

    async def close(self) -> None:  # noqa: B027
        """Release underlying network connections."""


class OpenAICompatChatClient(BaseChatClient):
    """OpenAI-protocol compatible async chat client with retries and cost accounting."""

    def __init__(self, config: LLMModelConfig) -> None:
        self.config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key or "sk-placeholder",
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout_sec,
            max_retries=0,  # Managed explicitly for transparent backoff control
        )

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        in_cost = (prompt_tokens / 1_000_000) * self.config.input_price_per_1m
        out_cost = (completion_tokens / 1_000_000) * self.config.output_price_per_1m
        return round(in_cost + out_cost, 6)

    @staticmethod
    def _parse_tool_arguments(raw_args: str) -> dict[str, Any]:
        if not raw_args:
            return {}
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except json.JSONDecodeError:
            return {"__parse_error__": raw_args}

    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": [m.to_openai_dict() for m in messages],
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_output_tokens,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format
        if self.config.extra_params:
            payload["extra_body"] = self.config.extra_params

        last_err: Exception | None = None
        for attempt in range(max(1, self.config.max_retries)):
            start = time.monotonic()
            try:
                resp = await self._client.chat.completions.create(**payload)
                latency_ms = int((time.monotonic() - start) * 1000)
                return self._normalize_response(resp, latency_ms)
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_err = exc
            except APIStatusError as exc:
                if exc.status_code >= 500:
                    last_err = exc
                else:
                    raise
            if attempt < self.config.max_retries - 1:
                await asyncio.sleep(min(15.0, 1.5 * (2**attempt)))

        assert last_err is not None
        raise last_err

    def _normalize_response(self, resp: Any, latency_ms: int) -> LLMResponse:
        choice = resp.choices[0]
        msg = choice.message

        parsed_tool_calls: list[ToolCallRequest] = []
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                raw_args = tc.function.arguments or "{}"
                parsed_tool_calls.append(
                    ToolCallRequest(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=self._parse_tool_arguments(raw_args),
                        raw_arguments=raw_args,
                    )
                )

        usage_obj = getattr(resp, "usage", None)
        p_tok = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        c_tok = int(getattr(usage_obj, "completion_tokens", 0) or 0)
        t_tok = int(getattr(usage_obj, "total_tokens", p_tok + c_tok) or (p_tok + c_tok))

        # Support reasoning models (e.g., DeepSeek-R1, Doubao-Seed, Qwen)
        reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)

        return LLMResponse(
            content=msg.content or "",
            reasoning_content=reasoning,
            tool_calls=parsed_tool_calls,
            finish_reason=choice.finish_reason,
            usage=TokenUsage(
                prompt_tokens=p_tok,
                completion_tokens=c_tok,
                total_tokens=t_tok,
                cost_usd=self._estimate_cost(p_tok, c_tok),
            ),
            latency_ms=latency_ms,
            model=getattr(resp, "model", self.config.model_id) or self.config.model_id,
        )

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream content, reasoning, and tool-call deltas over OpenAI-compatible SSE."""
        payload: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": [m.to_openai_dict() for m in messages],
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        if self.config.extra_params:
            payload["extra_body"] = self.config.extra_params

        start = time.monotonic()
        try:
            stream = await self._client.chat.completions.create(**payload)
        except APIStatusError as exc:
            # Some older OpenAI-compatible gateways reject `stream_options`
            if exc.status_code in (400, 422) and "stream_options" in payload:
                payload.pop("stream_options", None)
                stream = await self._client.chat.completions.create(**payload)
            else:
                raise

        resolved_model = self.config.model_id
        finish_reason: str | None = None
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_acc: dict[int, dict[str, Any]] = {}

        async for chunk in stream:
            if getattr(chunk, "model", None):
                resolved_model = chunk.model
            usage_obj = getattr(chunk, "usage", None)
            if usage_obj is not None:
                prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
                total_tokens = int(
                    getattr(usage_obj, "total_tokens", prompt_tokens + completion_tokens)
                    or (prompt_tokens + completion_tokens)
                )

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason

            delta = choice.delta
            reasoning_delta = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
            )
            if reasoning_delta:
                reasoning_parts.append(str(reasoning_delta))
                yield {"type": "reasoning_delta", "delta": str(reasoning_delta)}

            content_delta = getattr(delta, "content", None)
            if content_delta:
                content_parts.append(str(content_delta))
                yield {"type": "content_delta", "delta": str(content_delta)}

            tc_deltas = getattr(delta, "tool_calls", None) or []
            for tc_d in tc_deltas:
                raw_idx = getattr(tc_d, "index", None)
                idx = int(raw_idx) if raw_idx is not None else len(tool_call_acc)
                entry = tool_call_acc.setdefault(
                    idx, {"id": "", "name": "", "arguments_parts": []}
                )
                if getattr(tc_d, "id", None):
                    entry["id"] = str(tc_d.id)
                fn = getattr(tc_d, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        entry["name"] += str(fn.name)
                    if getattr(fn, "arguments", None):
                        entry["arguments_parts"].append(str(fn.arguments))

        assembled_tool_calls: list[dict[str, Any]] = []
        for idx in sorted(tool_call_acc.keys()):
            entry = tool_call_acc[idx]
            if not entry["name"]:
                continue
            raw_args = "".join(entry["arguments_parts"])
            tc_req = ToolCallRequest(
                id=entry["id"] or f"call_{idx}",
                name=entry["name"],
                arguments=self._parse_tool_arguments(raw_args),
                raw_arguments=raw_args,
            )
            assembled_tool_calls.append(tc_req.model_dump())

        latency_ms = int((time.monotonic() - start) * 1000)
        yield {
            "type": "done",
            "model": resolved_model,
            "finish_reason": finish_reason,
            "latency_ms": latency_ms,
            "content": "".join(content_parts),
            "reasoning_content": "".join(reasoning_parts),
            "tool_calls": assembled_tool_calls,
            "usage": TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=self._estimate_cost(prompt_tokens, completion_tokens),
            ).model_dump(),
        }

    async def close(self) -> None:
        await self._client.close()
