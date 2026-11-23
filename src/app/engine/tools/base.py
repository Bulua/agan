from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.sandbox.base import BaseSandbox


class ToolResult(BaseModel):
    success: bool = True
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ToolExecutionContext:
    """Runtime handles injected into tools during execution."""

    session_id: str
    sandbox: BaseSandbox
    scratchpad: WorkingScratchpad
    window_name: str = "ctf_main"
    flag_regex: str | None = None
    on_flag_submitted: Callable[[str], Awaitable[dict[str, Any]]] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """Abstract tool interface exporting OpenAI function-calling schema."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    requires_sandbox: bool = True

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    @abstractmethod
    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        """Execute tool logic and return a normalized ToolResult."""
