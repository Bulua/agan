from __future__ import annotations

import asyncio
import copy
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.security import secret_box
from app.engine.guardrails.compactor import ContextCompactor
from app.engine.llm.chat_client import OpenAICompatChatClient
from app.engine.llm.types import LLMMessage, LLMModelConfig, TokenUsage, ToolCallRequest
from app.engine.mcp.manager import attach_mcp_tools_to_registry
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.memory.store import build_shared_blackboard_pin
from app.engine.memory.tools import attach_memory_to_agent_run
from app.engine.sandbox.base import BaseSandbox
from app.engine.skills.runtime import attach_skills_to_agent_run
from app.engine.tools.base import ToolExecutionContext
from app.engine.tools.db_loader import build_tool_registry_from_db
from app.engine.tools.registry import ToolRegistry
from app.models.agent import AgentConfig
from app.models.llm import LLMModel, ModelType

SUBAGENT_TOOL_NAMES = frozenset({"spawn_subagent", "send_agent_message", "wait_subagents"})


def _slugify_role(role: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (role or "worker").strip().lower()).strip("-")
    return (slug[:20] or "worker")


@dataclass
class SubagentRecord:
    """State and isolated conversation history of a spawned child Agent."""

    subagent_id: str
    role: str
    agent_name: str
    model_id: str
    task: str
    status: str = "running"  # "running" | "done" | "error" | "stopped"
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    finished_at_ms: int | None = None
    final_output: str = ""
    error: str = ""
    current_action: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    messages: list[LLMMessage] = field(default_factory=list)
    runtime_cfg: LLMModelConfig | None = None
    tool_registry: ToolRegistry | None = None
    max_steps: int = 8
    pending_messages: list[str] = field(default_factory=list)
    steer_interrupt: asyncio.Event = field(default_factory=asyncio.Event)
    task_handle: asyncio.Task[None] | None = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event)

    def duration_ms(self) -> int:
        end = self.finished_at_ms or int(time.time() * 1000)
        return max(0, end - self.started_at_ms)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "subagent_id": self.subagent_id,
            "role": self.role,
            "agent_name": self.agent_name,
            "model_id": self.model_id,
            "task": self.task[:300],
            "status": self.status,
            "current_action": self.current_action,
            "step_count": len(self.steps),
            "steps": copy.deepcopy(self.steps[-20:]),
            "duration_ms": self.duration_ms(),
            "total_tokens": self.total_tokens,
            "final_output": self.final_output,
            "error": self.error,
        }


class SubagentCoordinator:
    """Manages child subagents spawned by a parent Agent run.

    Design guarantees:
    - Each subagent runs in an isolated LLM context window (never bloats parent context).
    - All subagents share the parent's sandbox workspace AND `shared` Markdown blackboard so
      discoveries, files, and handoffs flow seamlessly across teammates.
    - Supports synchronous (`wait=True`) and parallel background (`wait=False`) execution,
      plus mid-flight steering or post-completion wake-up via `send_message()`.
    - Recursion-capped (`depth >= max_depth` strips subagent-spawning tools) and cleanly
      cancelled on parent shutdown/stop.
    """

    def __init__(
        self,
        *,
        parent_session_id: str,
        parent_agent_id: str | None,
        parent_agent_name: str,
        parent_runtime_cfg: LLMModelConfig,
        parent_tool_registry: ToolRegistry,
        sandbox: BaseSandbox,
        shared_memory_dir: Path | None,
        memories_root: Path | None = None,
        skills_root: Path | None = None,
        on_update: Callable[[dict[str, Any]], None] | None = None,
        depth: int = 0,
        max_depth: int = 1,
        max_subagents: int = 8,
    ) -> None:
        self.parent_session_id = parent_session_id
        self.parent_agent_id = parent_agent_id or "general"
        self.parent_agent_name = parent_agent_name
        self.parent_runtime_cfg = parent_runtime_cfg
        self.parent_tool_registry = parent_tool_registry
        self.sandbox = sandbox
        self.shared_memory_dir = shared_memory_dir
        self.memories_root = (memories_root or settings.resolved_memories_dir).resolve()
        self.skills_root = (skills_root or settings.resolved_skills_dir).resolve()
        self.on_update = on_update
        self.depth = depth
        self.max_depth = max_depth
        self.max_subagents = max_subagents

        self._records: dict[str, SubagentRecord] = {}
        self._seq = 0

    def _emit_update(self, rec: SubagentRecord) -> None:
        if self.on_update is not None:
            try:
                self.on_update(rec.to_summary_dict())
            except Exception:
                pass

    def list_records(self) -> list[dict[str, Any]]:
        return [r.to_summary_dict() for r in self._records.values()]

    def get_record(self, subagent_id: str) -> SubagentRecord | None:
        return self._records.get(subagent_id)

    async def _resolve_subagent_runtime(
        self,
        *,
        role: str,
        requested_agent_name: str | None,
    ) -> tuple[str, str, LLMModelConfig, ToolRegistry, str]:
        """Resolve `(agent_id, agent_name, runtime_cfg, tool_registry, system_prompt)`."""
        studio_agent: AgentConfig | None = None
        studio_model: LLMModel | None = None

        if requested_agent_name and requested_agent_name.strip():
            req = requested_agent_name.strip()
            async with async_session_factory() as db:
                # Match by exact ID or case-insensitive name
                studio_agent = await db.get(AgentConfig, req)
                if studio_agent is None:
                    all_agents = list(
                        (
                            await db.scalars(
                                select(AgentConfig).where(AgentConfig.is_active.is_(True))
                            )
                        ).all()
                    )
                    req_lower = req.lower()
                    studio_agent = next(
                        (a for a in all_agents if a.name.strip().lower() == req_lower),
                        None,
                    )
                if studio_agent is not None:
                    if studio_agent.chat_model_id:
                        m = await db.get(LLMModel, studio_agent.chat_model_id)
                        if m is not None and m.model_type == ModelType.CHAT.value:
                            studio_model = m

                    bound_tool_ids = [t.id for t in (studio_agent.tools or []) if t.is_enabled]
                    sub_registry = await build_tool_registry_from_db(db, tool_ids=bound_tool_ids)
                    bound_mcp = [s for s in (studio_agent.mcp_servers or []) if s.is_enabled]
                    if bound_mcp:
                        await attach_mcp_tools_to_registry(
                            sub_registry,
                            bound_mcp,
                            tool_filters=studio_agent.mcp_tool_filters or {},
                        )
                    skills_prompt = attach_skills_to_agent_run(
                        list(studio_agent.skill_names or []),
                        self.skills_root,
                        sub_registry,
                    )
                else:
                    sub_registry = self._clone_parent_tools()
                    skills_prompt = ""
        else:
            sub_registry = self._clone_parent_tools()
            skills_prompt = ""

        # Strip subagent-spawning tools when at recursion depth cap
        if self.depth + 1 >= self.max_depth:
            for tname in SUBAGENT_TOOL_NAMES:
                sub_registry.unregister(tname)

        if studio_agent is not None:
            agent_id = studio_agent.id
            agent_name = f"{studio_agent.name} ({role})" if role.lower() != studio_agent.name.lower() else studio_agent.name
            base_sys = studio_agent.system_prompt or ""
            mem_cfg = dict(studio_agent.memory_config or {})
            mem_cfg.setdefault("memory_mode", "read_only")
        else:
            agent_id = self.parent_agent_id
            agent_name = f"Subagent: {role}"
            base_sys = ""
            mem_cfg = {"enable_long_term_memory": True, "memory_mode": "read_only"}

        if studio_model is not None:
            runtime_cfg = LLMModelConfig(
                name=studio_model.name,
                base_url=studio_model.base_url,
                api_key=secret_box.decrypt(studio_model.api_key_encrypted),
                model_id=studio_model.model_id,
                context_window=studio_model.context_window,
                max_output_tokens=studio_model.max_output_tokens,
                temperature=(
                    studio_agent.temperature
                    if (studio_agent and studio_agent.temperature is not None)
                    else studio_model.temperature
                ),
                input_price_per_1m=studio_model.input_price_per_1m,
                output_price_per_1m=studio_model.output_price_per_1m,
                extra_params=studio_model.extra_params or {},
            )
        else:
            runtime_cfg = copy.deepcopy(self.parent_runtime_cfg)

        # Wire shared blackboard + scoped memory tools (overwrite any parent-bound memory tools!)
        memory_prompt = attach_memory_to_agent_run(
            sub_registry,
            memories_root=self.memories_root,
            agent_id=agent_id,
            agent_name=agent_name,
            shared_dir=self.shared_memory_dir,
            memory_config=mem_cfg,
        )

        role_banner = (
            f"You are a focused specialist subagent with role: **{role}**.\n"
            f"You were delegated a specific task by parent coordinator `{self.parent_agent_name}`.\n"
            "Guidelines:\n"
            "1. Work autonomously using your tools in the shared workspace (`/workspace`).\n"
            "2. Whenever you discover verified facts, file paths, endpoints, credentials, or "
            "ruled-out directions useful to other teammates, record them concisely via "
            "`memory_write(scope=\"shared\", ...)` so the parent and peer subagents can read them.\n"
            "3. When finished, return a dense, well-structured final report summarizing concrete "
            "findings, exact file paths/artifacts produced, and any remaining blockers."
        )

        sys_parts = [p.strip() for p in (role_banner, base_sys, memory_prompt, skills_prompt) if p and p.strip()]
        return agent_id, agent_name, runtime_cfg, sub_registry, "\n\n".join(sys_parts)

    def _clone_parent_tools(self) -> ToolRegistry:
        """Clone parent's non-subagent, non-memory tools for an ad-hoc subagent."""
        reg = ToolRegistry()
        skip = SUBAGENT_TOOL_NAMES | {"memory_search", "memory_read", "memory_write", "memory_edit"}
        for tool in self.parent_tool_registry.list_tools(only_enabled=True):
            if tool.name in skip:
                continue
            reg.register(tool, enabled=True)
        return reg

    async def spawn(
        self,
        *,
        role: str,
        task: str,
        agent_name: str | None = None,
        wait: bool = True,
        max_steps: int = 8,
    ) -> SubagentRecord:
        if len(self._records) >= self.max_subagents:
            raise RuntimeError(
                f"Subagent limit reached ({self.max_subagents} per run). "
                "Reuse an existing subagent via `send_agent_message` instead."
            )

        self._seq += 1
        sub_id = f"sub-{self._seq}-{_slugify_role(role)}"
        _, resolved_name, runtime_cfg, sub_registry, sys_prompt = await self._resolve_subagent_runtime(
            role=role,
            requested_agent_name=agent_name,
        )

        rec = SubagentRecord(
            subagent_id=sub_id,
            role=role.strip(),
            agent_name=resolved_name,
            model_id=runtime_cfg.model_id,
            task=task.strip(),
            status="running",
            messages=[
                LLMMessage(role="system", content=sys_prompt),
                LLMMessage(role="user", content=task.strip()),
            ],
            runtime_cfg=runtime_cfg,
            tool_registry=sub_registry,
            max_steps=max(1, min(int(max_steps or 8), 15)),
        )
        self._records[sub_id] = rec
        self._emit_update(rec)

        rec.task_handle = asyncio.create_task(
            self._run_subagent_loop(rec),
            name=f"subagent-{sub_id}",
        )

        if wait:
            await rec.done_event.wait()
        return rec

    async def send_message(
        self,
        *,
        subagent_id: str,
        message: str,
        wait: bool = True,
    ) -> SubagentRecord:
        rec = self._records.get(subagent_id)
        if rec is None:
            available = ", ".join(self._records.keys()) or "(none)"
            raise KeyError(f"Unknown subagent_id '{subagent_id}'. Active/known IDs: {available}")

        clean = message.strip()
        if not clean:
            raise ValueError("Parameter 'message' must be non-empty.")

        if rec.status == "running" and rec.task_handle and not rec.task_handle.done():
            # Mid-flight steering into running subagent!
            rec.pending_messages.append(clean)
            rec.steer_interrupt.set()
            rec.current_action = "incorporating parent message..."
            self._emit_update(rec)
            if wait:
                await rec.done_event.wait()
            return rec

        # Wake up completed/stopped subagent with preserved context for follow-up work
        rec.messages.append(
            LLMMessage(
                role="user",
                content=f"[Follow-up message from parent coordinator]:\n{clean}",
            )
        )
        rec.status = "running"
        rec.error = ""
        rec.finished_at_ms = None
        rec.current_action = "continuing task..."
        rec.done_event.clear()
        self._emit_update(rec)

        rec.task_handle = asyncio.create_task(
            self._run_subagent_loop(rec),
            name=f"subagent-{subagent_id}-cont",
        )
        if wait:
            await rec.done_event.wait()
        return rec

    async def wait_for(
        self,
        *,
        subagent_ids: list[str] | None = None,
        wait: bool = True,
        timeout_sec: float = 120.0,
    ) -> list[dict[str, Any]]:
        if subagent_ids:
            targets = [self._records[sid] for sid in subagent_ids if sid in self._records]
        else:
            targets = list(self._records.values())

        if wait and targets:
            waiters = [r.done_event.wait() for r in targets if r.status == "running"]
            if waiters:
                try:
                    await asyncio.wait_for(asyncio.gather(*waiters), timeout=timeout_sec)
                except TimeoutError:
                    pass

        return [r.to_summary_dict() for r in targets]

    async def shutdown_all(self) -> None:
        """Cancel all running subagent tasks cleanly when the parent run ends/stops."""
        running_tasks: list[asyncio.Task[None]] = []
        for rec in self._records.values():
            if rec.task_handle is not None and not rec.task_handle.done():
                rec.task_handle.cancel()
                running_tasks.append(rec.task_handle)
        if running_tasks:
            await asyncio.gather(*running_tasks, return_exceptions=True)

    async def _run_subagent_loop(self, rec: SubagentRecord) -> None:
        assert rec.runtime_cfg is not None
        assert rec.tool_registry is not None

        client = OpenAICompatChatClient(rec.runtime_cfg)
        compactor = ContextCompactor(
            context_window=rec.runtime_cfg.context_window,
            compact_ratio=0.75,
            keep_recent=6,
        )
        tool_ctx = ToolExecutionContext(
            session_id=f"{self.parent_session_id}:{rec.subagent_id}",
            sandbox=self.sandbox,
            scratchpad=WorkingScratchpad(),
            window_name=f"sub_{rec.subagent_id[:12]}",
            flag_regex=r"flag\{[^}\s]+\}",
        )
        tool_schemas = rec.tool_registry.get_openai_schemas()
        last_prompt_tokens = 0
        last_assistant_text = ""

        try:
            for round_idx in range(1, rec.max_steps + 2):
                # Drain any mid-flight messages from parent
                if rec.pending_messages:
                    joined = "\n".join(rec.pending_messages)
                    rec.pending_messages.clear()
                    rec.steer_interrupt.clear()
                    rec.messages.append(
                        LLMMessage(
                            role="user",
                            content=f"[Live message from parent coordinator]:\n{joined}",
                        )
                    )

                # Two-stage context compaction with live shared blackboard pinned
                shared_pin = build_shared_blackboard_pin(self.shared_memory_dir)
                rec.messages, _ = await compactor.compact_with_stats(
                    rec.messages,
                    scratchpad=tool_ctx.scratchpad,
                    pinned_blocks=[shared_pin] if shared_pin else None,
                    llm_client=client,
                    force=False,
                    last_prompt_tokens=last_prompt_tokens,
                )

                allow_tools = tool_schemas if (round_idx <= rec.max_steps and tool_schemas) else None
                rec.current_action = f"thinking (step {round_idx})..."
                self._emit_update(rec)

                content_parts: list[str] = []
                round_tool_calls: list[ToolCallRequest] = []
                interrupted = False

                async for chunk in client.stream_chat(rec.messages, tools=allow_tools):
                    if rec.steer_interrupt.is_set():
                        interrupted = True
                        break
                    ctype = chunk.get("type")
                    if ctype == "content_delta":
                        delta = str(chunk.get("delta", ""))
                        if delta:
                            content_parts.append(delta)
                    elif ctype == "done":
                        u = chunk.get("usage") or {}
                        tu = TokenUsage(**u)
                        rec.total_tokens += tu.total_tokens or (tu.prompt_tokens + tu.completion_tokens)
                        if tu.prompt_tokens > 0:
                            last_prompt_tokens = tu.prompt_tokens
                        round_tool_calls = [
                            ToolCallRequest(**tc) for tc in (chunk.get("tool_calls") or [])
                        ]

                round_text = "".join(content_parts).strip()
                if round_text:
                    last_assistant_text = round_text

                if interrupted:
                    if round_text:
                        rec.messages.append(LLMMessage(role="assistant", content=round_text))
                    continue

                if not round_tool_calls:
                    if round_text:
                        rec.messages.append(LLMMessage(role="assistant", content=round_text))
                    if rec.pending_messages:
                        continue
                    break

                rec.messages.append(
                    LLMMessage(
                        role="assistant",
                        content=round_text,
                        tool_calls=round_tool_calls,
                    )
                )

                for tc in round_tool_calls:
                    if rec.steer_interrupt.is_set():
                        rec.messages.append(
                            LLMMessage(
                                role="tool",
                                tool_call_id=tc.id,
                                name=tc.name,
                                content="[Skipped due to new message from parent coordinator]",
                            )
                        )
                        continue

                    rec.current_action = f"running {tc.name}..."
                    step_entry: dict[str, Any] = {
                        "tool_name": tc.name,
                        "arguments": tc.arguments,
                        "status": "running",
                        "success": True,
                        "duration_ms": 0,
                        "summary": "",
                    }
                    rec.steps.append(step_entry)
                    self._emit_update(rec)

                    t0 = time.monotonic()
                    res = await rec.tool_registry.execute(tc.name, tc.arguments, tool_ctx)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    rendered = res.output or res.error or "(empty)"

                    step_entry["status"] = "done"
                    step_entry["success"] = res.success
                    step_entry["duration_ms"] = elapsed_ms
                    step_entry["summary"] = rendered[:240]
                    self._emit_update(rec)

                    rec.messages.append(
                        LLMMessage(
                            role="tool",
                            tool_call_id=tc.id,
                            name=tc.name,
                            content=rendered,
                        )
                    )

            rec.final_output = last_assistant_text or "(Subagent completed without text summary.)"
            rec.status = "done"
            rec.current_action = ""
            rec.finished_at_ms = int(time.time() * 1000)
        except asyncio.CancelledError:
            rec.status = "stopped"
            rec.current_action = ""
            rec.finished_at_ms = int(time.time() * 1000)
            if last_assistant_text:
                rec.final_output = last_assistant_text
            raise
        except Exception as exc:
            rec.status = "error"
            rec.error = f"{type(exc).__name__}: {exc}"
            rec.current_action = ""
            rec.finished_at_ms = int(time.time() * 1000)
        finally:
            await client.close()
            rec.done_event.set()
            self._emit_update(rec)
