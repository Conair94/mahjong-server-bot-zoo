"""Between-hand dealer rotation (session structure, config-driven).

Spec: docs/specs/scoring-config.md § Renchan (orchestration).

Renchan ("dealer repeats on a win") is a *session* rule, not an engine
transition — a Botzone game is a single hand, so the bot never models it. It
lives here, between hands, shared by every match loop (registry + web) so the
two can't drift on how the next dealer is chosen.
"""

from __future__ import annotations

from typing import Any

from mahjong.engine.types import Terminal


def next_dealer(current_dealer: int, terminal: Terminal | None, config: dict[str, Any]) -> int:
    """Pick the dealer for the next hand.

    With `dealer_repeat_on_win` set, the dealer keeps the seat iff they won the
    hand just completed (renchan). Otherwise — a non-dealer win, an exhaustive
    `DRAW`, or a ruleset without the flag — rotate `(dealer + 1) % 4`.
    """
    if (
        config.get("dealer_repeat_on_win")
        and terminal is not None
        and terminal["kind"] == "HU"
        and terminal["winner"] == current_dealer
    ):
        return current_dealer
    return (current_dealer + 1) % 4
