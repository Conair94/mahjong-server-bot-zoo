"""Hand-stats analysis (Spec 37): shanten, waits, fan potential, remaining counts.

Spec: docs/specs/hand-stats.md § Payload schema, § Verification fixtures.

Fan literals are calculator-anchored: every expected fan total below was
probed against PyMahjongGB through `pymj.calculate_fan` at fixture-authoring
time and pinned (the FB-09/FB-15 lesson — never hand-derive MCR arithmetic).
"""

from __future__ import annotations

import json
from typing import Any

from mahjong.analysis import prompt_stats, remaining_counts, stats_for_prompt
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.tiles import tile_sort_key

TS_REF: dict[str, Any] = {
    "id": "mcr-house-3fan",
    "version": 1,
    "config_hash": MANIFEST["mcr-house-3fan"],
}


def _opponent(
    seat: int, *, melds: list[dict[str, Any]] | None = None, discards: list[str] | None = None
) -> dict[str, Any]:
    return {
        "seat": seat,
        "seat_wind": f"F{seat + 1}",
        "concealed": {"count": 13},
        "melds": melds or [],
        "discards": discards or [],
        "flowers": [],
        "score": 0,
    }


def _view(
    own_concealed: list[str],
    *,
    own_melds: list[dict[str, Any]] | None = None,
    opponents: dict[int, dict[str, Any]] | None = None,
    last_discard: dict[str, Any] | None = None,
    phase: str = "DISCARD",
    wall_remaining: int = 42,
) -> dict[str, Any]:
    """A seat-0 SeatView shaped per state-schema.md § Per-seat projection."""
    seats: list[dict[str, Any]] = [
        {
            "seat": 0,
            "seat_wind": "F1",
            "concealed": sorted(own_concealed, key=tile_sort_key),
            "melds": own_melds or [],
            "discards": [],
            "flowers": [],
            "score": 0,
        }
    ]
    for i in (1, 2, 3):
        seats.append((opponents or {}).get(i) or _opponent(i))
    return {
        "ruleset": dict(TS_REF),
        "round_wind": "F1",
        "dealer_seat": 0,
        "hand_index": 0,
        "turn_index": 7,
        "wall": {
            "remaining_count": wall_remaining,
            "drawn_count": 144 - 53 - wall_remaining,
            "total": 144,
        },
        "seats": seats,
        "last_discard": last_discard,
        "pending_claims": [],
        "phase": phase,
        "current_actor": 0,
        "terminal": None,
    }


# Fixture A (probed): tenpai, waits B6 (4f discard / 6f self-draw) and
# B9 (6f / 8f) — Concealed Hand / Fully Concealed + Pung of Terminals etc.
TENPAI_A = ["W1", "W1", "W1", "W7", "W8", "W9", "B1", "B2", "B3", "T5", "T5", "B7", "B8"]

PASS_ONLY: list[dict[str, Any]] = [{"type": "PASS"}]


def test_tenpai_hand_block_waits_fan_and_remaining() -> None:
    """Fixture 1: tenpai waits carry per-wait raw fan (discard < self-draw)
    and remaining counts that subtract the visible zones."""
    view = _view(
        TENPAI_A,
        opponents={
            1: _opponent(1, discards=["J3"]),
            2: _opponent(2, discards=["B9"]),  # one wait copy visible in a pond
        },
        last_discard={"seat": 1, "tile": "J3"},
        phase="CLAIM_WINDOW",
    )
    stats = prompt_stats(view, 0, PASS_ONLY, "CLAIM")

    assert stats["floor"] == 3
    assert stats["wall_remaining"] == 42
    hand = stats["hand"]
    assert hand["shanten"] == 0
    tiles = hand["tiles"]
    assert [t["tile"] for t in tiles] == ["B6", "B9"]  # tile_sort_key order
    b6, b9 = tiles
    assert b6 == {"tile": "B6", "remaining": 4, "fan_discard": 4, "fan_self_draw": 6}
    # one B9 sits in seat 2's pond -> 3 unseen
    assert b9 == {"tile": "B9", "remaining": 3, "fan_discard": 6, "fan_self_draw": 8}


def test_subfloor_wait_fan_shown_raw_not_hidden() -> None:
    """Fixture 2 (FB-15 shape): an exposed hand whose W5 wait scores 1 fan —
    below the 3-fan floor — must still report the raw value so the client can
    dim it. Probed: W5 = 1f discard / 2f self; W8 = 8f discard (PyMahjongGB
    awards 'Chicken Hand 8' to an otherwise fanless discard win — pinned here
    as the calculator's actual behaviour; rules-fidelity question tracked in
    the ledger) / 1f self-draw."""
    melds = [
        {"type": "CHI", "tiles": ["W2", "W3", "W4"], "called_tile": "W3", "called_from_seat": 1},
        {"type": "CHI", "tiles": ["B4", "B5", "B6"], "called_tile": "B5", "called_from_seat": 1},
    ]
    view = _view(
        ["T7", "T8", "T9", "W6", "W7", "J1", "J1"],
        own_melds=melds,
        last_discard={"seat": 1, "tile": "J3"},
        phase="CLAIM_WINDOW",
    )
    stats = prompt_stats(view, 0, PASS_ONLY, "CLAIM")

    tiles = stats["hand"]["tiles"]
    assert [t["tile"] for t in tiles] == ["W5", "W8"]
    w5, w8 = tiles
    assert w5["fan_discard"] == 1  # raw, below floor, not hidden, not zeroed
    assert w5["fan_self_draw"] == 2
    assert w8["fan_discard"] == 8  # Chicken Hand
    assert w8["fan_self_draw"] == 1


def test_remaining_counts_subtract_each_visible_zone() -> None:
    """Fixture 3: own concealed + exposed melds + every pond subtract;
    an opponent's hidden concealed kong subtracts nothing (upper bound)."""
    view = _view(
        ["B6", "W2", "W3"],  # own concealed: one B6 visible
        opponents={
            1: _opponent(
                1,
                melds=[
                    {
                        "type": "PENG",
                        "tiles": ["B6", "B6", "B6"],
                        "called_tile": "B6",
                        "called_from_seat": 2,
                    }
                ],
                discards=["T1"],
            ),
            2: _opponent(
                2,
                # Hidden concealed kong: tiles dropped by the projection.
                melds=[{"type": "GANG_CONCEALED", "called_from_seat": 2, "hidden": True}],
                discards=["T1", "W2"],
            ),
        },
    )
    counts = remaining_counts(view)
    assert counts["B6"] == 0  # 1 in hand + 3 in seat 1's pung
    assert counts["T1"] == 2  # one in each of two ponds
    assert counts["W2"] == 2  # one own concealed + one in seat 2's pond
    assert counts["W9"] == 4  # the hidden kong subtracts nothing from anything


def test_one_shanten_effective_tiles_no_fan_fields() -> None:
    """Fixture 4 (probed): 1-shanten hand accepts exactly W1/W4/B2/J3."""
    view = _view(
        ["W1", "W1", "W1", "T5", "T6", "T7", "W2", "W3", "B2", "B2", "J3", "J3", "T1"],
        last_discard={"seat": 1, "tile": "J3"},
        phase="CLAIM_WINDOW",
    )
    stats = prompt_stats(view, 0, PASS_ONLY, "CLAIM")

    hand = stats["hand"]
    assert hand["shanten"] == 1
    tiles = hand["tiles"]
    assert [t["tile"] for t in tiles] == ["W1", "W4", "B2", "J3"]
    by_tile = {t["tile"]: t for t in tiles}
    assert by_tile["W1"]["remaining"] == 1  # 3 in own hand
    assert by_tile["W4"]["remaining"] == 4
    assert by_tile["B2"]["remaining"] == 2  # 2 in own hand
    assert by_tile["J3"]["remaining"] == 2  # 2 in own hand
    for t in tiles:
        assert "fan_discard" not in t and "fan_self_draw" not in t


def test_discard_prompt_candidate_table() -> None:
    """Fixture 5 (probed): in TENPAI_A + J3, only the J3 discard keeps
    tenpai; every other candidate is 1-shanten. Rows sorted by
    (shanten, -total remaining, tile_sort_key); no `hand` block."""
    hand14 = sorted([*TENPAI_A, "J3"], key=tile_sort_key)
    legal = [{"type": "PLAY", "tile": t} for t in sorted(set(hand14), key=tile_sort_key)]
    view = _view(hand14)
    stats = prompt_stats(view, 0, legal, "DISCARD")

    assert "hand" not in stats
    assert "claims" not in stats
    rows = stats["discards"]
    assert len(rows) == len(legal)
    assert rows[0]["tile"] == "J3"
    assert rows[0]["shanten"] == 0
    assert [t["tile"] for t in rows[0]["tiles"]] == ["B6", "B9"]
    assert all(r["shanten"] == 1 for r in rows[1:])
    # Sorted-ness pin: recompute the documented key from the payload itself.
    keys = [
        (r["shanten"], -sum(t["remaining"] for t in r["tiles"]), tile_sort_key(r["tile"]))
        for r in rows
    ]
    assert keys == sorted(keys)


def test_claim_prompt_options_and_dead_wait() -> None:
    """Fixture 6 (probed): base 1-shanten; PENG reaches tenpai after the
    forced discard (shanten_after 0); exposed GANG's post-meld hand stays
    1-shanten. The hand's own B6 acceptance is a dead tile (remaining 0:
    3 in hand + the claimable discard in the pond)."""
    own = ["B6", "B6", "B6", "W1", "W1", "W1", "W7", "W8", "W9", "T5", "T5", "B8", "J1"]
    view = _view(
        own,
        opponents={3: _opponent(3, discards=["B6"])},
        last_discard={"seat": 3, "tile": "B6"},
        phase="CLAIM_WINDOW",
    )
    legal: list[dict[str, Any]] = [
        {"type": "PASS"},
        {"type": "PENG", "tile": "B6"},
        {"type": "GANG", "tile": "B6", "kind": "EXPOSED"},
    ]
    stats = prompt_stats(view, 0, legal, "CLAIM")

    assert stats["hand"]["shanten"] == 1
    by_tile = {t["tile"]: t for t in stats["hand"]["tiles"]}
    assert by_tile["B6"]["remaining"] == 0  # dead acceptance, still listed

    claims = stats["claims"]
    assert len(claims) == 2  # PASS carries no row
    by_type = {c["action"]["type"]: c for c in claims}
    assert by_type["PENG"]["shanten_after"] == 0
    assert by_type["GANG"]["shanten_after"] == 1


def test_payload_is_deterministic_and_json_safe() -> None:
    """Fixture 7: same view in -> byte-identical JSON out."""
    hand14 = sorted([*TENPAI_A, "J3"], key=tile_sort_key)
    legal = [{"type": "PLAY", "tile": t} for t in sorted(set(hand14), key=tile_sort_key)]
    a = prompt_stats(_view(hand14), 0, legal, "DISCARD")
    b = prompt_stats(_view(hand14), 0, legal, "DISCARD")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --- Fixture 11: the live binding is gated to DISCARD prompts ---------------


def _prompt_payload(view: dict[str, Any], legal: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    """The seat-port Prompt shape `stats_for_prompt` consumes."""
    return {"view": view, "legal_actions": legal, "kind": kind}


def test_stats_for_prompt_returns_payload_on_discard() -> None:
    hand14 = sorted([*TENPAI_A, "J3"], key=tile_sort_key)
    legal = [{"type": "PLAY", "tile": t} for t in sorted(set(hand14), key=tile_sort_key)]
    out = stats_for_prompt(_prompt_payload(_view(hand14), legal, "DISCARD"), 0)
    assert out is not None
    assert "discards" in out


def test_stats_for_prompt_is_none_off_discard() -> None:
    """A 13-tile CLAIM hand has no single discard to rank — no stats surface
    (Spec 37 revision: discard-only)."""
    view = _view(TENPAI_A, last_discard={"seat": 1, "tile": "J3"}, phase="CLAIM_WINDOW")
    legal = [{"type": "PASS"}, {"type": "PENG", "tile": "B6"}]
    assert stats_for_prompt(_prompt_payload(view, legal, "CLAIM"), 0) is None
