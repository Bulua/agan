from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import secret_box
from app.engine.llm.chat_client import OpenAICompatChatClient
from app.engine.llm.types import LLMMessage, LLMModelConfig
from app.models.llm import LLMModel, ModelType
from app.models.session import SessionEvent, SessionScratchpad, SolveSession, Writeup


def _build_trace_digest(events: list[SessionEvent], max_chars: int = 24_000) -> str:
    lines: list[str] = []
    for ev in events:
        if ev.event_type in ("token_usage",):
            continue
        payload_str = json.dumps(ev.payload, ensure_ascii=False)
        if len(payload_str) > 900:
            payload_str = payload_str[:900] + "... [truncated]"
        lines.append(f"[seq={ev.seq_id} step={ev.step_index} {ev.event_type}] {payload_str}")
    digest = "\n".join(lines)
    if len(digest) > max_chars:
        half = max_chars // 2
        digest = digest[:half] + "\n\n... [middle trace omitted] ...\n\n" + digest[-half:]
    return digest or "(No trace events recorded yet.)"


async def generate_session_writeup(
    db: AsyncSession,
    session_obj: SolveSession,
    *,
    model_id: str | None = None,
    language: str = "zh-CN",
) -> Writeup:
    """Summarize full-chain session trace + scratchpad into a structured Markdown CTF Writeup."""
    target_model_id = model_id or session_obj.chat_model_id
    model_obj = await db.get(LLMModel, target_model_id) if target_model_id else None
    if model_obj is None:
        model_obj = await db.scalar(
            select(LLMModel)
            .where(LLMModel.model_type == ModelType.CHAT.value, LLMModel.is_active.is_(True))
            .order_by(LLMModel.is_default.desc(), LLMModel.created_at.desc())
        )
    if model_obj is None:
        raise ValueError("No active Chat LLM model available to generate Writeup.")

    events = list(
        (
            await db.execute(
                select(SessionEvent)
                .where(SessionEvent.session_id == session_obj.id)
                .order_by(SessionEvent.seq_id.asc())
            )
        )
        .scalars()
        .all()
    )
    sp = await db.scalar(
        select(SessionScratchpad).where(SessionScratchpad.session_id == session_obj.id)
    )

    lang_instruction = (
        "Write the entire Writeup in clear, professional Simplified Chinese (简体中文)."
        if language.lower().startswith("zh")
        else "Write the entire Writeup in clear, professional English."
    )

    scratchpad_json = (
        json.dumps(
            {
                "known_clues": sp.known_clues,
                "endpoints_ports": sp.endpoints_ports,
                "vulnerabilities": sp.vulnerabilities,
                "ruled_out": sp.ruled_out,
                "notes_md": sp.notes_md,
            },
            ensure_ascii=False,
            indent=2,
        )
        if sp
        else "{}"
    )

    user_prompt = (
        f"# Challenge Metadata\n"
        f"- Title: {session_obj.title}\n"
        f"- Category: {session_obj.category}\n"
        f"- Status: {session_obj.status}\n"
        f"- Loop Engine: {session_obj.loop_engine}\n"
        f"- Detected Flag: {session_obj.detected_flag or '(Not recovered)'}\n\n"
        f"## Description\n{session_obj.description or '(None)'}\n\n"
        f"## Working Scratchpad\n```json\n{scratchpad_json}\n```\n\n"
        f"## Full Execution Trace\n{_build_trace_digest(events)}"
    )

    system_prompt = (
        "You are a senior CTF player and technical writer. Based strictly on the provided "
        "execution trace and scratchpad, produce a clean, reproducible Markdown CTF Writeup.\n"
        f"{lang_instruction}\n\n"
        "Include sections:\n"
        "1. Challenge Overview & Metadata\n"
        "2. Reconnaissance & Initial Analysis\n"
        "3. Vulnerability / Core Logic Discovery\n"
        "4. Exploitation Steps & Key Commands / Scripts\n"
        "5. Flag & Summary (or Post-Mortem / Next Steps if unsolved)\n"
        "Do NOT fabricate commands or flags that do not appear in the trace."
    )

    client = OpenAICompatChatClient(
        LLMModelConfig(
            name=model_obj.name,
            base_url=model_obj.base_url,
            api_key=secret_box.decrypt(model_obj.api_key_encrypted),
            model_id=model_obj.model_id,
            context_window=model_obj.context_window,
            max_output_tokens=min(4096, model_obj.max_output_tokens),
            temperature=0.3,
            input_price_per_1m=model_obj.input_price_per_1m,
            output_price_per_1m=model_obj.output_price_per_1m,
            extra_params=model_obj.extra_params or {},
        )
    )
    try:
        resp = await client.chat(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
    finally:
        await client.close()

    content_md = resp.content.strip() or "# Writeup\n\n(Empty model output.)"
    title = f"Writeup: {session_obj.title}"

    existing = await db.scalar(select(Writeup).where(Writeup.session_id == session_obj.id))
    if existing is not None:
        existing.title = title
        existing.content_md = content_md
        existing.model_used = model_obj.model_id
        writeup = existing
    else:
        writeup = Writeup(
            session_id=session_obj.id,
            title=title,
            content_md=content_md,
            model_used=model_obj.model_id,
        )
        db.add(writeup)

    await db.flush()
    await db.refresh(writeup)
    return writeup
