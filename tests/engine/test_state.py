"""Tests for `mahjong.engine.state` — `initial_state`, `project`, `is_terminal`, `state_hash`.

Spec: docs/specs/state-schema.md § Engine API surface, § Per-seat projection;
      docs/specs/engine-api.md § Public API.

Step 2.1 of CHECKLIST.md. Tests written before the implementation.
"""

from __future__ import annotations

from typing import Any

import pytest

from mahjong.engine import errors, state
from mahjong.engine.hashing import canonical_hash
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.tiles import canonical_tile_set
from tests.conftest import load_golden

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


# --- initial_state ---


def test_initial_state_hash_matches_golden() -> None:
    """`initial_state(mcr-2006, seed=12345)` is byte-stable across runs.

    Golden state lives in tests/_fixtures/initial_state_seed_12345.json.
    A change to its hash is a determinism contract break — refactor protocol
    (determinism.md) applies.
    """
    s = state.initial_state(MCR_REF, seed=12345)
    golden = load_golden("initial_state_seed_12345.json")
    assert canonical_hash(s) == golden["state_hash"]
    # Strong form: full state must round-trip identically.
    assert s == golden["state"]


def test_initial_state_tile_count_invariant() -> None:
    """Wall + concealed + flowers + meld-tiles == 144 for all seeds."""
    for seed in (1, 12345, 2**127 - 1):
        s = state.initial_state(MCR_REF, seed=seed)
        wall_n = len(s["wall"]["remaining"])
        concealed_n = sum(len(seat["concealed"]) for seat in s["seats"])
        flowers_n = sum(len(seat["flowers"]) for seat in s["seats"])
        meld_n = sum(len(m["tiles"]) for seat in s["seats"] for m in seat["melds"])
        assert wall_n + concealed_n + flowers_n + meld_n == 144
        # Wall bookkeeping fields must agree.
        assert s["wall"]["total"] == 144
        assert s["wall"]["drawn_count"] == 144 - wall_n


def test_initial_state_dealer_has_14_others_13() -> None:
    """Dealer (seat 0 at hand_index 0) starts with the 14th tile already drawn."""
    s = state.initial_state(MCR_REF, seed=12345)
    assert s["dealer_seat"] == 0
    assert len(s["seats"][0]["concealed"]) == 14
    for i in (1, 2, 3):
        assert len(s["seats"][i]["concealed"]) == 13


def test_initial_state_phase_and_actor() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    assert s["phase"] == "DISCARD"
    assert s["current_actor"] == s["dealer_seat"]
    assert s["turn_index"] == 0
    assert s["last_discard"] is None
    assert s["pending_claims"] == []
    assert s["terminal"] is None


def test_initial_state_concealed_is_sorted() -> None:
    """Canonical-form invariant: each seat's `concealed` is sorted in canonical order."""
    s = state.initial_state(MCR_REF, seed=12345)
    # validate_state_invariants is the source of truth for sortedness; if it
    # passes, the invariant holds.
    from mahjong.engine.types import validate_state_invariants

    validate_state_invariants(s)  # raises InvalidState if unsorted


def test_initial_state_flowers_are_not_in_concealed() -> None:
    """Bonus tiles (H*) live in `flowers`, never in `concealed`."""
    s = state.initial_state(MCR_REF, seed=12345)
    for seat in s["seats"]:
        assert all(not t.startswith("H") for t in seat["concealed"]), (
            f"seat {seat['seat']}: H tile leaked into concealed"
        )


def test_initial_state_rng_cursor_is_post_shuffle() -> None:
    """`rng.cursor` reflects bytes consumed by the wall shuffle (only RNG op so far)."""
    s = state.initial_state(MCR_REF, seed=12345)
    assert s["rng"]["seed"] == "12345"
    # Cursor must be > 0 — Fisher-Yates on 143 swaps consumes bytes.
    assert s["rng"]["cursor"] > 0


def test_initial_state_seat_winds_in_order() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    assert [seat["seat_wind"] for seat in s["seats"]] == ["F1", "F2", "F3", "F4"]
    assert s["round_wind"] == "F1"


def test_initial_state_unknown_ruleset_raises() -> None:
    bogus: dict[str, Any] = {"id": "no-such-ruleset", "version": 1, "config_hash": "sha256:00"}
    with pytest.raises(errors.RulesetError):
        state.initial_state(bogus, seed=12345)


def test_initial_state_negative_seed_raises() -> None:
    with pytest.raises(ValueError):
        state.initial_state(MCR_REF, seed=-1)


def test_initial_state_oversize_seed_raises() -> None:
    with pytest.raises(ValueError):
        state.initial_state(MCR_REF, seed=1 << 128)


def test_initial_state_deal_uses_every_tile_at_most_once() -> None:
    """All 144 canonical tiles account for exactly themselves after deal."""
    s = state.initial_state(MCR_REF, seed=12345)
    bag: list[str] = list(s["wall"]["remaining"])
    for seat in s["seats"]:
        bag.extend(seat["concealed"])
        bag.extend(seat["flowers"])
        for m in seat["melds"]:
            bag.extend(m["tiles"])
    assert sorted(bag) == sorted(canonical_tile_set())


def test_initial_state_different_seeds_yield_different_hashes() -> None:
    h1 = canonical_hash(state.initial_state(MCR_REF, seed=1))
    h2 = canonical_hash(state.initial_state(MCR_REF, seed=2))
    assert h1 != h2


# --- project ---


def test_project_hides_foreign_concealed() -> None:
    """For every seat S, project(state, S).seats[i] for i != S has no tile tokens."""
    s = state.initial_state(MCR_REF, seed=12345)
    for seat in range(4):
        view = state.project(s, seat)
        for i, seat_view in enumerate(view["seats"]):
            if i == seat:
                continue
            assert isinstance(seat_view["concealed"], dict), (
                f"project(state, {seat}).seats[{i}].concealed must be a dict (count), got "
                f"{type(seat_view['concealed'])}"
            )
            assert "count" in seat_view["concealed"]
            # Privacy: there must be no tile tokens anywhere in this opponent view.
            _assert_no_tile_strings(seat_view["concealed"])


def test_project_self_view_reversibility() -> None:
    """`project(state, seat).seats[seat] == state.seats[seat]` for every seat."""
    s = state.initial_state(MCR_REF, seed=12345)
    for seat in range(4):
        view = state.project(s, seat)
        assert view["seats"][seat] == s["seats"][seat]


def test_project_wall_is_view_only() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    view = state.project(s, 0)
    assert "remaining" not in view["wall"], "wall.remaining must not leak in projection"
    assert view["wall"]["remaining_count"] == len(s["wall"]["remaining"])
    assert view["wall"]["drawn_count"] == s["wall"]["drawn_count"]
    assert view["wall"]["total"] == 144


def test_project_omits_rng() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    view = state.project(s, 0)
    assert "rng" not in view


def test_project_filters_pending_claims_to_own_seat() -> None:
    """A projection sees only its own seat's claim opportunities."""
    s = state.initial_state(MCR_REF, seed=12345)
    # Inject synthetic claim opportunities; project must filter.
    s = dict(s)  # type: ignore[assignment]
    s["pending_claims"] = [
        {"seat": 0, "claim": "PENG"},
        {"seat": 2, "claim": "CHI", "chi_tiles": ["B4", "B5", "B6"]},
        {"seat": 2, "claim": "HU"},
    ]
    view0 = state.project(s, 0)  # type: ignore[arg-type]
    view2 = state.project(s, 2)  # type: ignore[arg-type]
    view1 = state.project(s, 1)  # type: ignore[arg-type]
    assert [c["seat"] for c in view0["pending_claims"]] == [0]
    assert [c["seat"] for c in view2["pending_claims"]] == [2, 2]
    assert view1["pending_claims"] == []


def test_project_invalid_seat_raises() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    with pytest.raises(ValueError):
        state.project(s, 4)
    with pytest.raises(ValueError):
        state.project(s, -1)


# --- is_terminal ---


def test_is_terminal_false_for_initial_state() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    assert state.is_terminal(s) is False


def test_is_terminal_true_when_phase_terminal() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    mutated = dict(s)
    mutated["phase"] = "TERMINAL"
    assert state.is_terminal(mutated) is True  # type: ignore[arg-type]


# --- state_hash convenience re-export ---


def test_state_hash_matches_canonical_hash() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    assert state.state_hash(s) == canonical_hash(s)


# --- helpers ---


def _assert_no_tile_strings(obj: Any) -> None:
    """Recursively assert no value in `obj` is a string that parses as a tile token."""
    from mahjong.engine.tiles import validate_tile

    if isinstance(obj, str):
        assert not validate_tile(obj), f"tile token leaked into projection: {obj!r}"
    elif isinstance(obj, dict):
        for v in obj.values():
            _assert_no_tile_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _assert_no_tile_strings(v)
