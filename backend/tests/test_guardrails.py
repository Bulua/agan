import pytest

from app.engine.guardrails.compactor import ContextCompactor
from app.engine.guardrails.limits import BudgetTracker, GuardrailConfig, GuardrailViolation
from app.engine.guardrails.loop_detector import DeadLoopDetector
from app.engine.llm.types import LLMMessage, TokenUsage
from app.engine.memory.scratchpad import WorkingScratchpad


def test_budget_tracker_trips_on_max_steps() -> None:
    tracker = BudgetTracker(GuardrailConfig(max_steps=2))
    tracker.check_limits()
    tracker.record_step()
    tracker.check_limits()
    tracker.record_step()
    with pytest.raises(GuardrailViolation) as exc_info:
        tracker.check_limits()
    assert exc_info.value.code == "max_steps_exceeded"


def test_budget_tracker_trips_on_token_budget() -> None:
    tracker = BudgetTracker(GuardrailConfig(max_steps=50, token_budget=1000))
    tracker.record_usage(TokenUsage(prompt_tokens=600, completion_tokens=500, total_tokens=1100))
    with pytest.raises(GuardrailViolation) as exc_info:
        tracker.check_limits()
    assert exc_info.value.code == "token_budget_exceeded"


def test_dead_loop_detector_catches_repeated_similar_commands() -> None:
    detector = DeadLoopDetector(threshold=3, similarity_threshold=0.80)
    r1 = detector.record_and_check("bash_exec", {"command": "curl -s http://target:8080/admin?id=1"})
    r2 = detector.record_and_check("bash_exec", {"command": "curl -s http://target:8080/admin?id=2"})
    r3 = detector.record_and_check("bash_exec", {"command": "curl -s http://target:8080/admin?id=3"})
    assert r1.detected is False
    assert r2.detected is False
    assert r3.detected is True
    assert r3.repeat_count >= 3
    assert "DEAD LOOP DETECTED" in r3.reflexion_prompt


@pytest.mark.asyncio
async def test_context_compactor_preserves_pinned_scratchpad() -> None:
    scratchpad = WorkingScratchpad(
        endpoints_ports=["http://target:5000/login"],
        vulnerabilities=["Flask Jinja2 SSTI in `name` param"],
        ruled_out=["SQL injection on /login"],
    )
    compactor = ContextCompactor(context_window=4096, compact_ratio=0.1, keep_recent=4)
    messages = [
        LLMMessage(role="system", content="System prompt"),
        LLMMessage(role="user", content="Challenge brief"),
    ]
    for i in range(12):
        messages.append(LLMMessage(role="assistant", content=f"Investigating step {i} " + "x" * 300))
        messages.append(LLMMessage(role="user", content=f"Observation {i} " + "y" * 300))

    assert compactor.needs_compaction(messages) is True
    compacted = await compactor.compact(messages, scratchpad, llm_client=None)
    assert len(compacted) < len(messages)
    assert compacted[0].content == "System prompt"
    assert compacted[1].content == "Challenge brief"
    # Pinned scratchpad must appear inside the compacted summary block
    assert "Flask Jinja2 SSTI" in (compacted[2].content or "")
    assert "SQL injection on /login" in (compacted[2].content or "")
