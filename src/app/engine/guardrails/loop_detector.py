from __future__ import annotations

import json
import re
from collections import deque
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel


class LoopDetectionResult(BaseModel):
    detected: bool = False
    repeat_count: int = 0
    matched_signature: str = ""
    reflexion_prompt: str = ""


class DeadLoopDetector:
    """Detects repetitive / stalled tool invocations over a sliding window.

    When the Agent issues >= `threshold` highly similar commands or tool calls
    in recent turns, emits a mandatory Reflexion intervention prompt instructing
    the Agent to pivot strategy and log the dead end in Working Scratchpad.
    """

    def __init__(
        self,
        threshold: int = 3,
        similarity_threshold: float = 0.82,
        window_size: int = 8,
    ) -> None:
        self.threshold = max(2, threshold)
        self.similarity_threshold = similarity_threshold
        self._history: deque[tuple[str, str]] = deque(maxlen=max(window_size, threshold * 2))

    @staticmethod
    def _normalize(tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "bash_exec":
            cmd = str(arguments.get("command", "")).strip()
            # Collapse whitespace and normalize trailing numbers/temp filenames lightly
            cmd = re.sub(r"\s+", " ", cmd)
            return f"bash_exec::{cmd}"
        canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        canonical = re.sub(r"\s+", " ", canonical)
        return f"{tool_name}::{canonical}"

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        if a == b:
            return 1.0
        tool_a, _, body_a = a.partition("::")
        tool_b, _, body_b = b.partition("::")
        if tool_a != tool_b:
            return 0.0
        if not body_a and not body_b:
            return 1.0
        seq_ratio = SequenceMatcher(None, body_a, body_b).ratio()
        tokens_a = set(re.findall(r"\w+", body_a.lower()))
        tokens_b = set(re.findall(r"\w+", body_b.lower()))
        if tokens_a and tokens_b:
            jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
        else:
            jaccard = 0.0
        return max(seq_ratio, jaccard)

    def record_and_check(self, tool_name: str, arguments: dict[str, Any]) -> LoopDetectionResult:
        sig = self._normalize(tool_name, arguments)
        similar_count = 1
        for prev_sig, _ in self._history:
            if self._similarity(sig, prev_sig) >= self.similarity_threshold:
                similar_count += 1

        self._history.append((sig, tool_name))

        if similar_count >= self.threshold:
            prompt = (
                f"[SYSTEM REFLEXION INTERVENTION — DEAD LOOP DETECTED]\n"
                f"You have repeated a nearly identical `{tool_name}` action {similar_count} times "
                f"recently (`{sig[:180]}`) without making progress.\n"
                f"STOP repeating this command immediately. You MUST:\n"
                f"1. Diagnose WHY the previous attempts failed or yielded no new information.\n"
                f"2. Record this failed hypothesis in `ruled_out` via `scratchpad_update`.\n"
                f"3. Switch to a fundamentally different angle, tool, or payload."
            )
            return LoopDetectionResult(
                detected=True,
                repeat_count=similar_count,
                matched_signature=sig,
                reflexion_prompt=prompt,
            )

        return LoopDetectionResult(detected=False, repeat_count=similar_count, matched_signature=sig)

    def reset(self) -> None:
        self._history.clear()
