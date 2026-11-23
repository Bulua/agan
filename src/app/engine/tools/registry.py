from __future__ import annotations

from typing import Any

from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ToolRegistry:
    """Hot-reloadable registry of built-in, custom, and MCP-discovered tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._enabled: dict[str, bool] = {}

    def register(self, tool: BaseTool, *, enabled: bool = True) -> None:
        self._tools[tool.name] = tool
        self._enabled[tool.name] = enabled

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._enabled.pop(name, None)

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._tools:
            raise KeyError(f"Tool not registered: {name}")
        self._enabled[name] = enabled

    def get(self, name: str) -> BaseTool | None:
        if not self._enabled.get(name, False):
            return None
        return self._tools.get(name)

    def list_tools(self, *, only_enabled: bool = True) -> list[BaseTool]:
        return [
            tool
            for name, tool in self._tools.items()
            if not only_enabled or self._enabled.get(name, False)
        ]

    def get_openai_schemas(self, allowed_names: set[str] | None = None) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for tool in self.list_tools(only_enabled=True):
            if allowed_names is not None and tool.name not in allowed_names:
                continue
            schemas.append(tool.to_openai_tool())
        return schemas

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        ctx: ToolExecutionContext,
    ) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{name}' is unknown or currently disabled.",
            )
        if "__parse_error__" in arguments:
            return ToolResult(
                success=False,
                output="",
                error=f"Invalid JSON arguments for '{name}': {arguments['__parse_error__']}",
            )
        try:
            return await tool.execute(arguments, ctx)
        except Exception as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{name}' execution raised {type(exc).__name__}: {exc}",
            )


def create_default_tool_registry() -> ToolRegistry:
    """Instantiate a registry pre-populated with all built-in tools."""
    from app.engine.tools.builtin.agent_tools import ScratchpadUpdateTool, SubmitFlagTool
    from app.engine.tools.builtin.sandbox_tools import (
        BashExecTool,
        EditFileTool,
        GrepSearchTool,
        HttpRequestTool,
        ListFilesTool,
        PythonExecTool,
        ReadFileTool,
        WriteFileTool,
    )
    from app.engine.tools.builtin.skill_tools import CreateSkillTool, EditSkillFileTool
    from app.engine.tools.builtin.web_tools import WebFetchTool, WebSearchTool

    registry = ToolRegistry()
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListFilesTool(),
        GrepSearchTool(),
        BashExecTool(),
        PythonExecTool(),
        WebSearchTool(),
        WebFetchTool(),
        HttpRequestTool(),
        CreateSkillTool(),
        EditSkillFileTool(),
        ScratchpadUpdateTool(),
        SubmitFlagTool(),
    ):
        registry.register(tool, enabled=True)
    return registry
