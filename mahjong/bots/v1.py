"""v1 rule-based bot — v0's offense skeleton + hard accounting + defense.

Spec: docs/specs/v1-rule-bot.md.

Same pure-function contract as v0 (no I/O, no RNG, deterministic). What v1
adds over v0, in decision-relevance order:

1. **Hard accounting** (belief Stage A): ukeire weighted by *live copies*
   instead of tile types; a tenpai whose every ron-feasible wait is exhausted
   is treated as dead and reshaped.
2. **Defense**: per-opponent threat (melds, lateness, flush commitment), a
   per-discard deal-in danger score, and a push/fold regime — fold to the
   safest tile when the hand is hopeless against a visible threat, otherwise
   use danger only to separate offensively tied candidates.
3. **Payout-weighted waits**: at tenpai, candidates are ranked by
   sum(live_copies * house payout tier), not raw wait width (the convex house
   table makes tier differences worth real points).

v1 deliberately reuses v0's internals (the memoized shanten, the claim
application, the fan-aware distance) — two bots is below the bar for
extracting a shared module; the third bot pays for that refactor.

All constants are hand-tuned first guesses; tests pin *orderings*, not
literals (same pattern as v0's SUBFLOOR_TENPAI_DISTANCE).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from mahjong.bots.belief import remaining_counts
from mahjong.bots.v0 import (
    _ALL_TILE_TYPES,
    SUBFLOOR_TENPAI_DISTANCE,
    PromptKind,
    _apply_claim,
    _meld_key,
    _shanten,
    _without,
)
from mahjong.engine import pymj
from mahjong.engine.rulesets import resolve_config
from mahjong.engine.scoring import lookup_x
from mahjong.engine.tiles import Tile, tile_sort_key
from mahjong.engine.types import Action, Meld, SeatView, SeatViewOpponent

# --- Hand-tuned constants (orderings pinned by tests, literals tunable) ------

# Fold when the strongest visible threat reaches this level (3 melds anytime,
# or 2 melds in the endgame — a 400-hand eval showed folding on any 2-meld
# opponent guts offense in a claim-happy field for almost no deal-in gain)...
FOLD_THREAT = 0.8
# ...and our best reachable distance is at least this far from a legal win.
FOLD_DISTANCE = 2.0
# Push-mode danger weight against offense normalized to [0, 1].
DEFENSE_WEIGHT = 0.3

# Careful push: with a threat at this level, if every fastest discard is
# dangerous (> SAFE_DANGER) and a safe tile exists within CAUTION_WINDOW
# distance steps, give up the step(s) for the safety. Never engages at tenpai.
CAUTION_THREAT = 0.7
CAUTION_WINDOW = 1.0
SAFE_DANGER = 0.1

# Threat: melds are the strongest public tenpai signal MCR has (no riichi
# declaration), lateness compounds it.
_THREAT_PER_MELD = 0.3
_LATENESS_MID = 0.1  # wall below a third of total
_LATENESS_ENDGAME = 0.2  # wall below a sixth of total

# Danger base by tile class: middles sit in the most run windows.
_DANGER_MIDDLE = 0.8
_DANGER_EDGE = 0.6
_DANGER_TERMINAL = 0.4
_DANGER_HONOR = 0.3
_HOT_SUIT_MULT = 1.5
# MCR has no furiten rule, so an opponent's own discard is not *provably*
# safe — but they shed it, so they don't need it. Strong soft signal.
_THEIR_DISCARD_MULT = 0.3

_OFFICIAL_BASE_EACH = 8  # mcr-official per-loser additive payment

_SUITS = ("W", "B", "T")


@dataclasses.dataclass(frozen=True)
class Threat:
    """Visible-evidence estimate of one opponent's progress.

    `level` in [0, 1]; `hot_suit` is the suit of a visible flush commitment
    (>= 2 melds whose suited tiles all share one suit), else None. A fully
    concealed hand reads as near-zero — the documented Stage-A blind spot.
    """

    level: float
    hot_suit: str | None


def decide_action(
    view: SeatView,
    legal_actions: list[Action],
    seat: int,
    prompt_kind: PromptKind,
) -> Action:
    """Choose one action from `legal_actions` for `seat` (v1 policy)."""
    # 1. A legal win is always taken.
    for action in legal_actions:
        if action["type"] == "HU":
            return action

    config = resolve_config(view["ruleset"])
    remaining = remaining_counts(view, seat)

    # 2. GANG, gated on not worsening the hand (v1 refinement of v0's
    # always-GANG): the kong's fan and free replacement draw are taken
    # whenever the post-kong distance is no worse than the best non-kong
    # alternative. The gate exists because always-GANG can wreck a hand —
    # konging four tiles that serve as run components, or opening a concealed
    # tenpai whose floor clearance depended on the Concealed Hand fan.
    gangs = [a for a in legal_actions if a["type"] == "GANG"]
    if gangs:
        gang = _best_viable_gang(gangs, view, seat, prompt_kind, config, remaining)
        if gang is not None:
            return gang

    if prompt_kind == "CLAIM":
        return _decide_claim(view, legal_actions, seat, config, remaining)
    return _decide_discard(view, legal_actions, seat, config, remaining)


def _best_viable_gang(
    gangs: list[Action],
    view: SeatView,
    seat: int,
    prompt_kind: PromptKind,
    config: dict[str, Any],
    remaining: dict[Tile, int],
) -> Action | None:
    """The (tile, kind)-minimal kong whose post-kong distance is <= the best
    non-kong alternative, or None if every kong worsens the hand."""
    seat_data = view["seats"][seat]
    concealed: list[Tile] = list(seat_data["concealed"])
    melds: list[Meld] = list(seat_data["melds"])
    seat_wind = seat_data["seat_wind"]
    round_wind = view["round_wind"]

    if prompt_kind == "DISCARD":
        # Alternative: the best reachable distance over legal discards.
        alternative = min(
            effective_distance(
                _without(concealed, t), melds, seat_wind, round_wind, config, remaining
            )
            for t in set(concealed)
        )
    else:
        # CLAIM: the PASS baseline (PENG/CHI alternatives are evaluated by
        # the claim logic if the kong is refused).
        alternative = effective_distance(
            concealed, melds, seat_wind, round_wind, config, remaining
        )

    viable: list[Action] = []
    for action in gangs:
        after_concealed, after_melds = _apply_gang(action, concealed, melds, seat)
        d = effective_distance(
            after_concealed, after_melds, seat_wind, round_wind, config, remaining
        )
        if d <= alternative:
            viable.append(action)
    if not viable:
        return None
    return min(viable, key=lambda a: (tile_sort_key(a["tile"]), a["kind"]))  # type: ignore[typeddict-item]


def _apply_gang(
    action: Action, concealed: list[Tile], melds: list[Meld], seat: int
) -> tuple[list[Tile], list[Meld]]:
    """The (concealed, melds) after a kong, mirroring the engine transition.
    The post-kong hand is 3k+1 (the replacement draw restores the count), so
    its distance is directly comparable to a post-discard hand's."""
    tile: Tile = action["tile"]  # type: ignore[typeddict-item]
    kind = action["kind"]  # type: ignore[typeddict-item]
    new_concealed = list(concealed)
    new_melds = [dict(m) for m in melds]

    if kind == "CONCEALED":
        for _ in range(4):
            new_concealed.remove(tile)
        new_melds.append(
            {"type": "GANG_CONCEALED", "tiles": [tile] * 4, "called_from_seat": seat}
        )
    elif kind == "EXPOSED":
        for _ in range(3):
            new_concealed.remove(tile)
        new_melds.append({"type": "GANG_EXPOSED", "tiles": [tile] * 4, "called_from_seat": seat})
    else:  # ADDED: upgrade the existing PENG of this tile
        new_concealed.remove(tile)
        for m in new_melds:
            if m["type"] == "PENG" and m["tiles"][0] == tile:
                m["type"] = "GANG_ADDED"
                m["tiles"] = [tile] * 4
                break
    return new_concealed, [m for m in new_melds]  # type: ignore[misc]


# --- Distance and offense ----------------------------------------------------


def _ron_waits(
    concealed: list[Tile],
    melds: list[Meld],
    seat_wind: Tile,
    round_wind: Tile,
    config: dict[str, Any],
) -> list[tuple[Tile, int]]:
    """Ron-feasible waits of a tenpai hand as (tile, fan_total) pairs."""
    waits: list[tuple[Tile, int]] = []
    for wait in pymj.winning_tiles(concealed, melds):
        fans = pymj.calculate_fan(
            concealed,
            melds,
            wait,
            win_type="DISCARD",
            seat_wind=seat_wind,
            round_wind=round_wind,
            ruleset_config=config,
        )
        if fans:
            waits.append((wait, sum(f["value"] for f in fans)))
    return waits


def effective_distance(
    concealed: list[Tile],
    melds: list[Meld],
    seat_wind: Tile,
    round_wind: Tile,
    config: dict[str, Any],
    remaining: dict[Tile, int],
) -> float:
    """v0's fan-aware distance, plus dead-wait awareness: a tenpai whose every
    ron-feasible wait has zero live copies can never win — all four copies are
    visible, so no opponent can discard it and we can never draw it. Ranked
    like a sub-floor tenpai (needs reshaping)."""
    s = _shanten(tuple(concealed), _meld_key(melds))
    if s != 0:
        return float(s)
    waits = _ron_waits(concealed, melds, seat_wind, round_wind, config)
    if not waits:
        return SUBFLOOR_TENPAI_DISTANCE
    if all(remaining.get(t, 0) == 0 for t, _ in waits):
        return SUBFLOOR_TENPAI_DISTANCE
    return 0.0


def weighted_ukeire(
    concealed: list[Tile],
    melds: list[Meld],
    remaining: dict[Tile, int],
) -> int:
    """Acceptance weighted by live copies: Σ remaining[t] over tile types whose
    draw lowers shanten. v0 counted types; 8 live improvers beat 3 even when
    spread over fewer types."""
    melds_key = _meld_key(melds)
    base = _shanten(tuple(concealed), melds_key)
    total = 0
    for tile in _ALL_TILE_TYPES:
        if concealed.count(tile) >= 4:
            continue
        drawn = [*concealed, tile]
        best = min(_shanten(tuple(_without(drawn, d)), melds_key) for d in set(drawn))
        if best < base:
            total += remaining.get(tile, 0)
    return total


def win_value(fan_total: int, conversion: dict[str, Any] | None) -> int:
    """Per-loser payment proxy for a discard win at `fan_total` — the house
    tier `X` under `house-table`, `fan + base` under mcr-official. Only used
    comparatively, so the proxy's scale is irrelevant."""
    if conversion and conversion.get("scheme") == "house-table":
        return int(lookup_x(fan_total, conversion["tiers"]))
    return fan_total + _OFFICIAL_BASE_EACH


def tenpai_wait_ev(
    waits: list[tuple[Tile, int]],
    remaining: dict[Tile, int],
    conversion: dict[str, Any] | None,
) -> float:
    """sum(live copies * payout) over ron-feasible waits — discrete-fan
    integration at k=1: integrate over the actual waits and their actual
    payouts, never a mean fan."""
    return float(sum(remaining.get(t, 0) * win_value(fan, conversion) for t, fan in waits))


# --- Threat and danger ---------------------------------------------------------


def opponent_threat(opp: SeatViewOpponent, view: SeatView) -> Threat:
    """Scalar threat from visible evidence: exposed melds + game lateness,
    plus a flush-commitment suit when >= 2 melds agree on one suit (honor
    melds — including masked concealed kongs — don't break commitment)."""
    melds = opp["melds"]
    level = _THREAT_PER_MELD * min(len(melds), 3)
    wall = view["wall"]
    if wall["remaining_count"] < wall["total"] / 6:
        level += _LATENESS_ENDGAME
    elif wall["remaining_count"] < wall["total"] / 3:
        level += _LATENESS_MID
    level = min(1.0, level)

    hot_suit: str | None = None
    if len(melds) >= 2:
        suits = {t[0] for m in melds for t in m.get("tiles", ()) if t[0] in _SUITS}
        if len(suits) == 1:
            hot_suit = next(iter(suits))
    return Threat(level=level, hot_suit=hot_suit)


def _run_window_live(tile: Tile, remaining: dict[Tile, int]) -> bool:
    """Could any run wait include `tile`? True iff some window of two needed
    neighbors has both tiles still unseen (the "no-chance" heuristic)."""
    suit, rank = tile[0], int(tile[1])
    for a, b in ((rank - 2, rank - 1), (rank - 1, rank + 1), (rank + 1, rank + 2)):
        if (
            a >= 1
            and b <= 9
            and remaining.get(f"{suit}{a}", 0) > 0
            and remaining.get(f"{suit}{b}", 0) > 0
        ):
            return True
    return False


def discard_danger(
    tile: Tile,
    view: SeatView,
    seat: int,
    remaining: dict[Tile, int],
    threats: dict[int, Threat],
) -> float:
    """Deal-in danger of discarding `tile`, summed over opponents weighted by
    threat level. `remaining[tile]` already excludes our held copies, so it is
    exactly the copies an opponent could hold for a pair/pung wait."""
    total = 0.0
    for opp_seat, threat in threats.items():
        if threat.level <= 0.0:
            continue
        suit = tile[0]
        if suit in _SUITS:
            rank = int(tile[1])
            if 3 <= rank <= 7:
                base = _DANGER_MIDDLE
            elif rank in (2, 8):
                base = _DANGER_EDGE
            else:
                base = _DANGER_TERMINAL
            # No-chance: no live run window AND no copies left for a
            # pair/shanpon wait -> no wait can include this tile.
            if remaining.get(tile, 0) == 0 and not _run_window_live(tile, remaining):
                base = 0.0
            if threat.hot_suit == suit:
                base *= _HOT_SUIT_MULT
        else:
            # Honors can only be won as a pair/pung wait: scale by the copies
            # the opponent could still hold (all-visible -> provably safe).
            base = _DANGER_HONOR * min(remaining.get(tile, 0), 2) / 2
        if tile in view["seats"][opp_seat]["discards"]:
            base *= _THEIR_DISCARD_MULT
        total += threat.level * base
    return total


# --- DISCARD -------------------------------------------------------------------


def _decide_discard(
    view: SeatView,
    legal_actions: list[Action],
    seat: int,
    config: dict[str, Any],
    remaining: dict[Tile, int],
) -> Action:
    seat_data = view["seats"][seat]
    concealed: list[Tile] = list(seat_data["concealed"])
    melds: list[Meld] = list(seat_data["melds"])
    seat_wind = seat_data["seat_wind"]
    round_wind = view["round_wind"]
    conversion = config.get("conversion")

    threats = {
        i: opponent_threat(view["seats"][i], view)  # type: ignore[arg-type]
        for i in range(4)
        if i != seat
    }
    max_threat = max((t.level for t in threats.values()), default=0.0)

    plays = [a for a in legal_actions if a["type"] == "PLAY"]
    scored: list[tuple[float, float, Action, list[Tile]]] = []
    for action in plays:
        rem = _without(concealed, action["tile"])
        d = effective_distance(rem, melds, seat_wind, round_wind, config, remaining)
        danger = discard_danger(action["tile"], view, seat, remaining, threats)
        scored.append((d, danger, action, rem))

    best_d = min(d for d, _, _, _ in scored)

    if max_threat >= FOLD_THREAT and best_d >= FOLD_DISTANCE:
        # Fold: the hand is hopeless against a visible threat — stop paying.
        # Safest tile first; keep shape (distance) only as a free tie-break.
        return min(
            scored,
            key=lambda c: (c[1], c[0], tile_sort_key(c[2]["tile"])),  # type: ignore[typeddict-item]
        )[2]

    # Careful push: a strong threat is visible, we are not tenpai (never break
    # a live tenpai — we are racing too), every fastest discard is dangerous,
    # and a (nearly) provably-safe tile exists within one step of the fastest
    # shape. Pay the step for the safety; deal-ins pay double under the house
    # conversion, a one-step delay usually doesn't.
    if max_threat >= CAUTION_THREAT and best_d >= 1.0:
        fastest = [c for c in scored if c[0] == best_d]
        if min(danger for _, danger, _, _ in fastest) > SAFE_DANGER:
            safe = [
                c for c in scored if c[1] <= SAFE_DANGER and c[0] <= best_d + CAUTION_WINDOW
            ]
            if safe:
                return _pick_by_offense(
                    safe, melds, seat_wind, round_wind, config, conversion, remaining,
                    danger_weight=0.0,
                )

    tied = [c for c in scored if c[0] == best_d]
    if len(tied) == 1:
        return tied[0][2]
    # Push: offense first (live-copy weighted), danger separates near-ties.
    return _pick_by_offense(
        tied, melds, seat_wind, round_wind, config, conversion, remaining,
        danger_weight=DEFENSE_WEIGHT,
    )


def _pick_by_offense(
    candidates: list[tuple[float, float, Action, list[Tile]]],
    melds: list[Meld],
    seat_wind: Tile,
    round_wind: Tile,
    config: dict[str, Any],
    conversion: dict[str, Any] | None,
    remaining: dict[Tile, int],
    *,
    danger_weight: float,
) -> Action:
    """Best candidate at the candidates' own minimum distance: offense
    (wait EV at tenpai, live-copy ukeire otherwise) normalized to [0, 1],
    minus `danger_weight` x danger, ties broken by tile_sort_key."""
    best_d = min(c[0] for c in candidates)
    tier = [c for c in candidates if c[0] == best_d]
    if len(tier) == 1:
        return tier[0][2]

    if best_d == 0.0:
        offense = [
            tenpai_wait_ev(
                _ron_waits(rem, melds, seat_wind, round_wind, config), remaining, conversion
            )
            for _, _, _, rem in tier
        ]
    else:
        offense = [float(weighted_ukeire(rem, melds, remaining)) for _, _, _, rem in tier]
    max_offense = max(offense)
    scale = max_offense if max_offense > 0 else 1.0

    def _key(idx: int) -> tuple[float, tuple[int, int]]:
        _, danger, action, _ = tier[idx]
        score = offense[idx] / scale - danger_weight * danger
        return (-score, tile_sort_key(action["tile"]))  # type: ignore[typeddict-item]

    return tier[min(range(len(tier)), key=_key)][2]


# --- CLAIM -----------------------------------------------------------------------


def _decide_claim(
    view: SeatView,
    legal_actions: list[Action],
    seat: int,
    config: dict[str, Any],
    remaining: dict[Tile, int],
) -> Action:
    """v0's strict-improvement claim rule, evaluated with the dead-wait-aware
    distance. Claims stay offense-only (spec non-goal). Claiming on equal
    distance with better ukeire was tried and *measured worse* (CRN eval,
    -5 pts/hand): opening the hand costs more value than the speed buys —
    see spec § Alternatives."""
    seat_data = view["seats"][seat]
    concealed: list[Tile] = list(seat_data["concealed"])
    melds: list[Meld] = list(seat_data["melds"])
    seat_wind = seat_data["seat_wind"]
    round_wind = view["round_wind"]
    last = view["last_discard"]
    assert last is not None, "CLAIM prompt with no last_discard"

    pass_action: Action = {"type": "PASS"}
    best_action: Action = pass_action
    best_distance = effective_distance(concealed, melds, seat_wind, round_wind, config, remaining)

    for action in legal_actions:
        if action["type"] not in ("PENG", "CHI"):
            continue
        after_concealed, after_melds = _apply_claim(action, concealed, melds, last)
        reachable = min(
            effective_distance(
                _without(after_concealed, d), after_melds, seat_wind, round_wind, config, remaining
            )
            for d in set(after_concealed)
        )
        if reachable < best_distance:
            best_distance = reachable
            best_action = action
    return best_action


__all__ = [
    "DEFENSE_WEIGHT",
    "FOLD_DISTANCE",
    "FOLD_THREAT",
    "Threat",
    "decide_action",
    "discard_danger",
    "effective_distance",
    "opponent_threat",
    "tenpai_wait_ev",
    "weighted_ukeire",
    "win_value",
]
