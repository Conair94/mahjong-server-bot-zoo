"""Config-driven fan -> score-delta conversion (the RL reward contract).

Spec: docs/specs/scoring-config.md.

This is the single scorer shared by the live server's terminal transition and
(eventually) the training reward — one implementation so the reward you
optimize and the score the server pays can never disagree (no training/serving
skew). The `conversion` block of the resolved ruleset config selects the scheme:

  - `mcr-official`: additive `fan + base`. The default; reproduces the formula
    the HU transition hard-coded before this module existed.
  - `house-table`: tier lookup -> per-loser multiplier of `X(fan)`.

Both schemes build the winner's delta by accumulating the losers' payments, so
the result is zero-sum by construction — no config value can produce an
unbalanced payout.
"""

from __future__ import annotations

from typing import Any

from mahjong.engine.types import WinType

# Defaults reproduce the pre-change official formula exactly, so a ruleset with
# no `conversion` block (e.g. mcr-2006.json) scores identically to before.
_OFFICIAL_BASE_EACH = 8
_OFFICIAL_BASE_DEALER_IN = 24
_OFFICIAL_BASE_OTHER = 8


def score_delta(
    winner: int,
    fan_total: int,
    win_type: WinType,
    deal_in_seat: int | None,
    conversion: dict[str, Any] | None = None,
) -> list[int]:
    """Per-seat score change for a win. Sums to zero by construction.

    `conversion` is the resolved ruleset's `conversion` block, or None/absent
    for the default `mcr-official` scheme.
    """
    conv = conversion or {"scheme": "mcr-official"}
    scheme = conv.get("scheme", "mcr-official")
    if scheme == "mcr-official":
        return _official_delta(winner, fan_total, win_type, deal_in_seat, conv)
    if scheme == "house-table":
        return _house_table_delta(winner, fan_total, win_type, deal_in_seat, conv)
    raise ValueError(f"unknown conversion scheme: {scheme!r}")


def lookup_x(fan_total: int, tiers: list[list[int]]) -> int:
    """House table lookup: first `[max_fan, X]` tier with `fan_total <= max_fan`.

    `fan_total` above the top tier clamps to the top `X` — MCR limit hands cap
    near 88 fan, but totals can exceed it by stacking yaku, so clamping (rather
    than raising) keeps the scorer total.
    """
    for max_fan, x in tiers:
        if fan_total <= max_fan:
            return x
    return tiers[-1][1]


def _official_delta(
    winner: int, fan_total: int, win_type: WinType, deal_in_seat: int | None, conv: dict[str, Any]
) -> list[int]:
    sd = conv.get("self_draw", {})
    dc = conv.get("discard", {})
    base_each = sd.get("base_each", _OFFICIAL_BASE_EACH)
    base_dealer_in = dc.get("base_dealer_in", _OFFICIAL_BASE_DEALER_IN)
    base_other = dc.get("base_other", _OFFICIAL_BASE_OTHER)

    delta = [0, 0, 0, 0]
    if win_type == "SELF_DRAW":
        pay = fan_total + base_each
        for i in range(4):
            if i == winner:
                continue
            delta[i] = -pay
            delta[winner] += pay
    else:  # DISCARD (and any non-self-draw win): dealer-in pays full, others flat
        assert deal_in_seat is not None
        pay_in = fan_total + base_dealer_in
        delta[deal_in_seat] = -pay_in
        delta[winner] += pay_in
        for i in range(4):
            if i in (winner, deal_in_seat):
                continue
            delta[i] = -base_other
            delta[winner] += base_other
    return delta


def _house_table_delta(
    winner: int, fan_total: int, win_type: WinType, deal_in_seat: int | None, conv: dict[str, Any]
) -> list[int]:
    x = lookup_x(fan_total, conv["tiers"])
    delta = [0, 0, 0, 0]
    if win_type == "SELF_DRAW":
        pay = conv["self_draw"]["each_mult"] * x
        for i in range(4):
            if i == winner:
                continue
            delta[i] = -pay
            delta[winner] += pay
    else:  # DISCARD: dealer-in pays a heavier multiple than the other two losers
        assert deal_in_seat is not None
        pay_in = conv["discard"]["dealer_in_mult"] * x
        pay_other = conv["discard"]["other_mult"] * x
        delta[deal_in_seat] = -pay_in
        delta[winner] += pay_in
        for i in range(4):
            if i in (winner, deal_in_seat):
                continue
            delta[i] = -pay_other
            delta[winner] += pay_other
    return delta
