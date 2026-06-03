"""SlidingWindowLimiter unit tests — public-deployment.md § 24.3, fixtures 5-8.

A deterministic injected clock makes the time-window behaviour testable without
real sleeps.
"""

from __future__ import annotations

from mahjong.server.ratelimit import SlidingWindowLimiter


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ---------------------------------------------------------------------------
# Fixture 5: budget exhausts within the window
# ---------------------------------------------------------------------------


def test_allow_exhausts_budget() -> None:
    clk = FakeClock()
    lim = SlidingWindowLimiter(max_events=3, window_s=10, clock=clk)
    assert lim.allow("k") is True
    assert lim.allow("k") is True
    assert lim.allow("k") is True
    assert lim.allow("k") is False  # 4th in-window → over budget


# ---------------------------------------------------------------------------
# Fixture 6: budget refreshes once the window slides past
# ---------------------------------------------------------------------------


def test_budget_refreshes_after_window() -> None:
    clk = FakeClock()
    lim = SlidingWindowLimiter(max_events=2, window_s=10, clock=clk)
    assert lim.allow("k") is True
    assert lim.allow("k") is True
    assert lim.allow("k") is False
    clk.advance(11)  # both recorded events are now older than the window
    assert lim.allow("k") is True


def test_partial_window_slide_only_drops_expired_events() -> None:
    clk = FakeClock()
    lim = SlidingWindowLimiter(max_events=2, window_s=10, clock=clk)
    assert lim.allow("k") is True  # t=1000
    clk.advance(6)
    assert lim.allow("k") is True  # t=1006
    assert lim.allow("k") is False  # 2 in window
    clk.advance(5)  # t=1011: the t=1000 event expired, t=1006 still in window
    assert lim.allow("k") is True  # one slot freed
    assert lim.allow("k") is False  # back at 2


# ---------------------------------------------------------------------------
# Fixture 7: independent budgets per key
# ---------------------------------------------------------------------------


def test_keys_have_independent_budgets() -> None:
    clk = FakeClock()
    lim = SlidingWindowLimiter(max_events=1, window_s=10, clock=clk)
    assert lim.allow("a") is True
    assert lim.allow("a") is False
    assert lim.allow("b") is True  # b unaffected by a's exhaustion
    assert lim.allow("b") is False


# ---------------------------------------------------------------------------
# Fixture 8: idle keys are evicted by sweep (bounds memory)
# ---------------------------------------------------------------------------


def test_sweep_evicts_idle_keys() -> None:
    clk = FakeClock()
    lim = SlidingWindowLimiter(max_events=3, window_s=10, clock=clk)
    lim.allow("a")
    lim.allow("b")
    assert lim.active_keys() == 2
    clk.advance(11)  # every recorded event is now expired
    lim.sweep()
    assert lim.active_keys() == 0


def test_sweep_keeps_keys_with_live_events() -> None:
    clk = FakeClock()
    lim = SlidingWindowLimiter(max_events=3, window_s=10, clock=clk)
    lim.allow("a")
    clk.advance(11)
    lim.allow("b")  # fresh
    lim.sweep()
    assert lim.active_keys() == 1  # "a" gone, "b" stays


# ---------------------------------------------------------------------------
# would_allow / record split (for the login path: peek, then record on failure)
# ---------------------------------------------------------------------------


def test_would_allow_does_not_consume_budget() -> None:
    clk = FakeClock()
    lim = SlidingWindowLimiter(max_events=2, window_s=10, clock=clk)
    # Peeking many times never consumes the budget.
    for _ in range(10):
        assert lim.would_allow("k") is True
    # Only explicit records consume it.
    lim.record("k")
    lim.record("k")
    assert lim.would_allow("k") is False


def test_would_allow_unknown_key_is_true() -> None:
    lim = SlidingWindowLimiter(max_events=1, window_s=10, clock=FakeClock())
    assert lim.would_allow("never-seen") is True
    assert lim.active_keys() == 0  # peeking does not create an entry
