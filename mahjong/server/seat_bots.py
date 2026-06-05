"""Registry of selectable *in-process* bots for table creation.

When a ``CREATE_TABLE.seats[i]`` declares ``kind: "bot"`` it may also name a
``bot_id`` choosing *which* bot fills that seat.  This module is the single
source of truth for that menu: it maps each ``bot_id`` to (a) a factory that
builds a fresh ``SeatAdapter`` for a hand and (b) the display metadata the
client renders in the create-table picker.

Scope note: this is deliberately separate from ``mahjong.bots.registry``
(``BotRegistry``), which catalogs *out-of-process* bots via sandbox manifests
for the bot-runner protocol.  These are in-process adapters the table loop
constructs directly — a different concern with a different lifecycle.  Only one
real bot (``v0``) exists today; the factory indirection earns its keep the
moment a second lands, and keeps the wire/UI honest in the meantime.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, cast

from mahjong.adapters.base import SeatAdapter
from mahjong.adapters.v0 import V0Adapter


@dataclasses.dataclass(frozen=True)
class SeatBot:
    """One selectable in-process bot.

    ``bot_id`` is the wire/registry key; ``label`` and ``description`` are for
    the client picker; ``factory`` builds a fresh adapter per hand (adapters
    cache per-hand seat state, so each hand gets its own instance).
    """

    bot_id: str
    label: str
    description: str
    factory: Callable[[], SeatAdapter]

    def to_wire(self) -> dict[str, Any]:
        """Shape advertised in ``HELLO.bots[i]`` for the create-table picker."""
        return {
            "bot_id": self.bot_id,
            "label": self.label,
            "description": self.description,
        }


# Insertion order is the menu order the client shows; the first entry is the
# default when a bot seat omits ``bot_id``.
SEAT_BOTS: dict[str, SeatBot] = {
    "v0": SeatBot(
        bot_id="v0",
        label="v0 — greedy offense",
        description=(
            "Fan-aware greedy offense bot. Always claims wins and kongs; "
            "discards to minimise distance to a scoring hand. No defense."
        ),
        # cast: V0Adapter conforms to SeatAdapter structurally, but its
        # ``kind = "bot"`` class attr infers as ``str`` not the Literal the
        # Protocol declares — the same cast the table loop has always used.
        factory=lambda: cast(SeatAdapter, V0Adapter()),
    ),
}

DEFAULT_BOT_ID: str = next(iter(SEAT_BOTS))


def is_known_bot(bot_id: str) -> bool:
    return bot_id in SEAT_BOTS


def build_bot_adapter(bot_id: str) -> SeatAdapter:
    """Construct a fresh adapter for *bot_id*.

    Raises ``KeyError`` for an unknown id; callers that accept wire input
    validate against ``is_known_bot`` first and translate to a framing error.
    """
    return SEAT_BOTS[bot_id].factory()


def available_bots_wire() -> list[dict[str, Any]]:
    """The ``HELLO.bots`` advertisement: every selectable bot, in menu order."""
    return [bot.to_wire() for bot in SEAT_BOTS.values()]


__all__ = [
    "DEFAULT_BOT_ID",
    "SEAT_BOTS",
    "SeatBot",
    "available_bots_wire",
    "build_bot_adapter",
    "is_known_bot",
]
