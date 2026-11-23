from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.engine.llm.types import LLMModelConfig


class RerankItem(BaseModel):
    index: int
    score: float
    document: str = ""


class RerankResult(BaseModel):
    items: list[RerankItem] = Field(default_factory=list)
    model: str = ""
    latency_ms: int = 0


class RerankClient:
    """Universal Rerank client compatible with Jina, Cohere, SiliconFlow, BGE, and Xinference."""

    def __init__(self, config: LLMModelConfig) -> None:
        self.config = config
        self._client = httpx.AsyncClient(timeout=config.timeout_sec)

    def _resolve_endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/rerank"):
            return base
        return f"{base}/rerank"

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
    ) -> RerankResult:
        endpoint = self._resolve_endpoint()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload: dict[str, Any] = {
            "model": self.config.model_id,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            payload["top_n"] = top_n
        if self.config.extra_params:
            payload.update(self.config.extra_params)

        start = time.monotonic()
        resp = await self._client.post(endpoint, headers=headers, json=payload)
        latency_ms = int((time.monotonic() - start) * 1000)
        resp.raise_for_status()
        data = resp.json()

        raw_results = data.get("results") or data.get("data") or []
        items: list[RerankItem] = []
        for entry in raw_results:
            idx = int(entry.get("index", 0))
            score = float(entry.get("relevance_score", entry.get("score", 0.0)))
            doc_text = ""
            if isinstance(entry.get("document"), dict):
                doc_text = str(entry["document"].get("text", ""))
            elif isinstance(entry.get("document"), str):
                doc_text = entry["document"]
            elif 0 <= idx < len(documents):
                doc_text = documents[idx]
            items.append(RerankItem(index=idx, score=score, document=doc_text))

        items.sort(key=lambda x: x.score, reverse=True)
        return RerankResult(
            items=items,
            model=str(data.get("model") or self.config.model_id),
            latency_ms=latency_ms,
        )

    async def close(self) -> None:
        await self._client.aclose()
