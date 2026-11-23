from __future__ import annotations

import time
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.engine.llm.types import LLMModelConfig, TokenUsage


class EmbeddingResult(BaseModel):
    vectors: list[list[float]] = Field(default_factory=list)
    dimensions: int = 0
    model: str = ""
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = 0


class OpenAICompatEmbeddingClient:
    """OpenAI-protocol compatible async embedding client."""

    def __init__(self, config: LLMModelConfig) -> None:
        self.config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key or "sk-placeholder",
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout_sec,
            max_retries=1,
        )

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        start = time.monotonic()
        kwargs: dict[str, Any] = {
            "model": self.config.model_id,
            "input": texts,
        }
        if self.config.extra_params:
            kwargs["extra_body"] = self.config.extra_params

        resp = await self._client.embeddings.create(**kwargs)
        latency_ms = int((time.monotonic() - start) * 1000)

        ordered = sorted(resp.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in ordered]
        dims = len(vectors[0]) if vectors else 0

        usage_obj = getattr(resp, "usage", None)
        p_tok = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        t_tok = int(getattr(usage_obj, "total_tokens", p_tok) or p_tok)
        cost = round((p_tok / 1_000_000) * self.config.input_price_per_1m, 6)

        return EmbeddingResult(
            vectors=vectors,
            dimensions=dims,
            model=getattr(resp, "model", self.config.model_id) or self.config.model_id,
            usage=TokenUsage(prompt_tokens=p_tok, total_tokens=t_tok, cost_usd=cost),
            latency_ms=latency_ms,
        )

    async def close(self) -> None:
        await self._client.close()
