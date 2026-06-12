"""Seat composition for multi-human-seat tables (Step 8.7).

Spec: docs/specs/multi-human-seats.md § The schema / interface.

The ``CREATE_TABLE`` wire message carries an optional ``seats`` array declaring
the kind of each seat (``"human"`` or ``"bot"``).  This module is the single
parser/validator for that array; it produces a 4-tuple of ``SeatComposition``
that flows through ``TableRegistry.create_table_direct`` into
``TableHandle.__init__``.

v1 is open-lobby: ``seats[i]`` carries *only* ``kind``.  Pre-assigning a
specific ``user_id`` to a seat (invite-style tables) is a future addition; for
now any extra field on ``seats[i]`` is a framing error.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

from mahjong.server.seat_bots import is_known_bot

SeatKind = Literal["human", "bot"]


@dataclasses.dataclass(frozen=True)
class SeatComposition:
    """One seat's declared kind, as parsed from ``CREATE_TABLE.seats[i]``.

    ``bot_id`` selects *which* in-process bot fills a ``kind == "bot"`` seat
    (see ``mahjong.server.seat_bots``).  ``None`` means "use the default bot",
    resolved to ``seat_bots.DEFAULT_BOT_ID`` at adapter-build time.  Always
    ``None`` for human seats.
    """

    kind: SeatKind
    bot_id: str | None = None


# The canonical 4-tuple shape used throughout the server.
SeatsTuple = tuple[SeatComposition, SeatComposition, SeatComposition, SeatComposition]


DEFAULT_COMPOSITION: SeatsTuple = (
    SeatComposition("human"),
    SeatComposition("bot"),
    SeatComposition("bot"),
    SeatComposition("bot"),
)
"""Legacy single-human composition.  Used when ``CREATE_TABLE`` omits ``seats``."""


_ALLOWED_KINDS: frozenset[str] = frozenset({"human", "bot"})


class SeatsParseError(ValueError):
    """Raised when ``CREATE_TABLE.seats[]`` fails validation.

    The orchestrator translates this into an ``ERROR { code: "framing" }``
    wire response.  The exception ``str()`` is suitable for the framing
    ``message`` field (diagnostic, not user-facing).
    """


def parse_seats_from_wire(seats_obj: Any) -> SeatsTuple:
    """Parse and validate ``CREATE_TABLE.seats[]``.

    - ``seats_obj is None`` → ``DEFAULT_COMPOSITION`` (legacy default).
    - Otherwise: must be a 4-element list of ``{"kind": "human"|"bot"}``
      objects with no other fields and at least one ``"human"`` entry.

    Raises ``SeatsParseError`` on any validation failure.
    """
    if seats_obj is None:
        return DEFAULT_COMPOSITION
    if not isinstance(seats_obj, list):
        raise SeatsParseError("seats must be an array")
    if len(seats_obj) != 4:
        raise SeatsParseError(f"seats must have exactly 4 entries, got {len(seats_obj)}")

    parsed: list[SeatComposition] = []
    saw_human = False
    for i, entry in enumerate(seats_obj):
        if not isinstance(entry, dict):
            raise SeatsParseError(f"seats[{i}] must be an object")
        kind = entry.get("kind")
        if kind not in _ALLOWED_KINDS:
            raise SeatsParseError(f"seats[{i}].kind must be 'human' or 'bot', got {kind!r}")
        # Allowed fields are kind-dependent: ``bot_id`` selects the bot on a
        # bot seat, but is forbidden on a human seat (still open-lobby — humans
        # claim seats via ATTACH, not at create time).
        allowed = {"kind", "bot_id"} if kind == "bot" else {"kind"}
        extra = set(entry.keys()) - allowed
        if extra:
            raise SeatsParseError(f"seats[{i}] contains forbidden field(s): {sorted(extra)}")
        bot_id: str | None = None
        if kind == "bot":
            raw_bot_id = entry.get("bot_id")
            if raw_bot_id is not None:
                if not isinstance(raw_bot_id, str) or not is_known_bot(raw_bot_id):
                    raise SeatsParseError(f"seats[{i}].bot_id is not a known bot: {raw_bot_id!r}")
                bot_id = raw_bot_id
        else:
            saw_human = True
        parsed.append(SeatComposition(kind=kind, bot_id=bot_id))

    if not saw_human:
        raise SeatsParseError("seats must contain at least one 'human' entry")

    return (parsed[0], parsed[1], parsed[2], parsed[3])


__all__ = [
    "DEFAULT_COMPOSITION",
    "SeatComposition",
    "SeatKind",
    "SeatsParseError",
    "SeatsTuple",
    "parse_seats_from_wire",
]
