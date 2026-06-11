"""Client reducer maintains a global discard pond (minimal-view combined pond).

The SeatView projection only carries per-seat discard piles, so the minimal
view's "combined pond (old -> new)" needs a global timeline. The reducer
(apply_event.js) builds ``view.discard_pond`` as ``{seat, tile}`` in arrival
order: seeded on the first discard, appended thereafter, and pulled when a
discard is claimed into a meld (so it doesn't double-count). These fixtures
pin that contract.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


def _fresh_view() -> dict[str, Any]:
    """A start-of-hand view: seat 0 own (concealed list), others counts, no
    discards yet."""
    seats: list[dict[str, Any]] = [
        {
            "seat": 0,
            "seat_wind": "F1",
            "score": 0,
            "concealed": ["W3", "B5", "T7"],
            "melds": [],
            "flowers": [],
            "discards": [],
        },
        *(
            {
                "seat": s,
                "seat_wind": ["F1", "F2", "F3", "F4"][s],
                "score": 0,
                "concealed": {"count": 13},
                "melds": [],
                "flowers": [],
                "discards": [],
            }
            for s in (1, 2, 3)
        ),
    ]
    return {
        "round_wind": "F1",
        "hand_index": 0,
        "turn_index": 0,
        "dealer_seat": 0,
        "current_actor": 0,
        "phase": "DISCARD",
        "wall": {"remaining_count": 70, "drawn_count": 0},
        "seats": seats,
        "last_discard": None,
        "last_drawn": None,
        "pending_claims": [],
    }


async def _apply_chain(
    page: Page, server: FakeWireServer, view: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply a sequence of events via the real reducer; return the final view."""
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    return cast(
        dict[str, Any],
        await page.evaluate(
            """async ({ view, events }) => {
              const { applyEvent } = await import('/static/apply_event.js');
              let v = view;
              for (const ev of events) v = applyEvent(v, ev, 0);
              return v;
            }""",
            {"view": view, "events": events},
        ),
    )


async def test_pond_seeds_and_appends_in_arrival_order(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Three discards from different seats land in the pond in order."""
    events = [
        {"event": "DISCARD", "seat": 0, "tile": "W3", "from_hand": True, "turn_index": 1, "phase": "DISCARD"},
        {"event": "DISCARD", "seat": 1, "tile": "B5", "from_hand": True, "turn_index": 2, "phase": "DISCARD"},
        {"event": "DISCARD", "seat": 2, "tile": "T7", "from_hand": True, "turn_index": 3, "phase": "DISCARD"},
    ]
    view = await _apply_chain(page, fake_wire_server, _fresh_view(), events)
    assert view["discard_pond"] == [
        {"seat": 0, "tile": "W3"},
        {"seat": 1, "tile": "B5"},
        {"seat": 2, "tile": "T7"},
    ]


async def test_pond_drops_a_claimed_tile(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """A PENG resolution lifts the claimed tile off the pond (it joins a meld
    and is no longer 'in the pond')."""
    events = [
        {"event": "DISCARD", "seat": 0, "tile": "W3", "from_hand": True, "turn_index": 1, "phase": "DISCARD"},
        {"event": "DISCARD", "seat": 1, "tile": "B5", "from_hand": True, "turn_index": 2, "phase": "DISCARD"},
        # Seat 2 pengs seat 1's B5.
        {
            "event": "CLAIM_RESOLUTION",
            "outcome": "CLAIMED",
            "winning_seat": 2,
            "winning_claim": "PENG",
            "called_tile": "B5",
            "turn_index": 2,
            "phase": "DISCARD",
        },
    ]
    view = await _apply_chain(page, fake_wire_server, _fresh_view(), events)
    # B5 is gone from the pond; W3 remains.
    assert view["discard_pond"] == [{"seat": 0, "tile": "W3"}]
    # ...and a PENG meld formed on the claimer.
    claimer = next(s for s in view["seats"] if s["seat"] == 2)
    assert any(m["type"] == "PENG" for m in claimer["melds"]), claimer["melds"]
