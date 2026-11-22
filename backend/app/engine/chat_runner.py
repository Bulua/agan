from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.database import async_session_factory
from app.engine.guardrails.compactor import ContextCompactor
from app.engine.llm.chat_client import OpenAICompatChatClient
from app.engine.llm.types import LLMMessage, LLMModelConfig, TokenUsage, ToolCallRequest
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.memory.store import build_shared_blackboard_pin
from app.engine.sandbox.base import BaseSandbox, SandboxConfig
from app.engine.sandbox.manager import SandboxManager
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.subagents.manager import SubagentCoordinator
from app.engine.subagents.tools import attach_subagent_tools_to_run
from app.engine.tools.base import ToolExecutionContext
from app.engine.tools.registry import ToolRegistry
from app.models.chat import ChatConversation, ChatMessage
from app.models.llm import TokenUsageLog
from app.schemas.chat import ChatMessageDTO


def _msg_to_dto(msg: ChatMessage) -> ChatMessageDTO:
    return ChatMessageDTO(
        id=msg.id,
        conversation_id=msg.conversation_id,
        role=msg.role,
        content=msg.content,
        reasoning_content=msg.reasoning_content,
        tool_calls_json=list(msg.tool_calls_json or []),
        model_id_used=msg.model_id_used,
        prompt_tokens=msg.prompt_tokens,
        completion_tokens=msg.completion_tokens,
        cost_usd=msg.cost_usd,
        latency_ms=msg.latency_ms,
        created_at=msg.created_at,
    )


async def _save_assistant_reply(
    *,
    conversation_id: str,
    model_db_id: str,
    model_id_used: str,
    content: str,
    reasoning_content: str,
    tool_calls_json: list[dict[str, Any]],
    usage_data: dict[str, Any],
    latency_ms: int,
) -> ChatMessageDTO | None:
    p_tok = int(usage_data.get("prompt_tokens", 0) or 0)
    c_tok = int(usage_data.get("completion_tokens", 0) or 0)
    t_tok = int(usage_data.get("total_tokens", p_tok + c_tok) or (p_tok + c_tok))
    cost = float(usage_data.get("cost_usd", 0.0) or 0.0)

    async with async_session_factory() as db:
        conv = await db.get(ChatConversation, conversation_id)
        if conv is None:
            return None

        msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            reasoning_content=reasoning_content,
            tool_calls_json=tool_calls_json,
            model_id_used=model_id_used,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            cost_usd=cost,
            latency_ms=latency_ms,
        )
        db.add(msg)

        conv.message_count += 1
        conv.total_tokens += t_tok
        conv.total_cost_usd = round(conv.total_cost_usd + cost, 6)
        conv.updated_at = datetime.now(UTC)

        if t_tok > 0:
            db.add(
                TokenUsageLog(
                    model_db_id=model_db_id,
                    model_id=model_id_used,
                    model_type="chat",
                    session_id=conversation_id,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok,
                    total_tokens=t_tok,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                )
            )

        await db.commit()
        await db.refresh(msg)
        return _msg_to_dto(msg)


async def _save_user_steering_message(
    *,
    conversation_id: str,
    content: str,
) -> ChatMessageDTO | None:
    async with async_session_factory() as db:
        conv = await db.get(ChatConversation, conversation_id)
        if conv is None:
            return None
        msg = ChatMessage(
            conversation_id=conversation_id,
            role="user",
            content=content,
        )
        db.add(msg)
        conv.message_count += 1
        conv.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(msg)
        return _msg_to_dto(msg)


logger = logging.getLogger("agan.chat_runner")

TITLE_GEN_SYSTEM_PROMPT = (
    "Generate a concise, descriptive title for this conversation in the exact same language as "
    "the user's message (4-12 Chinese characters or 3-6 English words). "
    "Respond immediately without lengthy analysis. Output ONLY the plain title text — "
    "no quotes, no prefixes like 'Title:', no Markdown, and no trailing punctuation."
)


def _clean_generated_title(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    # Strip <think>...</think> blocks emitted by reasoning models
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    # Keep first non-empty line
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    title = lines[0]
    # Remove common prefixes & decorative quotes/markdown/trailing punctuation
    title = re.sub(r"^(title|标题|会话标题)\s*[:：]\s*", "", title, flags=re.IGNORECASE)
    title = title.rstrip("。.!?！？;；,，")
    title = title.strip(" \t\"'`*#「」『』《》【】“”‘’")
    title = title.rstrip("。.!?！？;；,，")
    title = " ".join(title.split())
    if not title:
        return ""
    return title[:48]


@dataclass
class ChatRunSpec:
    conversation_id: str
    title: str
    agent_name: str
    model_db_id: str
    runtime_cfg: LLMModelConfig
    llm_messages: list[LLMMessage]
    tool_registry: ToolRegistry
    use_agent_tools: bool
    sandbox_mode: str
    sandbox_image: str
    max_tool_rounds: int
    user_message: dict[str, Any] | None
    compact_ratio: float = 0.75
    keep_recent_messages: int = 8
    shared_memory_dir: Path | None = None
    parent_agent_id: str | None = None
    enable_subagents: bool = True
    available_agents: list[dict[str, str]] = field(default_factory=list)
    auto_title_prompt: str = ""


@dataclass
class ActiveChatRun:
    run_id: str
    conversation_id: str
    title: str
    agent_name: str
    started_at_ms: int
    user_message: dict[str, Any] | None
    content_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    subagents: dict[str, dict[str, Any]] = field(default_factory=dict)
    status: str = "running"  # "running" | "done" | "error" | "stopped"
    error_message: str = ""
    assistant_message: dict[str, Any] | None = None
    task: asyncio.Task[None] | None = None
    subscribers: set[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=set)
    pending_steerings: list[str] = field(default_factory=list)
    steer_interrupt: asyncio.Event = field(default_factory=asyncio.Event)

    def drain_steerings(self) -> list[str]:
        items = list(self.pending_steerings)
        self.pending_steerings.clear()
        self.steer_interrupt.clear()
        return items

    def broadcast(self, event: dict[str, Any]) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def close_subscribers(self) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def build_snapshot(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "title": self.title,
            "agent_name": self.agent_name,
            "started_at_ms": self.started_at_ms,
            "content": "".join(self.content_parts),
            "reasoning_content": "".join(self.reasoning_parts),
            "tool_calls": copy.deepcopy(self.tool_calls),
            "subagents": copy.deepcopy(list(self.subagents.values())),
            "pending_steerings": list(self.pending_steerings),
            "status": self.status,
            "error_message": self.error_message,
            "assistant_message": self.assistant_message,
        }


class ChatRunManager:
    """Runs Agent chat turns as independent background asyncio tasks with live SSE reattachment.

    Disconnecting a browser SSE stream (switching tabs, switching conversations, or pressing F5)
    NEVER cancels the running task; clients can reconnect at any time via `/stream` to receive an
    atomic snapshot + live token deltas until completion. Only explicit `/stop` cancels the task.
    """

    def __init__(self) -> None:
        self._runs: dict[str, ActiveChatRun] = {}
        self._sandbox_mgr = SandboxManager()

    def get_run(self, conversation_id: str) -> ActiveChatRun | None:
        return self._runs.get(conversation_id)

    def is_running(self, conversation_id: str) -> bool:
        run = self._runs.get(conversation_id)
        return run is not None and run.status == "running"

    def running_ids(self) -> set[str]:
        return {cid for cid, run in self._runs.items() if run.status == "running"}

    def start_run(self, spec: ChatRunSpec) -> tuple[ActiveChatRun, asyncio.Queue[dict[str, Any] | None]]:
        """Create and launch a background chat run, returning (run, initial_subscriber_queue)."""
        existing = self._runs.get(spec.conversation_id)
        if existing is not None and existing.status == "running":
            raise RuntimeError("This conversation already has an active task running.")

        run = ActiveChatRun(
            run_id=uuid.uuid4().hex,
            conversation_id=spec.conversation_id,
            title=spec.title,
            agent_name=spec.agent_name,
            started_at_ms=int(time.time() * 1000),
            user_message=spec.user_message,
        )
        initial_q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        run.subscribers.add(initial_q)
        self._runs[spec.conversation_id] = run

        run.task = asyncio.create_task(
            self._execute_run(run, spec),
            name=f"chat-run-{spec.conversation_id[:8]}",
        )
        return run, initial_q

    async def subscribe_existing(
        self, conversation_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Atomically snapshot an existing run and stream all subsequent live events."""
        run = self._runs.get(conversation_id)
        if run is None:
            return

        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        # Synchronous atomic snapshot + subscribe (no await between them -> zero missed deltas)
        snapshot = run.build_snapshot()
        if run.status == "running":
            run.subscribers.add(q)

        yield snapshot

        if run.status != "running":
            if run.status == "error" and run.error_message:
                yield {"type": "error", "message": run.error_message}
            elif run.assistant_message is not None:
                yield {
                    "type": "done",
                    "title": run.title,
                    "assistant_message": run.assistant_message,
                }
            return

        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item
        finally:
            run.subscribers.discard(q)

    async def stream_from_queue(
        self,
        run: ActiveChatRun,
        q: asyncio.Queue[dict[str, Any] | None],
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield events from an initial subscriber queue; detaching never cancels `run.task`."""
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item
        finally:
            run.subscribers.discard(q)

    async def stop_run(self, conversation_id: str) -> bool:
        """Explicitly cancel an active background chat run and wait briefly for partial save."""
        run = self._runs.get(conversation_id)
        if run is None or run.task is None or run.task.done():
            return False
        run.task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(run.task), timeout=3.0)
        except (TimeoutError, asyncio.CancelledError, Exception):
            pass
        return True

    def steer_run(self, conversation_id: str, guidance: str) -> dict[str, Any]:
        """Inject live mid-flight user guidance into an active background chat run."""
        run = self._runs.get(conversation_id)
        if run is None or run.status != "running":
            raise RuntimeError("No active task is running for this conversation.")
        clean = guidance.strip()
        if not clean:
            raise ValueError("Steering guidance cannot be empty.")

        run.pending_steerings.append(clean)
        run.steer_interrupt.set()
        run.broadcast({"type": "steering_pending", "content": clean})
        return {"content": clean}

    async def shutdown(self) -> None:
        for cid in list(self._runs.keys()):
            await self.stop_run(cid)
        await self._sandbox_mgr.cleanup_all()

    async def _cleanup_later(self, conversation_id: str, run_id: str, delay_sec: float = 30.0) -> None:
        await asyncio.sleep(delay_sec)
        cur = self._runs.get(conversation_id)
        if cur is not None and cur.run_id == run_id and cur.status != "running":
            self._runs.pop(conversation_id, None)

    async def _generate_and_persist_title(
        self,
        run: ActiveChatRun,
        spec: ChatRunSpec,
        placeholder_title: str,
    ) -> None:
        """Sidecar async LLM call to summarize the first user message into a concise title."""
        prompt_text = (spec.auto_title_prompt or "").strip()
        if not prompt_text:
            return
        title_client = OpenAICompatChatClient(spec.runtime_cfg)
        title_msgs = [
            LLMMessage(role="system", content=TITLE_GEN_SYSTEM_PROMPT),
            LLMMessage(role="user", content=prompt_text[:800]),
        ]
        try:
            # Use generous max_tokens (1024) and the model's own valid temperature so
            # reasoning models (DeepSeek-R1, Qwen3, Claude Thinking, Kimi, o1/o3) have
            # room for internal reasoning tokens without exhausting max_tokens before
            # emitting the actual title or rejecting non-default temperature values.
            raw_title_text = ""
            finish_reason = None
            try:
                resp = await asyncio.wait_for(
                    title_client.chat(title_msgs, max_tokens=1024),
                    timeout=30.0,
                )
                raw_title_text = resp.content or ""
                finish_reason = resp.finish_reason
            except Exception as chat_exc:
                # Some reasoning gateways (e.g. Qwen3 with thinking enabled) require stream=True
                logger.info(
                    "Auto-title non-streaming call fell back to stream_chat (model=%s): %s",
                    spec.runtime_cfg.model_id,
                    chat_exc,
                )
                parts: list[str] = []

                async def _collect_stream() -> None:
                    nonlocal finish_reason
                    async for ch in title_client.stream_chat(title_msgs, max_tokens=1024):
                        if ch.get("type") == "content_delta":
                            parts.append(str(ch.get("delta", "")))
                        elif ch.get("type") == "done":
                            finish_reason = ch.get("finish_reason")

                await asyncio.wait_for(_collect_stream(), timeout=30.0)
                raw_title_text = "".join(parts)

            cleaned = _clean_generated_title(raw_title_text)
            if not cleaned:
                logger.warning(
                    "Auto-title returned empty content (model=%s, finish_reason=%s)",
                    spec.runtime_cfg.model_id,
                    finish_reason,
                )
                return
            if cleaned == placeholder_title:
                return

            async with async_session_factory() as db:
                conv = await db.get(ChatConversation, spec.conversation_id)
                if conv is None:
                    return
                # Respect manual user rename if title was already changed externally
                if conv.title != placeholder_title:
                    return
                conv.title = cleaned
                conv.updated_at = datetime.now(UTC)
                await db.commit()

            run.title = cleaned
            spec.title = cleaned
            run.broadcast(
                {
                    "type": "title_updated",
                    "conversation_id": spec.conversation_id,
                    "title": cleaned,
                }
            )
        except Exception as exc:
            # Silent degradation: log for diagnostics, never disturb primary chat flow
            logger.warning(
                "Auto-title generation skipped (model=%s): %s: %s",
                spec.runtime_cfg.model_id,
                type(exc).__name__,
                exc,
            )
            return
        finally:
            await title_client.close()

    async def _execute_run(self, run: ActiveChatRun, spec: ChatRunSpec) -> None:
        client = OpenAICompatChatClient(spec.runtime_cfg)
        compactor = ContextCompactor(
            context_window=spec.runtime_cfg.context_window,
            compact_ratio=spec.compact_ratio,
            keep_recent=spec.keep_recent_messages,
        )
        total_usage = TokenUsage()
        last_round_prompt_tokens = 0
        start_ts = time.monotonic()
        resolved_model = spec.runtime_cfg.model_id
        sandbox: BaseSandbox | None = None
        subagent_coord: SubagentCoordinator | None = None
        title_task: asyncio.Task[None] | None = None

        async def _maybe_compact_context(
            working_msgs: list[LLMMessage],
            *,
            scratchpad: WorkingScratchpad | None = None,
            prompt_tokens_hint: int = 0,
        ) -> list[LLMMessage]:
            shared_pin = build_shared_blackboard_pin(spec.shared_memory_dir)
            new_msgs, stats = await compactor.compact_with_stats(
                working_msgs,
                scratchpad=scratchpad,
                pinned_blocks=[shared_pin] if shared_pin else None,
                llm_client=client,
                force=False,
                last_prompt_tokens=prompt_tokens_hint,
            )
            should_report = stats.stage == "auto" or (
                stats.stage == "micro"
                and stats.cleared_tool_outputs > 0
                and stats.tokens_saved >= 150
            )
            if should_report:
                compact_record: dict[str, Any] = {
                    "id": f"compact_{uuid.uuid4().hex[:8]}",
                    "tool_name": "__context_compact__",
                    "arguments": {"stage": stats.stage},
                    "status": "done",
                    "success": True,
                    "output": (
                        stats.summary_text
                        or f"Cleared {stats.cleared_tool_outputs} earlier tool output(s), saving ~{stats.tokens_saved} tokens."
                    ),
                    "duration_ms": 0,
                    "metadata": stats.to_dict(),
                }
                run.tool_calls.append(compact_record)
                run.broadcast({"type": "tool_call", "tool": copy.deepcopy(compact_record)})
                run.broadcast({"type": "tool_result", "tool": copy.deepcopy(compact_record)})
                run.broadcast({"type": "context_compacted", "stats": stats.to_dict()})
            return new_msgs

        async def _commit_steering_checkpoint(
            steerings: list[str],
            working_msgs: list[LLMMessage],
        ) -> None:
            nonlocal total_usage, start_ts
            # 1. Persist current partial assistant segment (if anything was produced)
            saved_seg: ChatMessageDTO | None = None
            if run.content_parts or run.reasoning_parts or run.tool_calls:
                saved_seg = await _save_assistant_reply(
                    conversation_id=spec.conversation_id,
                    model_db_id=spec.model_db_id,
                    model_id_used=resolved_model,
                    content="".join(run.content_parts),
                    reasoning_content="".join(run.reasoning_parts).strip(),
                    tool_calls_json=copy.deepcopy(run.tool_calls),
                    usage_data=total_usage.model_dump(),
                    latency_ms=int((time.monotonic() - start_ts) * 1000),
                )
            run.broadcast(
                {
                    "type": "segment_done",
                    "assistant_message": saved_seg.model_dump(mode="json") if saved_seg else None,
                }
            )

            # 2. Persist each steering message as a real User ChatMessage in exact chronological order
            saved_user_payloads: list[dict[str, Any]] = []
            for text in steerings:
                u_dto = await _save_user_steering_message(
                    conversation_id=spec.conversation_id,
                    content=text,
                )
                if u_dto is not None:
                    saved_user_payloads.append(u_dto.model_dump(mode="json"))

            # 3. Reset live segment buffers for the newly steered assistant response
            run.content_parts = []
            run.reasoning_parts = []
            run.tool_calls = []
            total_usage = TokenUsage()
            start_ts = time.monotonic()
            run.started_at_ms = int(time.time() * 1000)

            run.broadcast(
                {
                    "type": "steering_committed",
                    "user_messages": saved_user_payloads,
                    "started_at_ms": run.started_at_ms,
                }
            )

            # 4. Feed the user's guidance directly into the LLM working conversation
            joined = "\n".join(steerings)
            working_msgs.append(
                LLMMessage(
                    role="user",
                    content=(
                        f"[Live user steering]:\n{joined}\n\n"
                        "Immediately incorporate this guidance and continue."
                    ),
                )
            )

        try:
            run.broadcast(
                {
                    "type": "meta",
                    "title": spec.title,
                    "agent_name": spec.agent_name,
                    "started_at_ms": run.started_at_ms,
                    "user_message": spec.user_message,
                }
            )

            if spec.auto_title_prompt:
                title_task = asyncio.create_task(
                    self._generate_and_persist_title(run, spec, spec.title),
                    name=f"chat-title-{spec.conversation_id[:8]}",
                )

            if not spec.use_agent_tools:
                working_msgs = list(spec.llm_messages)
                max_steer_turns = 8
                for _turn in range(max_steer_turns):
                    working_msgs = await _maybe_compact_context(
                        working_msgs,
                        prompt_tokens_hint=last_round_prompt_tokens,
                    )
                    turn_content: list[str] = []
                    turn_reasoning: list[str] = []
                    interrupted_by_steer = False

                    async for chunk in client.stream_chat(working_msgs):
                        if run.steer_interrupt.is_set():
                            interrupted_by_steer = True
                            break
                        ctype = chunk.get("type")
                        if ctype == "reasoning_delta":
                            delta = str(chunk.get("delta", ""))
                            if delta:
                                if not turn_reasoning and run.reasoning_parts:
                                    sep = "\n\n"
                                    run.reasoning_parts.append(sep)
                                    run.broadcast({"type": "reasoning_delta", "delta": sep})
                                turn_reasoning.append(delta)
                                run.reasoning_parts.append(delta)
                                run.broadcast({"type": "reasoning_delta", "delta": delta})
                        elif ctype == "content_delta":
                            delta = str(chunk.get("delta", ""))
                            if delta:
                                if not turn_content and run.content_parts:
                                    sep = "\n\n"
                                    run.content_parts.append(sep)
                                    run.broadcast({"type": "content_delta", "delta": sep})
                                turn_content.append(delta)
                                run.content_parts.append(delta)
                                run.broadcast({"type": "content_delta", "delta": delta})
                        elif ctype == "done":
                            u = chunk.get("usage") or {}
                            round_usage = TokenUsage(**u)
                            total_usage.add(round_usage)
                            if round_usage.prompt_tokens > 0:
                                last_round_prompt_tokens = round_usage.prompt_tokens
                            resolved_model = str(chunk.get("model") or resolved_model)

                    steerings = run.drain_steerings()
                    if not steerings and not interrupted_by_steer:
                        break
                    if turn_content:
                        working_msgs.append(
                            LLMMessage(role="assistant", content="".join(turn_content))
                        )
                    if steerings:
                        await _commit_steering_checkpoint(steerings, working_msgs)
            else:
                chat_ws_dir = settings.workspaces_dir / f"chat-{spec.conversation_id}"
                needs_docker = spec.sandbox_mode == "docker" and any(
                    t.requires_sandbox for t in spec.tool_registry.list_tools()
                )
                if needs_docker:
                    sandbox = await self._sandbox_mgr.create_or_get(
                        f"chat-{spec.conversation_id[:16]}",
                        SandboxConfig(
                            image=spec.sandbox_image,
                            workspace_host_dir=chat_ws_dir,
                            skills_host_dir=settings.resolved_skills_dir,
                        ),
                        reuse_existing=True,
                    )
                else:
                    sandbox = SimulatedSandbox(
                        f"chat-{spec.conversation_id[:16]}",
                        workspace_host_dir=chat_ws_dir,
                        seed_demo_if_empty=False,
                    )

                tool_ctx = ToolExecutionContext(
                    session_id=spec.conversation_id,
                    sandbox=sandbox,
                    scratchpad=WorkingScratchpad(),
                    flag_regex=r"flag\{[^}\s]+\}",
                )
                working_msgs = list(spec.llm_messages)

                if spec.enable_subagents:
                    def _on_subagent_update(summary: dict[str, Any]) -> None:
                        sid = str(summary.get("subagent_id") or "")
                        if sid:
                            run.subagents[sid] = copy.deepcopy(summary)
                        run.broadcast({"type": "subagent_update", "subagent": copy.deepcopy(summary)})

                    subagent_coord = SubagentCoordinator(
                        parent_session_id=spec.conversation_id,
                        parent_agent_id=spec.parent_agent_id,
                        parent_agent_name=spec.agent_name,
                        parent_runtime_cfg=spec.runtime_cfg,
                        parent_tool_registry=spec.tool_registry,
                        sandbox=sandbox,
                        shared_memory_dir=spec.shared_memory_dir,
                        on_update=_on_subagent_update,
                    )
                    coord_prompt = attach_subagent_tools_to_run(
                        spec.tool_registry,
                        subagent_coord,
                        available_agents=spec.available_agents,
                    )
                    if coord_prompt:
                        if working_msgs and working_msgs[0].role == "system":
                            working_msgs[0] = LLMMessage(
                                role="system",
                                content=f"{working_msgs[0].content}\n\n{coord_prompt}",
                            )
                        else:
                            working_msgs.insert(0, LLMMessage(role="system", content=coord_prompt))

                tool_schemas = spec.tool_registry.get_openai_schemas()

                round_idx = 0
                max_total_rounds = spec.max_tool_rounds + 6
                hit_limit = False

                while round_idx < max_total_rounds:
                    round_idx += 1
                    # Checkpoint any steering queued right before this LLM call
                    pre_steerings = run.drain_steerings()
                    if pre_steerings:
                        await _commit_steering_checkpoint(pre_steerings, working_msgs)

                    # Two-stage context compaction (Micro-Compact + Auto-Compact)
                    working_msgs = await _maybe_compact_context(
                        working_msgs,
                        scratchpad=tool_ctx.scratchpad,
                        prompt_tokens_hint=last_round_prompt_tokens,
                    )

                    round_content_parts: list[str] = []
                    round_reasoning_parts: list[str] = []
                    round_tool_calls: list[ToolCallRequest] = []
                    interrupted_by_steer = False

                    async for chunk in client.stream_chat(working_msgs, tools=tool_schemas):
                        if run.steer_interrupt.is_set():
                            interrupted_by_steer = True
                            break
                        ctype = chunk.get("type")
                        if ctype == "reasoning_delta":
                            delta = str(chunk.get("delta", ""))
                            if delta:
                                if not round_reasoning_parts and run.reasoning_parts:
                                    sep = "\n\n"
                                    run.reasoning_parts.append(sep)
                                    run.broadcast({"type": "reasoning_delta", "delta": sep})
                                round_reasoning_parts.append(delta)
                                run.reasoning_parts.append(delta)
                                run.broadcast({"type": "reasoning_delta", "delta": delta})
                        elif ctype == "content_delta":
                            delta = str(chunk.get("delta", ""))
                            if delta:
                                if not round_content_parts and run.content_parts:
                                    sep = "\n\n"
                                    run.content_parts.append(sep)
                                    run.broadcast({"type": "content_delta", "delta": sep})
                                round_content_parts.append(delta)
                                run.content_parts.append(delta)
                                run.broadcast({"type": "content_delta", "delta": delta})
                        elif ctype == "done":
                            u = chunk.get("usage") or {}
                            round_usage = TokenUsage(**u)
                            total_usage.add(round_usage)
                            if round_usage.prompt_tokens > 0:
                                last_round_prompt_tokens = round_usage.prompt_tokens
                            resolved_model = str(chunk.get("model") or resolved_model)
                            round_tool_calls = [
                                ToolCallRequest(**tc) for tc in (chunk.get("tool_calls") or [])
                            ]

                    if interrupted_by_steer:
                        # Discard incomplete partial tool calls and pivot immediately to user steering
                        if round_content_parts:
                            working_msgs.append(
                                LLMMessage(role="assistant", content="".join(round_content_parts))
                            )
                        continue

                    if not round_tool_calls:
                        # Check if user sent steering right as final text finished
                        if run.pending_steerings:
                            if round_content_parts:
                                working_msgs.append(
                                    LLMMessage(
                                        role="assistant", content="".join(round_content_parts)
                                    )
                                )
                            continue
                        break

                    working_msgs.append(
                        LLMMessage(
                            role="assistant",
                            content="".join(round_content_parts),
                            tool_calls=round_tool_calls,
                        )
                    )

                    for tc in round_tool_calls:
                        # If user steered mid-batch, skip remaining unstarted tools cleanly
                        if run.steer_interrupt.is_set():
                            working_msgs.append(
                                LLMMessage(
                                    role="tool",
                                    tool_call_id=tc.id,
                                    name=tc.name,
                                    content="[Skipped due to immediate user steering]",
                                )
                            )
                            continue

                        call_record: dict[str, Any] = {
                            "id": tc.id,
                            "tool_name": tc.name,
                            "arguments": tc.arguments,
                            "status": "running",
                            "output": "",
                            "success": True,
                        }
                        run.tool_calls.append(call_record)
                        run.broadcast({"type": "tool_call", "tool": copy.deepcopy(call_record)})

                        t_start = time.monotonic()
                        tool_res = await spec.tool_registry.execute(tc.name, tc.arguments, tool_ctx)
                        t_ms = int((time.monotonic() - t_start) * 1000)
                        rendered = tool_res.output or tool_res.error or "(empty)"
                        meta = dict(tool_res.metadata or {})
                        if tc.name == "write_file" and "content" in tc.arguments:
                            meta.setdefault(
                                "lines_written",
                                len(str(tc.arguments.get("content") or "").splitlines()),
                            )

                        call_record["status"] = "done"
                        call_record["success"] = tool_res.success
                        call_record["output"] = rendered
                        call_record["duration_ms"] = t_ms
                        call_record["metadata"] = meta

                        run.broadcast({"type": "tool_result", "tool": copy.deepcopy(call_record)})

                        working_msgs.append(
                            LLMMessage(
                                role="tool",
                                tool_call_id=tc.id,
                                name=tc.name,
                                content=rendered,
                            )
                        )

                    if round_idx >= spec.max_tool_rounds and not run.pending_steerings:
                        hit_limit = True
                        break

                if hit_limit:
                    round_content_started = False
                    round_reasoning_started = False
                    async for chunk in client.stream_chat(working_msgs, tools=None):
                        ctype = chunk.get("type")
                        if ctype == "reasoning_delta":
                            delta = str(chunk.get("delta", ""))
                            if delta:
                                if not round_reasoning_started and run.reasoning_parts:
                                    sep = "\n\n"
                                    run.reasoning_parts.append(sep)
                                    run.broadcast({"type": "reasoning_delta", "delta": sep})
                                round_reasoning_started = True
                                run.reasoning_parts.append(delta)
                                run.broadcast({"type": "reasoning_delta", "delta": delta})
                        elif ctype == "content_delta":
                            delta = str(chunk.get("delta", ""))
                            if delta:
                                if not round_content_started and run.content_parts:
                                    sep = "\n\n"
                                    run.content_parts.append(sep)
                                    run.broadcast({"type": "content_delta", "delta": sep})
                                round_content_started = True
                                run.content_parts.append(delta)
                                run.broadcast({"type": "content_delta", "delta": delta})
                        elif ctype == "done":
                            u = chunk.get("usage") or {}
                            total_usage.add(TokenUsage(**u))
                            resolved_model = str(chunk.get("model") or resolved_model)

            # Give sidecar title generation a brief moment to land before emitting `done`
            if title_task is not None and not title_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(title_task), timeout=5.0)
                except (TimeoutError, Exception):
                    pass

            latency_ms = int((time.monotonic() - start_ts) * 1000)
            saved = await _save_assistant_reply(
                conversation_id=spec.conversation_id,
                model_db_id=spec.model_db_id,
                model_id_used=resolved_model,
                content="".join(run.content_parts),
                reasoning_content="".join(run.reasoning_parts).strip(),
                tool_calls_json=copy.deepcopy(run.tool_calls),
                usage_data=total_usage.model_dump(),
                latency_ms=latency_ms,
            )
            run.status = "done"
            run.assistant_message = saved.model_dump(mode="json") if saved else None
            run.broadcast(
                {
                    "type": "done",
                    "title": spec.title,
                    "assistant_message": run.assistant_message,
                }
            )
        except asyncio.CancelledError:
            run.status = "stopped"
            if run.content_parts or run.reasoning_parts or run.tool_calls:
                for tc in run.tool_calls:
                    if tc.get("status") == "running":
                        tc["status"] = "done"
                        tc["success"] = False
                        tc["output"] = tc.get("output") or "(stopped by user)"
                saved = await _save_assistant_reply(
                    conversation_id=spec.conversation_id,
                    model_db_id=spec.model_db_id,
                    model_id_used=resolved_model,
                    content="".join(run.content_parts),
                    reasoning_content="".join(run.reasoning_parts).strip(),
                    tool_calls_json=copy.deepcopy(run.tool_calls),
                    usage_data=total_usage.model_dump(),
                    latency_ms=int((time.monotonic() - start_ts) * 1000),
                )
                run.assistant_message = saved.model_dump(mode="json") if saved else None
            run.broadcast(
                {
                    "type": "done",
                    "title": spec.title,
                    "stopped": True,
                    "assistant_message": run.assistant_message,
                }
            )
            raise
        except Exception as exc:
            run.status = "error"
            run.error_message = f"{type(exc).__name__}: {exc}"
            if run.content_parts or run.reasoning_parts or run.tool_calls:
                await _save_assistant_reply(
                    conversation_id=spec.conversation_id,
                    model_db_id=spec.model_db_id,
                    model_id_used=resolved_model,
                    content="".join(run.content_parts),
                    reasoning_content="".join(run.reasoning_parts).strip(),
                    tool_calls_json=copy.deepcopy(run.tool_calls),
                    usage_data=total_usage.model_dump(),
                    latency_ms=int((time.monotonic() - start_ts) * 1000),
                )
            run.broadcast({"type": "error", "message": run.error_message})
        finally:
            if subagent_coord is not None:
                await subagent_coord.shutdown_all()
            await client.close()
            run.close_subscribers()
            asyncio.create_task(self._cleanup_later(spec.conversation_id, run.run_id))


chat_run_manager = ChatRunManager()
