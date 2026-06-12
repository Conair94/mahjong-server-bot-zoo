"""PacedAdapter — wall-clock pacing wrapper.

Spec: docs/specs/layer8-closeout.md § §2 Bot pacing.

The wrapper sleeps ``uniform(min_s, max_s)`` before each ``decide`` to
make bot turns visible at human reading speed at multi-human tables.
Tests use a monkey-patched ``asyncio.sleep`` so the suite stays fast
(< 0.1 s for the full file) while still pinning the delay-sampling and
budget-clamping behaviour.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, ClassVar, cast

import pytest

from mahjong.adapters.canned import CannedAdapter
from mahjong.adapters.paced import PacedAdapter
from mahjong.engine.types import Action

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _InstantAdapter:
    """A bot-like adapter that records every ``decide`` call and returns
    the prompt's ``default_action`` without sleeping."""

    identity: ClassVar[dict[str, Any]] = {"kind": "canned", "script": "instant-test"}
    kind = "bot"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def seated(self, ctx: dict[str, Any]) -> None:
        return None

    async def observe(self, event: dict[str, Any], view: dict[str, Any]) -> None:
        return None

    async def decide(self, prompt: dict[str, Any]) -> Action:
        self.calls.append(prompt)
        return cast(Action, prompt["default_action"])

    async def left(self, reason: str) -> None:
        return None


def _prompt(*, deadline_in: float = 60.0, default_action: Action | None = None) -> dict[str, Any]:
    """Build a minimal Prompt-shaped dict with a deadline ``deadline_in``
    seconds in the future (relative to the asyncio loop clock)."""
    loop = asyncio.get_event_loop()
    now = loop.time()
    if default_action is None:
        default_action = cast(Action, {"action": "PASS"})
    return {
        "kind": "DISCARD",
        "view": {},
        "legal_actions": [default_action],
        "default_action": default_action,
        "issued_at": now,
        "deadline": now + deadline_in,
        "context": {},
    }


class _SleepRecorder:
    """Monkey-patch target: replaces ``asyncio.sleep`` and records the
    requested durations without actually sleeping.  Restored on exit."""

    def __init__(self) -> None:
        self.durations: list[float] = []
        self._original: Any = None

    def __enter__(self) -> _SleepRecorder:
        self._original = asyncio.sleep

        async def fake_sleep(delay: float, *args: Any, **kwargs: Any) -> None:
            self.durations.append(delay)

        asyncio.sleep = fake_sleep  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc: Any) -> None:
        asyncio.sleep = self._original


# ---------------------------------------------------------------------------
# Fixture 1 — sampled delay sits inside (min_s, max_s)
# ---------------------------------------------------------------------------


async def test_fixture_1_delay_within_range() -> None:
    inner = _InstantAdapter()
    paced = PacedAdapter(inner, min_s=0.05, max_s=0.10, rng=random.Random(123))

    with _SleepRecorder() as rec:
        for _ in range(20):
            await paced.decide(_prompt())

    assert len(rec.durations) == 20
    for d in rec.durations:
        assert 0.05 <= d <= 0.10, d


# ---------------------------------------------------------------------------
# Fixture 2 — delay clamped under tight deadline
# ---------------------------------------------------------------------------


async def test_fixture_2_delay_clamped_by_deadline() -> None:
    """With ``deadline - issued_at = 1.0`` and the sampler trying to sleep
    10–20 s, the actual sleep is clamped to 0.5 s (1.0 minus the 0.5 s
    safety margin)."""
    inner = _InstantAdapter()
    paced = PacedAdapter(inner, min_s=10.0, max_s=20.0, rng=random.Random(7))

    with _SleepRecorder() as rec:
        await paced.decide(_prompt(deadline_in=1.0))

    assert len(rec.durations) == 1
    # 1.0 - 0.5 safety margin = 0.5
    assert rec.durations[0] == pytest.approx(0.5, abs=1e-9), rec.durations[0]


async def test_fixture_2b_zero_or_negative_budget_skips_sleep() -> None:
    """When the deadline is already at-or-past now (budget <= 0), the
    adapter skips ``asyncio.sleep`` entirely instead of sleeping zero
    seconds — the inner adapter still runs."""
    inner = _InstantAdapter()
    paced = PacedAdapter(inner, min_s=1.0, max_s=2.0)

    with _SleepRecorder() as rec:
        await paced.decide(_prompt(deadline_in=0.0))

    # 0.0 - 0.5 = -0.5 → max(0, -0.5) = 0 → no sleep emitted.
    assert rec.durations == []
    # Inner adapter still called.
    assert len(inner.calls) == 1


# ---------------------------------------------------------------------------
# Fixture 3 — identity / kind pass-through
# ---------------------------------------------------------------------------


async def test_fixture_3_identity_and_kind_passthrough() -> None:
    """The wrapper exposes the same ``identity`` and ``kind`` as the inner
    adapter, so the table manager's per-(seat_kind, prompt_kind) decide-
    timeout lookup still sees the seat as its underlying kind."""
    canned = CannedAdapter(
        identity={"kind": "canned", "script": "test"},
        actions=[],
    )
    paced = PacedAdapter(canned, min_s=0.0, max_s=0.01)
    assert paced.kind == "canned"
    assert paced.identity == canned.identity


# ---------------------------------------------------------------------------
# Fixture 4 — inner.decide return value flows through
# ---------------------------------------------------------------------------


async def test_fixture_4_inner_decision_returned() -> None:
    """The wrapper does not alter the action; it just sleeps before
    handing the prompt to the inner."""
    expected: Action = cast(Action, {"action": "PASS"})
    canned = CannedAdapter(
        identity={"kind": "canned", "script": "test"},
        actions=[expected],
    )
    paced = PacedAdapter(canned, min_s=0.0, max_s=0.001, rng=random.Random(0))
    prompt = _prompt(default_action=cast(Action, {"action": "PASS"}))
    prompt["legal_actions"] = [expected]
    result = await paced.decide(cast(Any, prompt))
    assert result == expected


# ---------------------------------------------------------------------------
# Fixture 5 — seated / observe / left pass through
# ---------------------------------------------------------------------------


class _RecordingAdapter:
    """Captures every Protocol method call so pass-through can be asserted."""

    identity: ClassVar[dict[str, Any]] = {
        "kind": "bot",
        "bot_id": "rec",
        "version": "1",
        "runtime": "in_process",
    }
    kind = "bot"

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def seated(self, ctx: Any) -> None:
        self.events.append(("seated", ctx))

    async def observe(self, event: Any, view: Any) -> None:
        self.events.append(("observe", (event, view)))

    async def decide(self, prompt: Any) -> Action:
        self.events.append(("decide", prompt))
        return cast(Action, prompt["default_action"])

    async def left(self, reason: Any) -> None:
        self.events.append(("left", reason))


async def test_fixture_5_lifecycle_passthrough() -> None:
    inner = _RecordingAdapter()
    paced = PacedAdapter(inner, min_s=0.0, max_s=0.001)

    await paced.seated({"seat": 2})
    await paced.observe({"kind": "DEAL"}, {"seat": 2})
    with _SleepRecorder():
        await paced.decide(_prompt())
    await paced.left("HAND_ENDED")

    kinds = [e[0] for e in inner.events]
    assert kinds == ["seated", "observe", "decide", "left"]


# ---------------------------------------------------------------------------
# Fixture 6 — deterministic delay with seeded RNG
# ---------------------------------------------------------------------------


async def test_fixture_6_rng_seeded_deterministic() -> None:
    """Same seed → same delay sequence.  Pins reproducibility for any
    future test that wants deterministic pacing."""
    rng_a = random.Random(42)
    rng_b = random.Random(42)
    paced_a = PacedAdapter(_InstantAdapter(), min_s=1.0, max_s=5.0, rng=rng_a)
    paced_b = PacedAdapter(_InstantAdapter(), min_s=1.0, max_s=5.0, rng=rng_b)

    with _SleepRecorder() as rec_a:
        for _ in range(5):
            await paced_a.decide(_prompt(deadline_in=100.0))
    with _SleepRecorder() as rec_b:
        for _ in range(5):
            await paced_b.decide(_prompt(deadline_in=100.0))

    assert rec_a.durations == rec_b.durations


# ---------------------------------------------------------------------------
# Fixture 7 — config validation
# ---------------------------------------------------------------------------


async def test_fixture_7_rejects_negative_min() -> None:
    with pytest.raises(ValueError):
        PacedAdapter(_InstantAdapter(), min_s=-1.0, max_s=5.0)


async def test_fixture_7b_rejects_max_below_min() -> None:
    with pytest.raises(ValueError):
        PacedAdapter(_InstantAdapter(), min_s=5.0, max_s=1.0)


# ---------------------------------------------------------------------------
# Fixture 8 — _build_adapters_for_hand wraps only non-human seats
# ---------------------------------------------------------------------------


async def test_fixture_8_build_adapters_wraps_only_non_human(tmp_path: Any) -> None:
    """``TableHandle._build_adapters_for_hand`` wraps every adapter whose
    ``kind`` is "bot" or "canned" when pacing is enabled; human adapters
    are passed through unchanged.  Verifies the composition seam — the
    place pacing actually gets applied during a hand."""
    from mahjong.engine.rulesets import MANIFEST
    from mahjong.server.registry import TableHandle
    from mahjong.server.seats import SeatComposition

    handle = TableHandle(
        table_id="99",
        ruleset=cast(Any, {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}),
        seed=1,
        hand_id="t99-h0",
        record_path=tmp_path / "rec.jsonl",
        server_info={"version": "test", "git_sha": "test", "host": "test"},
        seats=(
            SeatComposition("human"),  # unbound → AutoPassAdapter (kind=canned, paced)
            SeatComposition("bot"),  # CannedAdapter (kind=canned, paced)
            SeatComposition("bot"),
            SeatComposition("bot"),
        ),
        bot_pacing_enabled=True,
        bot_min_delay_s=0.0,
        bot_max_delay_s=0.001,
    )

    adapters = handle._build_adapters_for_hand()
    assert len(adapters) == 4
    # All four are non-human in this fixture (human seat is unbound →
    # AutoPassAdapter), so all four should be wrapped.
    for i, a in enumerate(adapters):
        assert isinstance(a, PacedAdapter), f"seat {i} not paced: {type(a).__name__}"


async def test_fixture_8b_disabled_pacing_no_wrap(tmp_path: Any) -> None:
    from mahjong.engine.rulesets import MANIFEST
    from mahjong.server.registry import TableHandle
    from mahjong.server.seats import SeatComposition

    handle = TableHandle(
        table_id="99",
        ruleset=cast(Any, {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}),
        seed=1,
        hand_id="t99-h0",
        record_path=tmp_path / "rec.jsonl",
        server_info={"version": "test", "git_sha": "test", "host": "test"},
        seats=(
            SeatComposition("human"),
            SeatComposition("bot"),
            SeatComposition("bot"),
            SeatComposition("bot"),
        ),
        bot_pacing_enabled=False,  # explicit off
    )

    adapters = handle._build_adapters_for_hand()
    for i, a in enumerate(adapters):
        assert not isinstance(a, PacedAdapter), f"seat {i} unexpectedly paced when disabled"
