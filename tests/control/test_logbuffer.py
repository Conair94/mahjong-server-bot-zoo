"""LogRingBuffer — bounded tail of the supervised server's output.

Spec: docs/specs/admin-console.md § 2 (log ring buffer).  Sync tests, kept out
of the asyncio-marked files per the pytest-asyncio mode quirk.
"""

from __future__ import annotations

from mahjong.control.logbuffer import LogRingBuffer


def test_append_assigns_monotonic_line_numbers() -> None:
    buf = LogRingBuffer(maxlen=10)
    a = buf.append("first", "stdout")
    b = buf.append("second", "stderr")
    assert a.line == 1
    assert b.line == 2
    assert a.text == "first" and a.stream == "stdout"
    assert b.stream == "stderr"


def test_recent_returns_tail_in_order() -> None:
    buf = LogRingBuffer(maxlen=10)
    for i in range(5):
        buf.append(f"line-{i}", "stdout")
    recent = buf.recent(limit=3)
    assert [ln.text for ln in recent] == ["line-2", "line-3", "line-4"]


def test_recent_without_limit_returns_everything() -> None:
    buf = LogRingBuffer(maxlen=10)
    for i in range(3):
        buf.append(f"line-{i}", "stdout")
    assert [ln.text for ln in buf.recent()] == ["line-0", "line-1", "line-2"]


def test_since_returns_only_newer_lines() -> None:
    buf = LogRingBuffer(maxlen=10)
    for i in range(4):
        buf.append(f"line-{i}", "stdout")  # lines 1..4
    after = buf.since(2)  # strictly greater than cursor 2
    assert [ln.line for ln in after] == [3, 4]
    assert buf.since(4) == []  # nothing newer than the latest


def test_maxlen_evicts_oldest_but_keeps_line_numbers() -> None:
    buf = LogRingBuffer(maxlen=3)
    for i in range(5):
        buf.append(f"line-{i}", "stdout")  # lines 1..5, only last 3 retained
    recent = buf.recent()
    assert [ln.line for ln in recent] == [3, 4, 5]
    assert [ln.text for ln in recent] == ["line-2", "line-3", "line-4"]
    # A cursor pointing into evicted territory still yields whatever survives.
    assert [ln.line for ln in buf.since(0)] == [3, 4, 5]
