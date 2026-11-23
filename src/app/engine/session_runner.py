from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.events import SessionEventBus, TraceEvent, TraceEventType, event_bus
from app.core.security import secret_box
from app.engine.guardrails.limits import GuardrailConfig
from app.engine.llm.chat_client import OpenAICompatChatClient
from app.engine.llm.types import LLMModelConfig
from app.engine.loops.base import (
    DEFAULT_CTF_SYSTEM_PROMPT,
    ChallengeBrief,
    LoopContext,
    LoopStatus,
)
from app.engine.loops.factory import create_loop_engine
from app.engine.memory.scratchpad import WorkingScratchpad
from app.engine.sandbox.base import BaseSandbox, ExecResult, SandboxConfig
from app.engine.sandbox.manager import SandboxManager
from app.engine.sandbox.simulated import SimulatedSandbox
from app.engine.tools.db_loader import build_tool_registry_from_db
from app.engine.tools.registry import create_default_tool_registry
from app.models.llm import LLMModel, TokenUsageLog
from app.models.session import (
    SandboxMode,
    SessionEvent,
    SessionScratchpad,
    SessionStatus,
    SolveSession,
)


@dataclass
class ActiveRun:
    session_id: str
    ctx: LoopContext
    sandbox: BaseSandbox
    llm_client: OpenAICompatChatClient
    task: asyncio.Task[Any]
    keep_container_on_stop: bool = False


class SessionRunnerManager:
    """Manages active solve session coroutines, DB trace persistence, and HITL controls."""

    def __init__(self, bus: SessionEventBus) -> None:
        self._bus = bus
        self._sandbox_mgr = SandboxManager()
        self._active: dict[str, ActiveRun] = {}
        self._lock = asyncio.Lock()
        self._hook_registered = False

    def register_persistence_hook(self) -> None:
        if not self._hook_registered:
            self._bus.add_hook(self._persist_trace_event)
            self._hook_registered = True

    async def mark_stale_sessions_stopped(self) -> None:
        """On server startup, recover any sessions orphaned in running/paused state."""
        async with async_session_factory() as db:
            await db.execute(
                update(SolveSession)
                .where(
                    SolveSession.status.in_(
                        [SessionStatus.RUNNING.value, SessionStatus.PAUSED.value]
                    )
                )
                .values(
                    status=SessionStatus.STOPPED.value,
                    finished_at=datetime.now(UTC),
                    error_message="Interrupted by backend restart.",
                )
            )
            await db.commit()

    def is_live(self, session_id: str) -> bool:
        run = self._active.get(session_id)
        return run is not None and not run.task.done()

    def get_active(self, session_id: str) -> ActiveRun | None:
        return self._active.get(session_id)

    async def _persist_trace_event(self, event: TraceEvent) -> None:
        """Persist every TraceEvent to SQLite and keep session metrics/scratchpad synced."""
        try:
            async with async_session_factory() as db:
                usage = event.usage
                row = SessionEvent(
                    session_id=event.session_id,
                    seq_id=event.seq_id,
                    step_index=event.step_index,
                    agent_name=event.agent_name,
                    event_type=event.event_type.value,
                    payload=event.payload,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                    cost_usd=usage.cost_usd if usage else 0.0,
                    latency_ms=usage.latency_ms if usage else 0,
                )
                db.add(row)

                session_obj = await db.get(SolveSession, event.session_id)
                if session_obj is not None:
                    if event.step_index > session_obj.current_step:
                        session_obj.current_step = event.step_index

                    if event.event_type == TraceEventType.TOKEN_USAGE and usage is not None:
                        session_obj.total_prompt_tokens += usage.prompt_tokens
                        session_obj.total_completion_tokens += usage.completion_tokens
                        session_obj.total_cost_usd = round(
                            session_obj.total_cost_usd + usage.cost_usd, 6
                        )
                        db.add(
                            TokenUsageLog(
                                model_db_id=session_obj.chat_model_id,
                                model_id=str(
                                    event.payload.get("model") or session_obj.model_name_snapshot
                                ),
                                model_type="chat",
                                session_id=session_obj.id,
                                prompt_tokens=usage.prompt_tokens,
                                completion_tokens=usage.completion_tokens,
                                total_tokens=usage.total_tokens,
                                cost_usd=usage.cost_usd,
                                latency_ms=usage.latency_ms,
                            )
                        )
                    elif event.event_type == TraceEventType.SCRATCHPAD_UPDATE:
                        sp_data = event.payload.get("scratchpad") or {}
                        sp_row = await db.scalar(
                            select(SessionScratchpad).where(
                                SessionScratchpad.session_id == event.session_id
                            )
                        )
                        if sp_row is not None:
                            sp_row.known_clues = list(sp_data.get("known_clues") or [])
                            sp_row.endpoints_ports = list(sp_data.get("endpoints_ports") or [])
                            sp_row.vulnerabilities = list(sp_data.get("vulnerabilities") or [])
                            sp_row.ruled_out = list(sp_data.get("ruled_out") or [])
                            sp_row.notes_md = str(sp_data.get("notes_md") or "")
                    elif event.event_type == TraceEventType.FLAG_DETECTED:
                        flag_val = event.payload.get("flag")
                        if flag_val:
                            session_obj.detected_flag = str(flag_val)
                    elif event.event_type == TraceEventType.STATUS:
                        st = event.payload.get("status")
                        if st in (SessionStatus.PAUSED.value, SessionStatus.RUNNING.value):
                            session_obj.status = st

                await db.commit()
        except Exception:
            # Never crash the reasoning loop on a secondary telemetry DB issue
            pass

    def attachments_dir(self, session_id: str) -> Path:
        path = settings.workspaces_dir / session_id / "attachments"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def workspace_dir(self, session_id: str) -> Path:
        path = settings.workspaces_dir / session_id / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def start_session(self, session_id: str) -> None:
        """Provision sandbox + LLM client and start background reasoning loop task."""
        async with self._lock:
            if self.is_live(session_id):
                raise RuntimeError("Session is already running.")

            async with async_session_factory() as db:
                session_obj = await db.get(SolveSession, session_id)
                if session_obj is None:
                    raise ValueError("Session not found.")

                model_obj = (
                    await db.get(LLMModel, session_obj.chat_model_id)
                    if session_obj.chat_model_id
                    else None
                )
                if model_obj is None:
                    raise ValueError(
                        "Configured Chat LLM model no longer exists. Please select a valid model."
                    )

                sp_row = await db.scalar(
                    select(SessionScratchpad).where(SessionScratchpad.session_id == session_id)
                )
                scratchpad = (
                    WorkingScratchpad(
                        known_clues=list(sp_row.known_clues or []),
                        endpoints_ports=list(sp_row.endpoints_ports or []),
                        vulnerabilities=list(sp_row.vulnerabilities or []),
                        ruled_out=list(sp_row.ruled_out or []),
                        notes_md=sp_row.notes_md or "",
                    )
                    if sp_row
                    else WorkingScratchpad()
                )

                # Sync event bus sequence counter to DB max seq_id so re-runs never collide
                max_seq = int(
                    (
                        await db.scalar(
                            select(func.coalesce(func.max(SessionEvent.seq_id), 0)).where(
                                SessionEvent.session_id == session_id
                            )
                        )
                    )
                    or 0
                )
                self._bus._seq_counters[session_id] = max(
                    self._bus._seq_counters.get(session_id, 0), max_seq
                )

                runtime_model_cfg = LLMModelConfig(
                    name=model_obj.name,
                    base_url=model_obj.base_url,
                    api_key=secret_box.decrypt(model_obj.api_key_encrypted),
                    model_id=model_obj.model_id,
                    context_window=model_obj.context_window,
                    max_output_tokens=model_obj.max_output_tokens,
                    temperature=model_obj.temperature,
                    input_price_per_1m=model_obj.input_price_per_1m,
                    output_price_per_1m=model_obj.output_price_per_1m,
                    extra_params=model_obj.extra_params or {},
                )

                att_dir = self.attachments_dir(session_id)
                ws_dir = self.workspace_dir(session_id)
                sandbox: BaseSandbox
                container_id: str | None = None

                if session_obj.sandbox_mode == SandboxMode.DOCKER.value:
                    sbx_cfg = SandboxConfig(
                        image=session_obj.sandbox_image or settings.sandbox_image,
                        platform=settings.sandbox_platform,
                        cpu_count=settings.sandbox_cpu_count,
                        mem_limit=settings.sandbox_mem_limit,
                        memswap_limit=settings.sandbox_memswap_limit,
                        pids_limit=settings.sandbox_pids_limit,
                        network_mode=settings.sandbox_network_mode,
                        workspace_host_dir=ws_dir,
                        attachments_host_dir=att_dir,
                        skills_host_dir=settings.resolved_skills_dir,
                        default_timeout=settings.sandbox_default_timeout,
                    )
                    docker_sbx = await self._sandbox_mgr.create_or_get(session_id, sbx_cfg)
                    sandbox = docker_sbx
                    container_id = docker_sbx.container_id[:12]
                else:
                    sandbox = SimulatedSandbox(
                        sandbox_id=session_id,
                        workspace_host_dir=ws_dir,
                        attachments_host_dir=att_dir,
                        seed_demo_if_empty=True,
                    )

                session_obj.status = SessionStatus.RUNNING.value
                session_obj.sandbox_container_id = container_id
                session_obj.started_at = datetime.now(UTC)
                session_obj.finished_at = None
                session_obj.error_message = ""
                await db.commit()

                tool_registry = await build_tool_registry_from_db(db)
                if not tool_registry.list_tools():
                    tool_registry = create_default_tool_registry()

                guardrails = GuardrailConfig(**(session_obj.guardrail_config or {}))
                challenge = ChallengeBrief(
                    title=session_obj.title,
                    category=session_obj.category,
                    description=session_obj.description,
                    target_info=session_obj.target_info or {},
                    attachments=list(session_obj.attachments or []),
                    flag_regex=session_obj.flag_regex or r"flag\{[^}\s]+\}",
                )
                sys_prompt = session_obj.system_prompt_override.strip() or DEFAULT_CTF_SYSTEM_PROMPT
                loop_name = session_obj.loop_engine

            llm_client = OpenAICompatChatClient(runtime_model_cfg)
            ctx = LoopContext(
                session_id=session_id,
                challenge=challenge,
                sandbox=sandbox,
                llm_client=llm_client,
                tool_registry=tool_registry,
                event_bus=self._bus,
                scratchpad=scratchpad,
                guardrails=guardrails,
                system_prompt=sys_prompt,
            )

            task = asyncio.create_task(
                self._execute_run(session_id, loop_name, ctx),
                name=f"solve-session-{session_id[:8]}",
            )
            self._active[session_id] = ActiveRun(
                session_id=session_id,
                ctx=ctx,
                sandbox=sandbox,
                llm_client=llm_client,
                task=task,
            )

    async def _execute_run(self, session_id: str, loop_name: str, ctx: LoopContext) -> None:
        final_status = SessionStatus.FAILED
        detected_flag: str | None = None
        summary = ""
        error_msg = ""

        try:
            engine = create_loop_engine(loop_name)
            result = await engine.run(ctx)

            if result.status == LoopStatus.SOLVED:
                final_status = SessionStatus.SOLVED
            elif result.status == LoopStatus.STOPPED:
                final_status = SessionStatus.STOPPED
            else:
                final_status = SessionStatus.FAILED

            detected_flag = result.detected_flag
            summary = result.summary
            error_msg = result.error or ""
        except asyncio.CancelledError:
            final_status = SessionStatus.STOPPED
            summary = "Session forcefully cancelled."
        except Exception as exc:
            final_status = SessionStatus.FAILED
            error_msg = f"{type(exc).__name__}: {exc}"
            summary = error_msg
            await self._bus.emit(
                session_id,
                TraceEventType.ERROR,
                {"error_type": type(exc).__name__, "message": str(exc)},
                step_index=0,
            )
        finally:
            await self._bus.emit(
                session_id,
                TraceEventType.STATUS,
                {
                    "status": final_status.value,
                    "detected_flag": detected_flag,
                    "summary": summary,
                    "error": error_msg,
                },
                step_index=0,
            )
            async with async_session_factory() as db:
                session_obj = await db.get(SolveSession, session_id)
                if session_obj is not None:
                    session_obj.status = final_status.value
                    if detected_flag:
                        session_obj.detected_flag = detected_flag
                    session_obj.summary = summary
                    session_obj.error_message = error_msg
                    session_obj.finished_at = datetime.now(UTC)
                    await db.commit()

            run = self._active.pop(session_id, None)
            if run is not None:
                await run.llm_client.close()
                if not run.keep_container_on_stop:
                    if isinstance(run.sandbox, SimulatedSandbox):
                        await run.sandbox.close()
                    else:
                        await self._sandbox_mgr.remove(session_id, remove_container=True)

    async def pause_session(self, session_id: str) -> None:
        run = self._active.get(session_id)
        if run is None or run.task.done():
            raise RuntimeError("Session is not currently running.")
        run.ctx.pause()
        async with async_session_factory() as db:
            obj = await db.get(SolveSession, session_id)
            if obj is not None:
                obj.status = SessionStatus.PAUSED.value
                await db.commit()

    async def resume_session(self, session_id: str) -> None:
        run = self._active.get(session_id)
        if run is None or run.task.done():
            raise RuntimeError("Session is not currently active.")
        run.ctx.resume()
        async with async_session_factory() as db:
            obj = await db.get(SolveSession, session_id)
            if obj is not None:
                obj.status = SessionStatus.RUNNING.value
                await db.commit()

    async def steer_session(
        self,
        session_id: str,
        guidance: str,
        *,
        immediate_resume: bool = True,
    ) -> None:
        run = self._active.get(session_id)
        if run is None or run.task.done():
            raise RuntimeError("Session is not currently active; cannot inject steering.")
        run.ctx.inject_steering(guidance, resume_if_paused=immediate_resume)
        if immediate_resume:
            async with async_session_factory() as db:
                obj = await db.get(SolveSession, session_id)
                if obj is not None and obj.status == SessionStatus.PAUSED.value:
                    obj.status = SessionStatus.RUNNING.value
                    await db.commit()

    async def stop_session(self, session_id: str, *, keep_container: bool = False) -> None:
        run = self._active.get(session_id)
        if run is not None and not run.task.done():
            run.keep_container_on_stop = keep_container
            run.ctx.stop()
            run.task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(run.task), timeout=3.0)
            except (TimeoutError, asyncio.CancelledError, Exception):
                pass
        else:
            async with async_session_factory() as db:
                obj = await db.get(SolveSession, session_id)
                if obj is not None and obj.status in (
                    SessionStatus.RUNNING.value,
                    SessionStatus.PAUSED.value,
                ):
                    obj.status = SessionStatus.STOPPED.value
                    obj.finished_at = datetime.now(UTC)
                    await db.commit()

    async def update_live_scratchpad(
        self, session_id: str, data: dict[str, Any]
    ) -> None:
        """Apply operator scratchpad edits to the live running context immediately."""
        run = self._active.get(session_id)
        if run is not None and not run.task.done():
            sp = run.ctx.scratchpad
            sp.known_clues = list(data.get("known_clues") or [])
            sp.endpoints_ports = list(data.get("endpoints_ports") or [])
            sp.vulnerabilities = list(data.get("vulnerabilities") or [])
            sp.ruled_out = list(data.get("ruled_out") or [])
            sp.notes_md = str(data.get("notes_md") or "")
            await self._bus.emit(
                session_id,
                TraceEventType.SCRATCHPAD_UPDATE,
                {"scratchpad": sp.model_dump(), "source": "human_operator"},
                step_index=0,
                agent_name="operator",
            )

    async def exec_manual_command(
        self,
        session_id: str,
        command: str,
        *,
        timeout: int = 30,
    ) -> ExecResult:
        """Execute an ad-hoc operator command inside the active session sandbox."""
        run = self._active.get(session_id)
        if run is None:
            raise RuntimeError(
                "No live sandbox is attached to this session. Start the session first."
            )
        res = await run.sandbox.exec_command(command, timeout=timeout)
        await self._bus.emit(
            session_id,
            TraceEventType.TOOL_CALL,
            {"tool_name": "operator_shell", "arguments": {"command": command}},
            step_index=0,
            agent_name="operator",
        )
        await self._bus.emit(
            session_id,
            TraceEventType.TOOL_RESULT,
            {
                "tool_name": "operator_shell",
                "success": res.exit_code == 0 and not res.timed_out,
                "output": res.output,
                "metadata": {
                    "exit_code": res.exit_code,
                    "cwd": res.cwd,
                    "duration_ms": res.duration_ms,
                },
            },
            step_index=0,
            agent_name="operator",
        )
        return res

    async def shutdown(self) -> None:
        for sid in list(self._active.keys()):
            await self.stop_session(sid, keep_container=False)
        await self._sandbox_mgr.cleanup_all()


session_runner = SessionRunnerManager(event_bus)
