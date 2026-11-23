from __future__ import annotations

from pydantic import BaseModel

from app.engine.llm.types import TokenUsage


class GuardrailConfig(BaseModel):
    max_steps: int = 30
    step_timeout_sec: int = 120
    token_budget: int = 200_000
    cost_budget_usd: float = 5.0
    repeat_threshold: int = 3
    repeat_similarity: float = 0.82
    context_compact_ratio: float = 0.75
    keep_recent_messages: int = 10


class GuardrailViolation(Exception):
    """Raised when a hard safety/budget guardrail trips."""

    def __init__(self, reason: str, code: str = "guardrail_violation") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


class BudgetTracker:
    """Tracks step count, token consumption, and USD cost against configured caps."""

    def __init__(self, config: GuardrailConfig) -> None:
        self.config = config
        self.steps: int = 0
        self.usage = TokenUsage()

    def record_step(self) -> int:
        self.steps += 1
        return self.steps

    def record_usage(self, usage: TokenUsage) -> None:
        self.usage.add(usage)

    def check_limits(self) -> None:
        if self.steps >= self.config.max_steps:
            raise GuardrailViolation(
                f"Reached maximum step limit ({self.config.max_steps} steps).",
                code="max_steps_exceeded",
            )
        if self.config.token_budget > 0 and self.usage.total_tokens >= self.config.token_budget:
            raise GuardrailViolation(
                f"Token budget circuit breaker tripped "
                f"({self.usage.total_tokens:,} / {self.config.token_budget:,} tokens).",
                code="token_budget_exceeded",
            )
        if self.config.cost_budget_usd > 0 and self.usage.cost_usd >= self.config.cost_budget_usd:
            raise GuardrailViolation(
                f"Cost budget circuit breaker tripped "
                f"(${self.usage.cost_usd:.4f} / ${self.config.cost_budget_usd:.4f}).",
                code="cost_budget_exceeded",
            )
