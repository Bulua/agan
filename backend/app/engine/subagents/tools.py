from __future__ import annotations

from typing import Any

from app.engine.subagents.manager import SubagentCoordinator, SubagentRecord
from app.engine.tools.base import BaseTool, ToolExecutionContext, ToolResult
from app.engine.tools.registry import ToolRegistry


def _format_subagent_report(rec: SubagentRecord) -> str:
    header = (
        f"### Subagent `{rec.subagent_id}` — {rec.role} "
        f"[status={rec.status} | steps={len(rec.steps)} | {rec.duration_ms()}ms]"
    )
    if rec.status == "error":
        return f"{header}\nError: {rec.error}"
    if rec.status == "running":
        return (
            f"{header}\nLaunched in background ({rec.current_action or 'running'}). "
            "Use `wait_subagents` to collect its final report or `send_agent_message` to steer it."
        )
    return f"{header}\n\n{rec.final_output}"


class SpawnSubagentTool(BaseTool):
    name = "spawn_subagent"
    requires_sandbox = False
    description = (
        "Spawn a specialist child subagent with its own isolated context window while sharing the "
        "same workspace (`/workspace`) and `shared` Markdown blackboard. Use `wait=true` (default) "
        "to delegate a focused subtask and receive its report immediately, or `wait=false` to run "
        "multiple subagents in parallel and collect them with `wait_subagents`."
    )

    def __init__(
        self,
        coordinator: SubagentCoordinator,
        available_agents: list[dict[str, str]] | None = None,
    ) -> None:
        self._coord = coordinator
        agent_names = [a["name"] for a in (available_agents or []) if a.get("name")]
        agent_desc = (
            "Optional name of a configured Studio Agent to run (available: "
            + ", ".join(agent_names[:12])
            + "). Omit to inherit the parent Agent's model and tools."
            if agent_names
            else "Optional name of a configured Studio Agent to run. Omit to inherit the parent Agent's model and tools."
        )
        self.parameters_schema = {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "Concise 2-5 word role title (e.g. 'Code Reviewer', 'API Investigator', 'Script Author').",
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Clear, self-contained task brief specifying what to investigate/build, "
                        "what to publish to `memory_write(scope='shared')`, and what to return."
                    ),
                },
                "agent_name": {
                    "type": "string",
                    "description": agent_desc,
                },
                "wait": {
                    "type": "boolean",
                    "description": "Wait for completion and return report (true, default), or launch in parallel background (false).",
                    "default": True,
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Max tool rounds for the subagent (default 8, max 15).",
                    "minimum": 1,
                    "maximum": 15,
                    "default": 8,
                },
            },
            "required": ["role", "task"],
        }

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        role = str(arguments.get("role") or "").strip()
        task = str(arguments.get("task") or "").strip()
        agent_name = str(arguments.get("agent_name") or "").strip() or None
        wait = bool(arguments.get("wait", True))
        max_steps = int(arguments.get("max_steps") or 8)

        if not role:
            return ToolResult(success=False, error="Parameter 'role' is required.")
        if not task:
            return ToolResult(success=False, error="Parameter 'task' is required.")

        try:
            rec = await self._coord.spawn(
                role=role,
                task=task,
                agent_name=agent_name,
                wait=wait,
                max_steps=max_steps,
            )
            return ToolResult(
                success=(rec.status != "error"),
                output=_format_subagent_report(rec),
                error=rec.error if rec.status == "error" else None,
                metadata={
                    "subagent_id": rec.subagent_id,
                    "role": rec.role,
                    "agent_name": rec.agent_name,
                    "status": rec.status,
                    "step_count": len(rec.steps),
                    "subagent_steps": rec.to_summary_dict()["steps"],
                    "duration_ms": rec.duration_ms(),
                    "total_tokens": rec.total_tokens,
                },
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class SendAgentMessageTool(BaseTool):
    name = "send_agent_message"
    requires_sandbox = False
    description = (
        "Send a follow-up instruction, newly discovered intel, or steering correction to an "
        "existing subagent by `subagent_id`. If the subagent is running, injects mid-flight; "
        "if already completed, wakes it up in its preserved context."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "subagent_id": {
                "type": "string",
                "description": "The `subagent_id` returned by `spawn_subagent`.",
            },
            "message": {
                "type": "string",
                "description": "Follow-up guidance, intel, or additional subtask for the subagent.",
            },
            "wait": {
                "type": "boolean",
                "description": "Wait for the subagent to respond to this message (default true).",
                "default": True,
            },
        },
        "required": ["subagent_id", "message"],
    }

    def __init__(self, coordinator: SubagentCoordinator) -> None:
        self._coord = coordinator

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        subagent_id = str(arguments.get("subagent_id") or "").strip()
        msg = str(arguments.get("message") or "").strip()
        wait = bool(arguments.get("wait", True))

        if not subagent_id:
            return ToolResult(success=False, error="Parameter 'subagent_id' is required.")
        if not msg:
            return ToolResult(success=False, error="Parameter 'message' must be non-empty.")

        try:
            rec = await self._coord.send_message(
                subagent_id=subagent_id,
                message=msg,
                wait=wait,
            )
            return ToolResult(
                success=(rec.status != "error"),
                output=_format_subagent_report(rec),
                error=rec.error if rec.status == "error" else None,
                metadata={
                    "subagent_id": rec.subagent_id,
                    "role": rec.role,
                    "status": rec.status,
                    "step_count": len(rec.steps),
                    "subagent_steps": rec.to_summary_dict()["steps"],
                    "duration_ms": rec.duration_ms(),
                },
            )
        except (KeyError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))


class WaitSubagentsTool(BaseTool):
    name = "wait_subagents"
    requires_sandbox = False
    description = (
        "Check status or wait for parallel background subagents to complete and gather their "
        "final reports. Pass `wait=false` for an instant non-blocking status check."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "subagent_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of specific `subagent_id`s to wait on. Omit to wait on all spawned subagents.",
            },
            "wait": {
                "type": "boolean",
                "description": "Block until targets complete (true, default) or return instant status snapshot (false).",
                "default": True,
            },
            "timeout_sec": {
                "type": "integer",
                "description": "Maximum seconds to wait (default 120, max 300).",
                "minimum": 1,
                "maximum": 300,
                "default": 120,
            },
        },
    }

    def __init__(self, coordinator: SubagentCoordinator) -> None:
        self._coord = coordinator

    async def execute(self, arguments: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        raw_ids = arguments.get("subagent_ids")
        ids = [str(x).strip() for x in raw_ids if str(x).strip()] if isinstance(raw_ids, list) else None
        wait = bool(arguments.get("wait", True))
        timeout_sec = float(max(1, min(int(arguments.get("timeout_sec") or 120), 300)))

        summaries = await self._coord.wait_for(
            subagent_ids=ids,
            wait=wait,
            timeout_sec=timeout_sec,
        )
        if not summaries:
            return ToolResult(
                success=True,
                output="No subagents have been spawned in this run yet.",
                metadata={"count": 0, "done_count": 0, "subagents": []},
            )

        blocks: list[str] = []
        done_count = 0
        for s in summaries:
            if s["status"] in ("done", "error", "stopped"):
                done_count += 1
            header = (
                f"### `{s['subagent_id']}` — {s['role']} "
                f"[status={s['status']} | steps={s['step_count']} | {s['duration_ms']}ms]"
            )
            if s["status"] == "error":
                blocks.append(f"{header}\nError: {s['error']}")
            elif s["status"] == "running":
                blocks.append(f"{header}\nCurrently: {s['current_action'] or 'running'}")
            else:
                blocks.append(f"{header}\n\n{s['final_output']}")

        return ToolResult(
            success=True,
            output="\n\n---\n\n".join(blocks),
            metadata={
                "count": len(summaries),
                "done_count": done_count,
                "subagents": summaries,
            },
        )


def attach_subagent_tools_to_run(
    tool_registry: ToolRegistry,
    coordinator: SubagentCoordinator,
    *,
    available_agents: list[dict[str, str]] | None = None,
) -> str:
    """Register multi-agent orchestration tools and return a compact coordination prompt block."""
    tool_registry.register(SpawnSubagentTool(coordinator, available_agents), enabled=True)
    tool_registry.register(SendAgentMessageTool(coordinator), enabled=True)
    tool_registry.register(WaitSubagentsTool(coordinator), enabled=True)

    lines = [
        "<multi_agent_coordination>",
        "You can delegate parallel or heavy exploratory subtasks to isolated child subagents "
        "without polluting your own context window:",
        "- `spawn_subagent(role, task, agent_name?, wait=true|false)`: launch a focused subagent "
        "sharing `/workspace` and the `shared` Markdown blackboard (`scope='shared'`). "
        "Use `wait=false` to launch multiple independent subagents in parallel.",
        "- `wait_subagents(subagent_ids?, wait=true|false)`: wait for parallel subagents and collect reports.",
        "- `send_agent_message(subagent_id, message, wait=true|false)`: steer a running subagent or "
        "follow up with a completed one in its preserved context.",
        "Always instruct parallel subagents to record verified facts/artifacts in "
        "`memory_write(scope='shared')` so teammates stay synchronized.",
    ]
    if available_agents:
        lines.append("\nConfigured Studio Agents you can optionally specify via `agent_name`:")
        for ag in available_agents[:12]:
            desc = f" — {ag['description'][:100]}" if ag.get("description") else ""
            lines.append(f"- `{ag['name']}`{desc}")
    lines.append("</multi_agent_coordination>")
    return "\n".join(lines)
