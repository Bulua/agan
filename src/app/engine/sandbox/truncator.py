from __future__ import annotations

from pydantic import BaseModel


class TruncationResult(BaseModel):
    text: str
    truncated: bool = False
    total_lines: int = 0
    total_chars: int = 0
    full_log_path: str | None = None


class OutputTruncator:
    """Prevents oversized terminal outputs from overflowing LLM context windows.

    Preserves head + tail lines and appends a standardized notice instructing
    the Agent to inspect the complete output file inside the sandbox via
    grep / head / tail / sed.
    """

    def __init__(
        self,
        max_chars: int = 8000,
        max_lines: int = 120,
        head_lines: int = 50,
        tail_lines: int = 50,
    ) -> None:
        self.max_chars = max_chars
        self.max_lines = max_lines
        self.head_lines = head_lines
        self.tail_lines = tail_lines

    def truncate(self, raw_text: str, full_log_path: str | None = None) -> TruncationResult:
        """Truncate an in-memory output string if it exceeds char or line thresholds."""
        if not raw_text:
            return TruncationResult(
                text="",
                truncated=False,
                total_lines=0,
                total_chars=0,
                full_log_path=full_log_path,
            )

        lines = raw_text.splitlines()
        total_lines = len(lines)
        total_chars = len(raw_text)

        if total_chars <= self.max_chars and total_lines <= self.max_lines:
            return TruncationResult(
                text=raw_text,
                truncated=False,
                total_lines=total_lines,
                total_chars=total_chars,
                full_log_path=full_log_path,
            )

        half_budget = max(500, self.max_chars // 2)

        if total_lines > self.head_lines + self.tail_lines:
            head_part = "\n".join(lines[: self.head_lines])
            tail_part = "\n".join(lines[-self.tail_lines :])
            omitted_lines = total_lines - self.head_lines - self.tail_lines
        else:
            # Few very long lines (e.g., minified JS / hex dump on 1 line)
            head_part = raw_text[:half_budget]
            tail_part = raw_text[-half_budget:]
            omitted_lines = 0

        if len(head_part) > half_budget:
            head_part = head_part[:half_budget] + "\n... [head truncated by char limit]"
        if len(tail_part) > half_budget:
            tail_part = "[tail truncated by char limit] ...\n" + tail_part[-half_budget:]

        formatted = self.format_truncated_output(
            head_text=head_part,
            tail_text=tail_part,
            total_lines=total_lines,
            total_chars=total_chars,
            omitted_lines=omitted_lines,
            full_log_path=full_log_path,
        )
        return TruncationResult(
            text=formatted,
            truncated=True,
            total_lines=total_lines,
            total_chars=total_chars,
            full_log_path=full_log_path,
        )

    def format_truncated_output(
        self,
        *,
        head_text: str,
        tail_text: str,
        total_lines: int,
        total_chars: int,
        omitted_lines: int | None = None,
        full_log_path: str | None = None,
    ) -> str:
        """Assemble head + notice + tail for both in-memory and container-side truncation."""
        omitted_desc = (
            f"{omitted_lines} lines omitted"
            if omitted_lines and omitted_lines > 0
            else "middle content omitted"
        )
        notice_lines = [
            "",
            f"========== [OUTPUT TRUNCATED: {total_lines} lines / {total_chars} bytes total; {omitted_desc}] ==========",
        ]
        if full_log_path:
            notice_lines.append(
                f"Full output saved inside sandbox at: {full_log_path}"
            )
            notice_lines.append(
                f"Hint: inspect specific parts with `grep -n <pattern> {full_log_path}`, "
                f"`sed -n '51,120p' {full_log_path}`, `head`, or `tail`."
            )
        notice_lines.append(
            "=========================================================================="
        )
        notice_lines.append("")
        return f"{head_text}\n" + "\n".join(notice_lines) + f"\n{tail_text}"
