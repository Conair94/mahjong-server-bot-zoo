"""Project a recorded hand into the per-seat wire EVENT stream a replay viewer
renders (account-records-replay.md, Spec 32 / FB-04).

The live client renders a hand by folding wire EVENT frames through
``apply_event.js``. A replay reuses that exact renderer: we take the recorded
events, drop the record-keeping wrappers (HEADER / FOOTER), and project each
remaining event for the viewing seat with the *same* ``project_event`` the live
session-mux uses (``mahjong/sessions/mux.py``). So a replayed stream is shaped
byte-for-byte like the live stream the player originally saw — no second
renderer, no second privacy rule.

``seat=None`` is the public projection (admin / non-participant view): own-draw
tiles and concealed-kong identities are elided exactly as they were in-hand.
"""

from __future__ import annotations

from typing import Any, cast

from mahjong.engine.state import project as project_state
from mahjong.engine.state import project_event
from mahjong.records.replay import replay

# Record-keeping wrappers that are not wire-visible game events. The live path
# writes these to the record (manager.py) but never fans them out to seats.
_NON_VISIBLE: frozenset[str] = frozenset({"HEADER", "FOOTER"})


def projected_events_for_seat(
    record_events: list[dict[str, Any]], *, seat: int | None
) -> list[dict[str, Any]]:
    """The ordered inner-event payloads a replay viewer folds through the live
    reducer. HEADER/FOOTER are dropped; every other event (incl. HAND_END) is
    projected for ``seat`` (``None`` = public)."""
    return [
        project_event(e, seat)
        for e in record_events
        if e.get("event") not in _NON_VISIBLE
    ]


def initial_snapshot_for_seat(
    record_events: list[dict[str, Any]], *, seat: int | None
) -> dict[str, Any]:
    """The post-deal board for ``seat`` — the same shape an ATTACHED snapshot
    carries live. The replay viewer seeds its board with this, then applies the
    projected events on top."""
    state = next(replay(record_events))  # first yielded state is the deal
    return cast(dict[str, Any], project_state(state, seat))


__all__ = ["initial_snapshot_for_seat", "projected_events_for_seat"]
