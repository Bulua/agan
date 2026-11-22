from app.engine.sandbox.truncator import OutputTruncator


def test_small_output_not_truncated() -> None:
    truncator = OutputTruncator(max_chars=500, max_lines=20, head_lines=5, tail_lines=5)
    text = "\n".join(f"line {i}" for i in range(1, 11))
    res = truncator.truncate(text, full_log_path="/tmp/agan_logs/cmd_1.log")
    assert res.truncated is False
    assert res.text == text
    assert res.total_lines == 10


def test_many_lines_truncated_preserves_head_and_tail() -> None:
    truncator = OutputTruncator(max_chars=10000, max_lines=30, head_lines=5, tail_lines=5)
    text = "\n".join(f"row_{i:04d}" for i in range(1, 201))
    res = truncator.truncate(text, full_log_path="/tmp/agan_logs/cmd_big.log")
    assert res.truncated is True
    assert res.total_lines == 200
    assert "row_0001" in res.text
    assert "row_0005" in res.text
    assert "row_0050" not in res.text
    assert "row_0196" in res.text
    assert "row_0200" in res.text
    assert "/tmp/agan_logs/cmd_big.log" in res.text
    assert "grep -n" in res.text


def test_single_huge_line_truncated_by_char_budget() -> None:
    truncator = OutputTruncator(max_chars=1200, max_lines=100, head_lines=10, tail_lines=10)
    raw = "A" * 2000 + "MIDDLE" + "Z" * 2000
    res = truncator.truncate(raw, full_log_path="/tmp/agan_logs/cmd_hex.log")
    assert res.truncated is True
    assert "AAA" in res.text
    assert "ZZZ" in res.text
    assert "MIDDLE" not in res.text
    assert "/tmp/agan_logs/cmd_hex.log" in res.text
