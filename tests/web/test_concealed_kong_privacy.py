"""Concealed kong tile identity stays private to its owner (Spec 29 Bug D).

The user observed a bot's concealed gang rendered face-up. The server now redacts
the CONCEALED kong's tile from non-owners; the client reducer must therefore build
a *hidden* meld (no tiles) for an opponent's kong, while the owner keeps theirs.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


def _view(own_concealed: list[str]) -> dict[str, Any]:
    seats: list[dict[str, Any]] = [
        {
            "seat": 0,
            "seat_wind": "F1",
            "score": 0,
            "concealed": own_concealed,
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
        "turn_index": 10,
        "dealer_seat": 0,
        "current_actor": 0,
        "phase": "DISCARD",
        "wall": {"remaining_count": 40, "drawn_count": 0},
        "seats": seats,
        "last_discard": None,
        "last_drawn": None,
        "pending_claims": [],
    }


async def _apply(page: Page, server: FakeWireServer, view: dict[str, Any], event: dict[str, Any]):
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    return cast(
        dict[str, Any],
        await page.evaluate(
            """async ({ view, event }) => {
              const { applyEvent } = await import('/static/apply_event.js');
              return applyEvent(view, event, 0);
            }""",
            {"view": view, "event": event},
        ),
    )


async def _apply_seq(
    page: Page, server: FakeWireServer, view: dict[str, Any], events: list[dict[str, Any]]
):
    """Apply a sequence of events through the real reducer. If any applyEvent
    throws in the browser, page.evaluate rejects and the test fails."""
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    return cast(
        dict[str, Any],
        await page.evaluate(
            """async ({ view, events }) => {
              const { applyEvent } = await import('/static/apply_event.js');
              let v = view;
              for (const e of events) { v = applyEvent(v, e, 0); }
              return v;
            }""",
            {"view": view, "events": events},
        ),
    )


async def test_opponent_concealed_kong_is_hidden(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """An opponent's CONCEALED kong arrives with the tile redacted; the meld must
    carry no tile identity (hidden) and the seat's count drops by 4."""
    view = _view(own_concealed=["W2", "W3"])
    # Server-redacted decision: no `tile` field for the non-owner.
    event = {"event": "CLAIM_DECISION", "seat": 1, "decision": "GANG", "kind": "CONCEALED"}
    result = await _apply(page, fake_wire_server, view, event)

    seat1 = next(s for s in result["seats"] if s["seat"] == 1)
    assert len(seat1["melds"]) == 1
    meld = seat1["melds"][0]
    assert meld["type"] == "GANG_CONCEALED"
    assert meld.get("hidden") is True
    # No leaked tile identity anywhere in the meld.
    assert "tiles" not in meld or all(t is None for t in (meld.get("tiles") or [])), meld
    assert seat1["concealed"]["count"] == 9  # 13 - 4


async def test_own_concealed_kong_keeps_tiles(page: Page, fake_wire_server: FakeWireServer) -> None:
    """The owner sees their own kong's tiles (not redacted to self)."""
    view = _view(own_concealed=["W4", "W4", "W4", "W4", "B5", "B6"])
    event = {
        "event": "CLAIM_DECISION",
        "seat": 0,
        "decision": "GANG",
        "kind": "CONCEALED",
        "tile": "W4",
    }
    result = await _apply(page, fake_wire_server, view, event)

    seat0 = next(s for s in result["seats"] if s["seat"] == 0)
    assert len(seat0["melds"]) == 1
    meld = seat0["melds"][0]
    assert meld["type"] == "GANG_CONCEALED"
    assert meld["tiles"] == ["W4", "W4", "W4", "W4"]
    assert not meld.get("hidden")
    assert seat0["concealed"] == ["B5", "B6"]


async def test_event_after_opponent_concealed_kong_does_not_crash(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Regression: an opponent's hidden kong has no `tiles`, so the *next* event's
    cloneSeatView (`[...m.tiles]`) threw `TypeError: m.tiles is not iterable`,
    freezing the whole client. Reproduced from ConnorL's 2026-06-09 game: a bot's
    concealed kong (seq 101) followed by its replacement DRAW (seq 102).

    The single-event tests above missed this because the crash is on the *second*
    event, when the seat view (now carrying the tiles-less meld) is cloned.
    """
    view = _view(own_concealed=["W2", "W3"])
    kong = {"event": "CLAIM_DECISION", "seat": 1, "decision": "GANG", "kind": "CONCEALED"}
    # Any follow-on event triggers cloneSeatView over seat 1's hidden meld.
    follow_on = {"event": "DRAW", "seat": 1}
    result = await _apply_seq(page, fake_wire_server, view, [kong, follow_on])

    # The hidden meld survives the clone intact.
    seat1 = next(s for s in result["seats"] if s["seat"] == 1)
    meld = next(m for m in seat1["melds"] if m["type"] == "GANG_CONCEALED")
    assert meld.get("hidden") is True
    assert "tiles" not in meld or all(t is None for t in (meld.get("tiles") or [])), meld
