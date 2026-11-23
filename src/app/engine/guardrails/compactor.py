from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.engine.llm.chat_client import BaseChatClient
from app.engine.llm.types import LLMMessage
from app.engine.memory.scratchpad import WorkingScratchpad

MICRO_CLEARED_PREFIX = "[Earlier tool output cleared by micro-compact"

STRUCTURED_COMPACT_SYSTEM_PROMPT = """\
You are a context compaction specialist for an autonomous AI agent.
Summarize the conversation transcript below into a concise, high-signal handoff brief so the \
agent can continue seamlessly without losing critical state.

Organize the summary using these Markdown sections (omit any section that has nothing relevant):
### Current Goal
What the user is trying to accomplish and any active constraints.

### Completed Work & Verified Facts
Concrete artifacts, exact file paths, function/class names, commands executed, URLs inspected, \
and confirmed results.

### Key Decisions & User Guidance
Preferences, design choices, and any mid-task corrections or steering from the user.

### Errors & Ruled-Out Directions
Failed attempts, root causes, and approaches not to repeat.

### Pending Next Steps
Remaining tasks, open questions, or immediate next action.

Rules:
- Preserve exact paths, identifiers, error messages, and numbers verbatim.
- Be dense and factual; drop conversational fluff and raw command dumps."""


@dataclass
class CompactionStats:
    """Telemetry produced by a micro-compact or full summary compaction pass."""

    stage: str = "none"  # "none" | "micro" | "auto" | "manual"
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    cleared_tool_outputs: int = 0
    summarized_messages: int = 0
    summary_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "tokens_saved": self.tokens_saved,
            "cleared_tool_outputs": self.cleared_tool_outputs,
            "summarized_messages": self.summarized_messages,
        }


class ContextCompactor:
    """Two-stage Claude-style context compactor (Micro-Compact + Auto/Manual Summarization).

    Stage 1 (Micro-Compact — zero LLM calls):
        Clears oversized historical `role="tool"` outputs older than `keep_recent_tool_results`
        in-place with a compact pointer, deferring full summarization at zero token cost.
    Stage 2 (Auto / Manual Compact — LLM structured handoff):
        When estimated or reported prompt tokens cross `context_window * compact_ratio` (or on
        explicit `/compact`), folds earlier turns into a structured Markdown handoff brief while
        preserving leading system prompts, pinned scratchpad/shared blocks, and recent turns.
    """

    def __init__(
        self,
        context_window: int = 128_000,
        compact_ratio: float = 0.75,
        keep_recent: int = 10,
        keep_recent_tool_results: int = 4,
        micro_min_chars: int = 600,
    ) -> None:
        self.context_window = max(4096, int(context_window or 128_000))
        self.compact_ratio = max(0.35, min(float(compact_ratio or 0.75), 0.95))
        self.keep_recent = max(2, int(keep_recent or 10))
        self.keep_recent_tool_results = max(1, int(keep_recent_tool_results))
        self.micro_min_chars = max(120, int(micro_min_chars))

    @staticmethod
    def estimate_tokens(messages: list[LLMMessage]) -> int:
        """Fast char-based heuristic (~3.5 chars/token across mixed English/code/Chinese)."""
        total_chars = 0
        for m in messages:
            total_chars += len(m.content or "")
            for tc in m.tool_calls:
                total_chars += len(tc.name) + len(tc.raw_arguments or "")
        return int(total_chars / 3.5) + len(messages) * 8

    def threshold_tokens(self) -> int:
        return int(self.context_window * self.compact_ratio)

    def needs_compaction(
        self,
        messages: list[LLMMessage],
        last_prompt_tokens: int = 0,
        *,
        min_messages_to_compact: int | None = None,
    ) -> bool:
        threshold = self.threshold_tokens()
        estimated = max(int(last_prompt_tokens or 0), self.estimate_tokens(messages))
        min_count = (
            min_messages_to_compact
            if min_messages_to_compact is not None
            else (self.keep_recent + 2)
        )
        return estimated >= threshold and len(messages) > min_count

    def micro_compact(
        self,
        messages: list[LLMMessage],
        *,
        keep_recent_tool_results: int | None = None,
        min_chars_to_clear: int | None = None,
    ) -> tuple[list[LLMMessage], CompactionStats]:
        """Stage 1: Zero-cost replacement of older oversized `tool` outputs."""
        keep_n = (
            self.keep_recent_tool_results
            if keep_recent_tool_results is None
            else max(1, keep_recent_tool_results)
        )
        min_chars = (
            self.micro_min_chars if min_chars_to_clear is None else max(80, min_chars_to_clear)
        )

        tokens_before = self.estimate_tokens(messages)
        tool_indices = [idx for idx, m in enumerate(messages) if m.role == "tool"]
        if len(tool_indices) <= keep_n:
            return list(messages), CompactionStats(
                stage="none",
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                tokens_saved=0,
            )

        clearable_indices = set(tool_indices[:-keep_n])
        out: list[LLMMessage] = []
        cleared_count = 0

        for idx, m in enumerate(messages):
            if idx in clearable_indices:
                raw = m.content or ""
                if len(raw) >= min_chars and not raw.startswith(MICRO_CLEARED_PREFIX):
                    tool_label = m.name or "tool"
                    placeholder = (
                        f"{MICRO_CLEARED_PREFIX} (tool={tool_label}, {len(raw)} chars) "
                        "— re-read or re-run if still needed]"
                    )
                    out.append(
                        LLMMessage(
                            role="tool",
                            content=placeholder,
                            tool_call_id=m.tool_call_id,
                            name=m.name,
                        )
                    )
                    cleared_count += 1
                    continue
            out.append(m)

        tokens_after = self.estimate_tokens(out)
        saved = max(0, tokens_before - tokens_after)
        return out, CompactionStats(
            stage="micro" if cleared_count > 0 else "none",
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_saved=saved,
            cleared_tool_outputs=cleared_count,
        )

    async def compact_with_stats(
        self,
        messages: list[LLMMessage],
        *,
        scratchpad: WorkingScratchpad | None = None,
        pinned_blocks: list[str] | None = None,
        llm_client: BaseChatClient | None = None,
        extra_instructions: str = "",
        force: bool = False,
        last_prompt_tokens: int = 0,
        keep_recent_override: int | None = None,
        preserve_first_user: bool = True,
    ) -> tuple[list[LLMMessage], CompactionStats]:
        """Run Stage 1 Micro-Compact, then Stage 2 LLM Summarization if forced or over threshold."""
        initial_tokens = max(int(last_prompt_tokens or 0), self.estimate_tokens(messages))

        # Stage 1: always micro-compact first (free!)
        working, micro_stats = self.micro_compact(messages)

        keep_tail = (
            max(1, int(keep_recent_override))
            if keep_recent_override is not None
            else self.keep_recent
        )

        # Check whether Stage 2 is needed
        if not force:
            # After micro-compact freed tokens, re-evaluate using the reduced token count
            effective_after_micro = max(
                self.estimate_tokens(working),
                max(0, int(last_prompt_tokens or 0) - micro_stats.tokens_saved),
            )
            if effective_after_micro < self.threshold_tokens():
                return working, micro_stats

        # Preserve leading system message(s) (and optionally initial user brief)
        prefix_end = 0
        while prefix_end < len(working) and working[prefix_end].role == "system":
            prefix_end += 1
        if preserve_first_user and prefix_end < len(working) and working[prefix_end].role == "user":
            # Only keep initial user message separately when enough messages follow
            if len(working) - prefix_end > keep_tail + 1:
                prefix_end += 1

        tail_start = max(prefix_end, len(working) - keep_tail)
        # Never orphan `tool` responses from their preceding `assistant(tool_calls)`
        while tail_start < len(working) and working[tail_start].role == "tool":
            tail_start += 1
        # If tail_start pushed all the way to the end while forcing, step back to include the assistant call
        if force and tail_start >= len(working) and prefix_end < len(working):
            tail_start = max(prefix_end, len(working) - 1)
            while tail_start > prefix_end and working[tail_start].role == "tool":
                tail_start -= 1

        middle = working[prefix_end:tail_start]
        if not middle:
            return working, micro_stats

        summary_text = await self._summarize_middle(
            middle,
            llm_client,
            extra_instructions=extra_instructions,
        )

        sections = ["[CONTEXT COMPACTED — EARLIER TURNS SUMMARIZED]"]
        if scratchpad is not None and not scratchpad.is_empty():
            sections.append(scratchpad.to_prompt_block())
        for block in pinned_blocks or []:
            if block and block.strip():
                sections.append(block.strip())
        sections.append(f"## Summary of Earlier Actions\n{summary_text}")
        pinned_message = "\n\n".join(sections)

        compacted = [
            *working[:prefix_end],
            LLMMessage(role="user", content=pinned_message),
            *working[tail_start:],
        ]

        tokens_after = self.estimate_tokens(compacted)
        saved = max(0, initial_tokens - tokens_after)
        return compacted, CompactionStats(
            stage="manual" if force else "auto",
            tokens_before=initial_tokens,
            tokens_after=tokens_after,
            tokens_saved=saved,
            cleared_tool_outputs=micro_stats.cleared_tool_outputs,
            summarized_messages=len(middle),
            summary_text=summary_text,
        )

    async def compact(
        self,
        messages: list[LLMMessage],
        scratchpad: WorkingScratchpad,
        llm_client: BaseChatClient | None = None,
    ) -> list[LLMMessage]:
        """Backward-compatible wrapper preserving pinned scratchpad + summary + recent turns."""
        if len(messages) <= self.keep_recent + 2:
            return messages
        compacted, _ = await self.compact_with_stats(
            messages,
            scratchpad=scratchpad,
            llm_client=llm_client,
            force=True,
            preserve_first_user=True,
        )
        return compacted

    async def _summarize_middle(
        self,
        middle: list[LLMMessage],
        llm_client: BaseChatClient | None,
        *,
        extra_instructions: str = "",
    ) -> str:
        transcript_lines: list[str] = []
        for m in middle:
            snippet = (m.content or "").strip()
            if len(snippet) > 600:
                snippet = snippet[:600] + "..."
            if m.tool_calls:
                calls = ", ".join(f"{tc.name}({tc.raw_arguments[:140]})" for tc in m.tool_calls)
                transcript_lines.append(f"[{m.role}] {snippet} | calls: {calls}")
            elif m.role == "tool":
                transcript_lines.append(f"[tool:{m.name or 'tool'}] {snippet}")
            else:
                transcript_lines.append(f"[{m.role}] {snippet}")

        compact_source = "\n".join(transcript_lines)
        if len(compact_source) > 16_000:
            compact_source = compact_source[:16_000] + "\n... [truncated]"

        user_prompt = compact_source
        if extra_instructions.strip():
            user_prompt = (
                f"Additional focus instructions from user:\n{extra_instructions.strip()}\n\n"
                f"---\nTranscript to summarize:\n{compact_source}"
            )

        if llm_client is not None:
            try:
                resp = await llm_client.chat(
                    [
                        LLMMessage(role="system", content=STRUCTURED_COMPACT_SYSTEM_PROMPT),
                        LLMMessage(role="user", content=user_prompt),
                    ],
                    temperature=0.1,
                    max_tokens=1000,
                )
                if resp.content.strip():
                    return resp.content.strip()
            except Exception:
                pass

        # Deterministic fallback summary if LLM summarization fails or client omitted
        return "\n".join(transcript_lines[-24:])
