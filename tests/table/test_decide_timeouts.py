"""Per-(seat-kind, prompt-kind) decide-timeout policy.

Spec: docs/specs/human-decide-timeout.md (Step 8.10).  Six fixtures:

1. Human DISCARD timeout uses ``human_discard_s``.
2. Human CLAIM timeout uses ``human_claim_s``.
3. Bot seats keep ``bot_s`` regardless of prompt kind.
4. Default behaviour matches the new env-var defaults; the legacy
   ``decide_timeout_seconds`` single-value path still works.
5. Adapter ``kind`` pins per spec § Adapter-kind lookup.
6. Strike system unchanged — three timeouts still swap to AutoPassAdapter.

Design note: rather than rely on wall-clock timing (flaky on a loaded
runner), the timing fixtures capture the prompt's ``deadline - issued_at``
window on each ``decide`` call.  That value is the deadline the manager
plumbed in via ``DecideTimeouts.for_(kind, prompt_kind)``, so checking it
pins the lookup table directly.  The adapter then returns its default
action so the hand makes progress.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mahjong.adapters.autopass import AutoPassAdapter
from mahjong.adapters.canned import CannedAdapter
from mahjong.adapters.human import HumanAdapter
from mahjong.engine.rulesets import MANIFEST
from mahjong.records.reader import read_record
from mahjong.server.config import load_config_from_env
from mahjong.table.manager import DecideTimeouts, run_hand

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}

SERVER = {"version": "test", "git_sha": "test", "host": "test"}


# --- Test doubles -----------------------------------------------------------


class _BudgetRecordingAdapter(CannedAdapter):
    """Records the (prompt_kind, budget) seen by each ``decide`` call.

    Returns the prompt's default action immediately so the hand proceeds.
    ``kind`` is overridable so the same double can stand in for human or
    canned seats.
    """

    def __init__(self, *, kind: str, identity_script: str = "budget_record") -> None:
        super().__init__(
            identity={"kind": "canned", "script": identity_script},
            actions=[],
        )
        self.kind = kind  # type: ignore[assignment]
        self.seen: list[tuple[str, float]] = []

    async def decide(self, prompt: dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        budget = round(prompt["deadline"] - prompt["issued_at"], 3)
        self.seen.append((prompt["kind"], budget))
        return prompt["default_action"]


def _four_passers() -> list[CannedAdapter]:
    return [
        CannedAdapter(identity={"kind": "canned", "script": "pass"}, actions=[]) for _ in range(4)
    ]


async def _run_one_hand(
    adapters: list[Any],
    *,
    tmp_path: Path,
    decide_timeouts: DecideTimeouts,
    strike_limit: int = 99,
) -> Path:
    record_path = tmp_path / "hand.jsonl"
    await run_hand(
        adapters=adapters,
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=record_path,
        server_info=SERVER,
        decide_timeouts=decide_timeouts,
        strike_limit=strike_limit,
    )
    return record_path


# --- Fixture 1: human DISCARD timeout uses human_discard_s ------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_human_discard_timeout_uses_human_discard_budget(tmp_path: Path) -> None:
    adapters: list[Any] = _four_passers()
    human = _BudgetRecordingAdapter(kind="human", identity_script="human_seat")
    adapters[0] = human

    await _run_one_hand(
        adapters,
        tmp_path=tmp_path,
        decide_timeouts=DecideTimeouts(human_discard_s=45.0, human_claim_s=20.0, bot_s=30.0),
    )

    discard_budgets = [b for (kind, b) in human.seen if kind == "DISCARD"]
    assert discard_budgets, "expected at least one DISCARD prompt to seat 0"
    assert all(
        b == pytest.approx(45.0, abs=0.5) for b in discard_budgets
    ), f"human DISCARD budgets should all be ~45s, got {discard_budgets}"


# --- Fixture 2: human CLAIM timeout uses human_claim_s ----------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_human_claim_timeout_uses_human_claim_budget(tmp_path: Path) -> None:
    """When the human adapter is prompted inside a CLAIM_WINDOW, the
    deadline plumbed into the prompt should be ``human_claim_s`` — distinct
    from the DISCARD budget."""
    adapters: list[Any] = _four_passers()
    human = _BudgetRecordingAdapter(kind="human", identity_script="human_seat")
    adapters[1] = human

    await _run_one_hand(
        adapters,
        tmp_path=tmp_path,
        decide_timeouts=DecideTimeouts(human_discard_s=45.0, human_claim_s=15.0, bot_s=30.0),
    )

    by_kind: dict[str, list[float]] = {"DISCARD": [], "CLAIM": []}
    for kind, b in human.seen:
        by_kind[kind].append(b)

    assert by_kind["CLAIM"], "expected at least one CLAIM prompt to the human seat"
    assert all(
        b == pytest.approx(15.0, abs=0.5) for b in by_kind["CLAIM"]
    ), f"human CLAIM budgets should all be ~15s, got {by_kind['CLAIM']}"
    # And DISCARD prompts (if any) used the human_discard_s side.
    for b in by_kind["DISCARD"]:
        assert b == pytest.approx(45.0, abs=0.5)


# --- Fixture 3: bot/canned seats use bot_s regardless of prompt kind --------


@pytest.mark.asyncio(loop_scope="function")
async def test_bot_seat_uses_bot_s_regardless_of_prompt_kind(tmp_path: Path) -> None:
    """A ``kind != "human"`` seat (canned or bot) should see ``bot_s`` for
    every prompt kind, even when the human budgets are very different."""
    adapters: list[Any] = _four_passers()
    canned = _BudgetRecordingAdapter(kind="canned", identity_script="canned_seat")
    adapters[2] = canned

    await _run_one_hand(
        adapters,
        tmp_path=tmp_path,
        decide_timeouts=DecideTimeouts(human_discard_s=90.0, human_claim_s=15.0, bot_s=7.0),
    )

    assert canned.seen, "expected at least one prompt to seat 2"
    assert all(
        b == pytest.approx(7.0, abs=0.5) for (_, b) in canned.seen
    ), f"all canned-seat budgets should be ~7s (bot_s), got {canned.seen}"


# --- Fixture 4: env-var defaults + back-compat shim -------------------------


def test_default_decide_timeouts_match_spec() -> None:
    cfg, unknown = load_config_from_env({})
    assert unknown == []
    assert cfg.decide_timeout_human_discard_s == 60
    assert cfg.decide_timeout_human_claim_s == 20
    assert cfg.decide_timeout_bot_s == 30


def test_env_vars_override_defaults() -> None:
    cfg, unknown = load_config_from_env(
        {
            "MAHJONG_DECIDE_TIMEOUT_HUMAN_DISCARD_S": "90",
            "MAHJONG_DECIDE_TIMEOUT_HUMAN_CLAIM_S": "15",
            "MAHJONG_DECIDE_TIMEOUT_BOT_S": "5",
        }
    )
    assert unknown == []
    assert cfg.decide_timeout_human_discard_s == 90
    assert cfg.decide_timeout_human_claim_s == 15
    assert cfg.decide_timeout_bot_s == 5


def test_decide_timeouts_uniform_is_back_compat_shim() -> None:
    """Single-value back-compat: ``DecideTimeouts.uniform(N)`` returns N
    from ``for_`` regardless of (kind, prompt_kind)."""
    uniform = DecideTimeouts.uniform(5.0)
    assert uniform.for_("human", "DISCARD") == 5.0
    assert uniform.for_("human", "CLAIM") == 5.0
    assert uniform.for_("canned", "DISCARD") == 5.0
    assert uniform.for_("bot", "CLAIM") == 5.0


@pytest.mark.asyncio(loop_scope="function")
async def test_legacy_decide_timeout_seconds_still_works(tmp_path: Path) -> None:
    """Callers passing the legacy ``decide_timeout_seconds`` (single value)
    with no ``decide_timeouts`` should observe that single value on every
    prompt — proving the uniform shim is wired through ``run_hand``."""
    adapters: list[Any] = _four_passers()
    recorder = _BudgetRecordingAdapter(kind="canned", identity_script="legacy")
    adapters[0] = recorder

    await run_hand(
        adapters=adapters,
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info=SERVER,
        decide_timeout_seconds=12.5,
    )
    assert recorder.seen
    assert all(b == pytest.approx(12.5, abs=0.5) for (_, b) in recorder.seen)


# --- Fixture 5: adapter kind values -----------------------------------------


def test_adapter_kind_values_are_pinned() -> None:
    """Pin the per-adapter ``kind`` literal so the manager's lookup is
    unambiguous (spec § Adapter-kind lookup)."""
    canned = CannedAdapter(identity={"kind": "canned", "script": "x"}, actions=[])
    autopass = AutoPassAdapter()
    assert canned.kind == "canned"
    assert autopass.kind == "canned"
    # HumanAdapter needs a real SeatSession to instantiate; check the
    # class-level attribute, which is what the manager reads anyway.
    assert HumanAdapter.kind == "human"


# --- Fixture 6: strike system unchanged -------------------------------------


class _AlwaysTimeoutHuman(CannedAdapter):
    """Human-tagged adapter whose decide never returns within any deadline."""

    kind = "human"  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__(identity={"kind": "canned", "script": "stuck_human"}, actions=[])

    async def decide(self, prompt: dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        import asyncio as _asyncio

        await _asyncio.sleep(60.0)
        return prompt["default_action"]


@pytest.mark.asyncio(loop_scope="function")
async def test_strike_limit_still_swaps_to_autopass_under_per_kind_budgets(
    tmp_path: Path,
) -> None:
    """Three consecutive timeouts on a human seat still swap in AutoPassAdapter."""
    adapters: list[Any] = _four_passers()
    adapters[0] = _AlwaysTimeoutHuman()

    decide_timeouts = DecideTimeouts(human_discard_s=0.05, human_claim_s=0.05, bot_s=60.0)
    record_path = await _run_one_hand(
        adapters, tmp_path=tmp_path, decide_timeouts=decide_timeouts, strike_limit=3
    )
    events = read_record(record_path)
    seat_0_discards = [e for e in events if e["event"] == "DISCARD" and e["seat"] == 0]
    assert sum(1 for e in seat_0_discards if e.get("timeout") is True) >= 3
    assert any(e.get("auto_pass") is True for e in seat_0_discards[3:])
