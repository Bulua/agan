"""Stage 2 MVP CLI runner: spins up an Ubuntu Docker sandbox and runs ReAct or Plan-and-Execute."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

# Allow running directly via `python scripts/run_mvp_demo.py`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.events import SessionEventBus, TraceEvent
from app.engine.guardrails.limits import GuardrailConfig
from app.engine.llm.chat_client import OpenAICompatChatClient
from app.engine.llm.types import LLMModelConfig
from app.engine.loops.base import ChallengeBrief, LoopContext
from app.engine.loops.factory import create_loop_engine
from app.engine.sandbox.base import SandboxConfig
from app.engine.sandbox.manager import SandboxManager
from app.engine.tools.registry import create_default_tool_registry


async def _print_event(event: TraceEvent) -> None:
    prefix = f"[seq={event.seq_id:03d} | step={event.step_index:02d} | {event.event_type.value}]"
    print(f"{prefix} {event.payload}", flush=True)


async def main() -> None:
    parser = argparse.ArgumentParser(description="AGAN-CTF Stage 2 MVP Runner")
    parser.add_argument("--engine", choices=["react", "plan_execute"], default="react")
    parser.add_argument("--title", default="Demo Challenge")
    parser.add_argument("--category", default="misc")
    parser.add_argument("--desc", default="Find the flag located inside /workspace/challenge.")
    parser.add_argument("--attachments-dir", type=Path, default=None)
    parser.add_argument("--image", default=settings.sandbox_image)
    parser.add_argument("--model", default=os.getenv("AGAN_DEFAULT_LLM_MODEL", settings.default_llm_model))
    parser.add_argument("--base-url", default=os.getenv("AGAN_DEFAULT_LLM_BASE_URL", settings.default_llm_base_url))
    parser.add_argument("--api-key", default=os.getenv("AGAN_DEFAULT_LLM_API_KEY", settings.default_llm_api_key))
    parser.add_argument("--max-steps", type=int, default=15)
    args = parser.parse_args()

    if not args.api_key:
        print("Error: Provide --api-key or set AGAN_DEFAULT_LLM_API_KEY.", file=sys.stderr)
        sys.exit(1)

    session_id = f"mvp-{uuid.uuid4().hex[:8]}"
    bus = SessionEventBus()
    bus.add_hook(_print_event)

    sandbox_mgr = SandboxManager()
    sandbox = await sandbox_mgr.create_or_get(
        session_id,
        SandboxConfig(
            image=args.image,
            attachments_host_dir=args.attachments_dir,
        ),
    )

    llm_client = OpenAICompatChatClient(
        LLMModelConfig(
            base_url=args.base_url,
            api_key=args.api_key,
            model_id=args.model,
        )
    )

    try:
        ctx = LoopContext(
            session_id=session_id,
            challenge=ChallengeBrief(
                title=args.title,
                category=args.category,
                description=args.desc,
            ),
            sandbox=sandbox,
            llm_client=llm_client,
            tool_registry=create_default_tool_registry(),
            event_bus=bus,
            guardrails=GuardrailConfig(max_steps=args.max_steps),
        )
        engine = create_loop_engine(args.engine)
        result = await engine.run(ctx)
        print("\n=== MVP RUN FINISHED ===")
        print(result.model_dump_json(indent=2))
    finally:
        await llm_client.close()
        await sandbox_mgr.remove(session_id, remove_container=True)


if __name__ == "__main__":
    asyncio.run(main())
