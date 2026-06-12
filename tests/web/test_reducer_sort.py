"""Client reducer keeps own concealed in engine-canonical order (Spec 22 § 22.7).

The engine sorts ``concealed`` after every draw; the client reducer must
mirror that so previously-drawn-then-kept tiles don't strand at the tail of
the hand (which broke the renderer's suit-break logic). The fix lives in the
reducer — NOT the renderer — because the selection cursor (``selectedTile``)
indexes into the reducer's concealed array, so sorting only the display would
desync digit/arrow tile selection from what the player sees.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


def _own_view(concealed: list[str], *, last_drawn: dict[str, Any] | None = None) -> dict[str, Any]:
    seats: list[dict[str, Any]] = [
        {
            "seat": 0,
            "seat_wind": "F1",
            "score": 0,
            "concealed": concealed,
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
        "last_drawn": last_drawn,
        "pending_claims": [],
    }


async def _apply_draw(
    page: Page, server: FakeWireServer, view: dict[str, Any], tile: str
) -> list[str]:
    """Apply a DRAW event for seat 0 via the real reducer; return seat 0's
    concealed list afterwards."""
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    return cast(
        list[str],
        await page.evaluate(
            """async ({ view, tile }) => {
              const { applyEvent } = await import('/static/apply_event.js');
              const ev = { event: "DRAW", seat: 0, tile, turn_index: 1, phase: "DISCARD" };
              const next = applyEvent(view, ev, 0);
              return next.seats.find(s => s.seat === 0).concealed;
            }""",
            {"view": view, "tile": tile},
        ),
    )


async def test_draw_resorts_own_concealed_into_canonical_order(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """A hand whose tail drifted out of order (tiles drawn-then-kept on
    earlier turns) is re-sorted when the next DRAW arrives."""
    # Simulate a drifted hand: sorted prefix, then two stranded tail tiles.
    view = _own_view(["W2", "W3", "B5", "T7", "W9", "B1"])
    result = await _apply_draw(page, fake_wire_server, view, "T2")
    # Engine-canonical order: W < B < T, then by rank.
    assert result == ["W2", "W3", "W9", "B1", "B5", "T2", "T7"]


async def test_just_drawn_tile_is_sorted_in_array_but_offset_by_renderer(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """The reducer sorts the drawn tile into canonical position; the
    just-drawn 'sits apart' cue is the renderer's job (it pulls last_drawn
    by value), so the array itself stays fully sorted."""
    view = _own_view(["W2", "T7", "B5"])
    result = await _apply_draw(page, fake_wire_server, view, "B3")
    assert result == ["W2", "B3", "B5", "T7"]
