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

SeatKind = Literal["human", "bot"]


@dataclasses.dataclass(frozen=True)
class SeatComposition:
    """One seat's declared kind, as parsed from ``CREATE_TABLE.seats[i]``."""

    kind: SeatKind


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
        extra = set(entry.keys()) - {"kind"}
        if extra:
            raise SeatsParseError(f"seats[{i}] contains forbidden field(s): {sorted(extra)}")
        kind = entry.get("kind")
        if kind not in _ALLOWED_KINDS:
            raise SeatsParseError(f"seats[{i}].kind must be 'human' or 'bot', got {kind!r}")
        if kind == "human":
            saw_human = True
        parsed.append(SeatComposition(kind=kind))

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
