"""Client reducer applies claims on CLAIM_RESOLUTION, not CLAIM_DECISION
(Spec 29 Bug C).

Regression for the "5 melds / could not mahjong" bug found in ConnorL's
2026-06-05 game: in a contested claim window the human declared CHI B6B7B8 and
an opponent declared an overriding GANG B6. The engine correctly awarded the
tile to the GANG, but the old client mutated the view on *every* CLAIM_DECISION,
so the losing CHI built a phantom meld (and deleted B7/B8 from the hand) that was
never rolled back. The fix makes CLAIM_RESOLUTION the authoritative mutation
point; a losing decision now touches nothing.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


def _view(*, own_concealed: list[str], last_discard: dict[str, Any]) -> dict[str, Any]:
    """Seat 0 is the local player (list concealed); seats 1-3 are opponents
    (count concealed)."""
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
                # Seat 3 is the discarder in these fixtures.
                "discards": [last_discard["tile"]] if s == last_discard["seat"] else [],
            }
            for s in (1, 2, 3)
        ),
    ]
    return {
        "round_wind": "F1",
        "hand_index": 0,
        "turn_index": 10,
        "dealer_seat": 0,
        "current_actor": last_discard["seat"],
        "phase": "CLAIM_WINDOW",
        "wall": {"remaining_count": 40, "drawn_count": 0},
        "seats": seats,
        "last_discard": last_discard,
        "last_drawn": None,
        "pending_claims": [],
    }


async def _apply_chain(
    page: Page, server: FakeWireServer, view: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Fold a list of events through the real reducer; return the final view."""
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


async def test_losing_chi_leaves_no_phantom_meld(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """The exact ConnorL scenario: own CHI loses to an opponent's GANG. The
    local hand must be untouched (no phantom meld, B7/B8 still in hand) and the
    opponent must get the exposed kong."""
    view = _view(
        own_concealed=["B7", "B8", "W2", "W3", "W4"],
        last_discard={"seat": 3, "tile": "B6"},
    )
    events = [
        # Loser: own CHI B6B7B8 (informational now — must NOT mutate).
        {"event": "CLAIM_DECISION", "seat": 0, "decision": "CHI", "chi_tiles": ["B6", "B7", "B8"]},
        # Winner: opponent seat 2 exposed GANG of B6 (also informational).
        {"event": "CLAIM_DECISION", "seat": 2, "decision": "GANG", "kind": "EXPOSED", "tile": "B6"},
        # Authoritative resolution: seat 2 wins the GANG.
        {
            "event": "CLAIM_RESOLUTION",
            "outcome": "CLAIMED",
            "winning_seat": 2,
            "winning_claim": "GANG",
            "winning_kind": "EXPOSED",
            "called_tile": "B6",
        },
    ]
    result = await _apply_chain(page, fake_wire_server, view, events)

    seat0 = next(s for s in result["seats"] if s["seat"] == 0)
    assert seat0["melds"] == [], f"local hand must have no phantom meld; got {seat0['melds']}"
    assert seat0["concealed"] == [
        "B7",
        "B8",
        "W2",
        "W3",
        "W4",
    ], f"B7/B8 must stay in hand; got {seat0['concealed']}"

    seat2 = next(s for s in result["seats"] if s["seat"] == 2)
    assert len(seat2["melds"]) == 1
    meld = seat2["melds"][0]
    assert meld["type"] == "GANG_EXPOSED"
    assert meld["tiles"] == ["B6", "B6", "B6", "B6"]
    # Opponent gang consumed 3 concealed tiles (13 -> 10).
    assert seat2["concealed"]["count"] == 10


async def test_winning_chi_builds_meld_on_resolution(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """The winner's meld IS applied — but on the resolution, not the decision."""
    view = _view(
        own_concealed=["B7", "B8", "W2", "W3", "W4"],
        last_discard={"seat": 3, "tile": "B6"},
    )
    events = [
        {"event": "CLAIM_DECISION", "seat": 0, "decision": "CHI", "chi_tiles": ["B6", "B7", "B8"]},
        {
            "event": "CLAIM_RESOLUTION",
            "outcome": "CLAIMED",
            "winning_seat": 0,
            "winning_claim": "CHI",
            "winning_chi_tiles": ["B6", "B7", "B8"],
        },
    ]
    after_decision_only = await _apply_chain(page, fake_wire_server, view, events[:1])
    seat0_mid = next(s for s in after_decision_only["seats"] if s["seat"] == 0)
    assert seat0_mid["melds"] == [], "decision alone must not build the meld"
    assert seat0_mid["concealed"] == ["B7", "B8", "W2", "W3", "W4"]

    result = await _apply_chain(page, fake_wire_server, view, events)
    seat0 = next(s for s in result["seats"] if s["seat"] == 0)
    assert len(seat0["melds"]) == 1
    assert seat0["melds"][0]["type"] == "CHI"
    assert seat0["melds"][0]["tiles"] == ["B6", "B7", "B8"]
    # B7/B8 consumed; B6 came off the discard (never in hand).
    assert seat0["concealed"] == ["W2", "W3", "W4"]
    # Discarder's pile no longer shows the called B6.
    seat3 = next(s for s in result["seats"] if s["seat"] == 3)
    assert "B6" not in seat3["discards"]
