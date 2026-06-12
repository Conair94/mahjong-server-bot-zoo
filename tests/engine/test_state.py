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


def test_initial_state_dealer_seat_parameter() -> None:
    """Layer 8 amendment: dealer_seat kwarg rotates the dealer and seat winds.

    Spec: Step 8 — multi-hand orchestration engine amendment.
    The default (dealer_seat=0) must produce byte-identical output to the
    old single-arg call so the F1 golden fixture doesn't break.
    """
    # Default is unchanged
    s_default = state.initial_state(MCR_REF, seed=12345)
    s_explicit = state.initial_state(MCR_REF, seed=12345, dealer_seat=0)
    assert s_default == s_explicit

    # Each dealer_seat value produces the correct dealer_seat field
    for dealer in range(4):
        s = state.initial_state(MCR_REF, seed=12345, dealer_seat=dealer)
        assert s["dealer_seat"] == dealer, f"dealer_seat={dealer}: state has {s['dealer_seat']}"
        # current_actor and last_drawn seat must equal the dealer
        assert s["current_actor"] == dealer
        assert s["last_drawn"]["seat"] == dealer
        # Dealer has 14 concealed tiles; others have 13
        for i in range(4):
            expected = 14 if i == dealer else 13
            assert len(s["seats"][i]["concealed"]) == expected, (
                f"dealer={dealer}, seat {i}: expected {expected}, got {len(s['seats'][i]['concealed'])}"
            )
        # East wind (F1) belongs to the dealer seat
        dealer_wind = s["seats"][dealer]["seat_wind"]
        assert dealer_wind == "F1", f"dealer={dealer}: seat wind is {dealer_wind}, want F1"


def test_initial_state_hand_index_parameter() -> None:
    """hand_index kwarg is stored in the state (metadata for multi-hand)."""
    s = state.initial_state(MCR_REF, seed=12345, hand_index=3)
    assert s["hand_index"] == 3
    # Default is 0 (unchanged)
    assert state.initial_state(MCR_REF, seed=12345)["hand_index"] == 0


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


def test_project_includes_own_last_drawn() -> None:
    """FB-17: the per-seat projection carries `last_drawn` so a reconnect
    snapshot is self-sufficient (the client's just-drawn offset and the
    Enter-to-tsumogiri shortcut survive a refresh). Own seat sees the tile."""
    s = state.initial_state(MCR_REF, seed=12345)
    view = state.project(s, 0)  # dealer holds the just-drawn 14th tile
    assert view["last_drawn"] == s["last_drawn"]
    assert view["last_drawn"] is not s["last_drawn"]  # copy, not alias


def test_project_redacts_last_drawn_tile_for_other_seats() -> None:
    """Another seat learns *who* drew (public knowledge, same as the DRAW
    event projection) but never *what* they drew."""
    s = state.initial_state(MCR_REF, seed=12345)
    view = state.project(s, 1)
    assert view["last_drawn"] == {"seat": 0, "tile": None}


def test_project_last_drawn_none_passes_through() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    s = dict(s)  # type: ignore[assignment]
    s["last_drawn"] = None
    view = state.project(s, 0)  # type: ignore[arg-type]
    assert view["last_drawn"] is None


def _terminal_stub() -> dict[str, Any]:
    return {
        "kind": "EXHAUSTIVE_DRAW",
        "winner": None,
        "win_tile": None,
        "win_type": None,
        "deal_in_seat": None,
        "fan": [],
        "fan_total": 0,
        "score_delta": [0, 0, 0, 0],
    }


def test_project_terminal_includes_final_hands_reveal() -> None:
    """FB-17: at TERMINAL the projection's `terminal` carries the same
    `final_hands` settlement reveal the HAND_END record event does, so a
    post-hand reconnect snapshot can render the summary without the frame."""
    s = state.initial_state(MCR_REF, seed=12345)
    s = dict(s)  # type: ignore[assignment]
    s["phase"] = "TERMINAL"
    s["terminal"] = _terminal_stub()
    view = state.project(s, 1)  # type: ignore[arg-type]
    hands = view["terminal"]["final_hands"]
    assert [h["seat"] for h in hands] == [0, 1, 2, 3]
    # Full reveal — including seats other than the viewer's.
    assert hands[0]["concealed"] == s["seats"][0]["concealed"]
    assert hands[0]["melds"] == s["seats"][0]["melds"]
    assert hands[0]["flowers"] == s["seats"][0]["flowers"]


def test_project_non_terminal_has_no_final_hands() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    view = state.project(s, 0)
    assert view["terminal"] is None


def test_initial_state_last_drawn_is_dealer_fourteenth_tile() -> None:
    """`initial_state` sets last_drawn to seat 0's 14th drawn (post-flower)
    tile — the dealer's just-drawn tile, ready to be discarded on turn 0."""
    s = state.initial_state(MCR_REF, seed=12345)
    last_drawn = s["last_drawn"]
    assert last_drawn is not None
    assert last_drawn["seat"] == 0
    assert last_drawn["tile"] in s["seats"][0]["concealed"]


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


# --- project(state, seat=None): public/spectator view (Step 7.0) ---


def test_project_public_view_hides_every_concealed() -> None:
    """`project(state, seat=None)` shows count form for *every* seat — no own-seat exception."""
    s = state.initial_state(MCR_REF, seed=12345)
    view = state.project(s, None)
    for i, seat_view in enumerate(view["seats"]):
        assert isinstance(seat_view["concealed"], dict), (
            f"project(state, None).seats[{i}].concealed must be a dict (count), "
            f"got {type(seat_view['concealed'])}"
        )
        assert "count" in seat_view["concealed"]
        assert seat_view["concealed"]["count"] == len(s["seats"][i]["concealed"])


def test_project_public_view_no_tile_tokens_in_concealed() -> None:
    """No tile token appears anywhere under any seat's `concealed` in the public view."""
    s = state.initial_state(MCR_REF, seed=12345)
    view = state.project(s, None)
    for seat_view in view["seats"]:
        _assert_no_tile_strings(seat_view["concealed"])


def test_project_public_view_pending_claims_empty() -> None:
    """A public observer has no claim opportunities of their own."""
    s = state.initial_state(MCR_REF, seed=12345)
    s = dict(s)  # type: ignore[assignment]
    s["pending_claims"] = [
        {"seat": 0, "claim": "PENG"},
        {"seat": 2, "claim": "HU"},
    ]
    view = state.project(s, None)  # type: ignore[arg-type]
    assert view["pending_claims"] == []


def test_project_public_view_wall_is_count_only() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    view = state.project(s, None)
    assert "remaining" not in view["wall"]
    assert view["wall"]["remaining_count"] == len(s["wall"]["remaining"])
    assert view["wall"]["drawn_count"] == s["wall"]["drawn_count"]


def test_project_public_view_omits_rng_and_last_drawn() -> None:
    s = state.initial_state(MCR_REF, seed=12345)
    view = state.project(s, None)
    assert "rng" not in view
    assert "last_drawn" not in view


def test_project_public_view_byte_stable_across_calls() -> None:
    """`project(state, None)` is byte-stable across two calls (pure function)."""
    s = state.initial_state(MCR_REF, seed=12345)
    v1 = state.project(s, None)
    v2 = state.project(s, None)
    assert canonical_hash(v1) == canonical_hash(v2)  # type: ignore[arg-type]
    assert v1 == v2


def test_project_public_view_agrees_with_opponent_view_per_seat() -> None:
    """For every seat S, the public view's seats[S] equals the opponent-form
    view of seat S as seen from any other seat S' != S."""
    s = state.initial_state(MCR_REF, seed=12345)
    public = state.project(s, None)
    for s_target in range(4):
        for viewer in range(4):
            if viewer == s_target:
                continue
            per_seat = state.project(s, viewer)
            assert public["seats"][s_target] == per_seat["seats"][s_target], (
                f"public seat[{s_target}] disagrees with viewer-{viewer}'s opponent view"
            )


def test_project_public_view_terminal_fully_visible() -> None:
    """Terminal fields (winner, fan, score_delta) are public — full reveal at hand-end."""
    s = state.initial_state(MCR_REF, seed=12345)
    s = dict(s)  # type: ignore[assignment]
    s["terminal"] = {
        "kind": "HU",
        "winner": 2,
        "win_tile": "B5",
        "win_type": "self_draw",
        "deal_in_seat": None,
        "fan": [{"name": "All Pungs", "value": 6}],
        "fan_total": 6,
        "score_delta": [-8, -8, 24, -8],
    }
    view = state.project(s, None)  # type: ignore[arg-type]
    # Terminal passes through in full, plus the FB-17 `final_hands` reveal.
    assert view["terminal"] == {**s["terminal"], "final_hands": state.final_hands_view(s)}


# --- project_event(event, seat=...) (Step 7.0) ---


def test_project_event_draw_strips_tile_for_public() -> None:
    """`project_event(DRAW, seat=None)` removes the `tile` field."""
    draw = {
        "event": "DRAW",
        "turn_index": 5,
        "phase": "DISCARD",
        "ts": "2026-05-22T10:00:00Z",
        "seat": 2,
        "tile": "B5",
        "flower_replacements": [],
    }
    projected = state.project_event(draw, None)
    assert "tile" not in projected
    # other fields preserved
    assert projected["event"] == "DRAW"
    assert projected["seat"] == 2
    assert projected["turn_index"] == 5
    # input not mutated
    assert draw["tile"] == "B5"


def test_project_event_draw_keeps_tile_for_own_seat() -> None:
    """The drawing seat sees its own drawn tile."""
    draw = {
        "event": "DRAW",
        "turn_index": 5,
        "phase": "DISCARD",
        "ts": "ts",
        "seat": 2,
        "tile": "B5",
        "flower_replacements": [],
    }
    projected = state.project_event(draw, 2)
    assert projected["tile"] == "B5"


def test_project_event_draw_strips_tile_for_other_seats() -> None:
    """A non-drawing seat does not see the drawn tile."""
    draw = {
        "event": "DRAW",
        "turn_index": 5,
        "phase": "DISCARD",
        "ts": "ts",
        "seat": 2,
        "tile": "B5",
        "flower_replacements": [],
    }
    for viewer in (0, 1, 3):
        projected = state.project_event(draw, viewer)
        assert "tile" not in projected, f"viewer {viewer} should not see seat 2's draw tile"


def test_project_event_concealed_gang_hides_tile_from_others() -> None:
    """Spec 29 Bug D: a CONCEALED kong's tile is private to its owner."""
    decision = {
        "event": "CLAIM_DECISION",
        "turn_index": 7,
        "phase": "DISCARD",
        "ts": "ts",
        "seat": 3,
        "decision": "GANG",
        "kind": "CONCEALED",
        "tile": "W4",
    }
    # Owner (and only the owner) still sees the tile.
    assert state.project_event(decision, 3)["tile"] == "W4"
    # Everyone else — and the public/spectator view — does not.
    for viewer in (None, 0, 1, 2):
        assert "tile" not in state.project_event(decision, viewer), viewer


def test_project_event_exposed_and_added_gang_tiles_public() -> None:
    """Exposed and added kongs sit on public information, so their tile carries
    through to every viewer (only CONCEALED is redacted)."""
    for kind in ("EXPOSED", "ADDED"):
        decision = {
            "event": "CLAIM_DECISION",
            "turn_index": 7,
            "phase": "CLAIM_WINDOW",
            "ts": "ts",
            "seat": 3,
            "decision": "GANG",
            "kind": kind,
            "tile": "W4",
        }
        for viewer in (None, 0, 1, 2, 3):
            assert state.project_event(decision, viewer)["tile"] == "W4", (kind, viewer)


def test_project_opponent_concealed_kong_meld_is_masked() -> None:
    """An opponent's GANG_CONCEALED meld shows no tile identity in the snapshot;
    the owner's own view keeps it."""
    s = state.initial_state(MCR_REF, seed=12345)
    # Give seat 1 a concealed kong meld.
    s["seats"][1]["melds"] = [
        {"type": "GANG_CONCEALED", "tiles": ["W4", "W4", "W4", "W4"], "called_from_seat": 1}
    ]

    own = state.project(s, 1)
    own_meld = own["seats"][1]["melds"][0]
    assert own_meld["tiles"] == ["W4", "W4", "W4", "W4"]
    assert not own_meld.get("hidden")

    for viewer in (0, 2, 3):
        opp = state.project(s, viewer)
        meld = opp["seats"][1]["melds"][0]
        assert meld["type"] == "GANG_CONCEALED"
        assert meld.get("hidden") is True
        assert "tiles" not in meld, f"viewer {viewer} must not see the kong tiles"


def test_project_event_discard_preserved_in_full() -> None:
    """DISCARD is publicly visible — every field carries through."""
    discard = {
        "event": "DISCARD",
        "turn_index": 6,
        "phase": "CLAIM_WINDOW",
        "ts": "ts",
        "seat": 2,
        "tile": "B5",
        "from_hand": True,
    }
    for viewer in (None, 0, 1, 2, 3):
        assert state.project_event(discard, viewer) == discard


def test_project_event_hand_end_preserved_in_full() -> None:
    """HAND_END is the terminal reveal — public, including final_hands."""
    he = {
        "event": "HAND_END",
        "turn_index": 30,
        "phase": "TERMINAL",
        "ts": "ts",
        "kind": "HU",
        "winner": [2],
        "win_tile": "B5",
        "win_type": "self_draw",
        "deal_in_seat": None,
        "fan": [{"name": "All Pungs", "value": 6}],
        "fan_total": 6,
        "score_delta": [-8, -8, 24, -8],
        "final_hands": [
            {"seat": 0, "concealed": ["B1"], "melds": [], "flowers": []},
        ],
        "state_hash": "sha256:abc",
    }
    for viewer in (None, 0, 1, 2, 3):
        assert state.project_event(he, viewer) == he


def test_project_event_is_idempotent() -> None:
    """Applying `project_event` twice equals applying once."""
    draw = {
        "event": "DRAW",
        "turn_index": 5,
        "phase": "DISCARD",
        "ts": "ts",
        "seat": 2,
        "tile": "B5",
        "flower_replacements": [],
    }
    for viewer in (None, 0, 1, 2, 3):
        once = state.project_event(draw, viewer)
        twice = state.project_event(once, viewer)
        assert twice == once


def test_project_event_does_not_mutate_input() -> None:
    draw = {
        "event": "DRAW",
        "turn_index": 5,
        "phase": "DISCARD",
        "ts": "ts",
        "seat": 2,
        "tile": "B5",
        "flower_replacements": [],
    }
    snapshot = dict(draw)
    state.project_event(draw, None)
    state.project_event(draw, 2)
    assert draw == snapshot


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
