"""The HAND_END *frame* must drive the §22.9 summary (wire→UI dispatch).

Regression for the "no hand summary after a game ends" bug. The renderer and the
applyHandEnd reducer were both present and unit-tested (test_hand_end_summary.py
pins the renderer given a pre-set view.terminal), but app.js never dispatched the
top-level HAND_END *frame* to the reducer — so in real play the summary never
appeared.  These tests drive the real <mahjong-app> over the FakeWireServer and
assert the summary shows up after a HAND_END frame, and not before.

The terminal payload mirrors the wire shape: the record HAND_END event minus its
wrapper fields (so `winner` is an array, as _terminal_from_record produces).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page, expect

from mahjong.engine.state import initial_state, project
from mahjong.engine.types import RuleSetRef

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio

_TEST_SEED = 42
_TEST_RULESET: RuleSetRef = cast(RuleSetRef, {"id": "mcr-2006", "version": 1})


def _hello() -> dict[str, Any]:
    return {"kind": "HELLO", "seq": 1, "protocol_version": 1, "server_id": "mahjong-test"}


def _attached(own_seat: int = 0) -> dict[str, Any]:
    state = initial_state(_TEST_RULESET, seed=_TEST_SEED)
    snapshot = cast(dict[str, Any], project(state, own_seat))
    return {
        "kind": "ATTACHED",
        "seq": 2,
        "table_id": 1,
        "seat": own_seat,
        "hand_index": 0,
        "snapshot": snapshot,
        "resume_buffer_size": 0,
    }


def _hand_end_hu() -> dict[str, Any]:
    """A top-level HAND_END frame with a HU `terminal` payload (winner is an
    array, as the wire carries it)."""
    return {
        "kind": "HAND_END",
        "seq": 9,
        "table_id": 1,
        "hand_index": 0,
        "next_hand_seq": None,
        "terminal": {
            "kind": "HU",
            "winner": [2],
            "win_tile": "B5",
            "win_type": "DISCARD",
            "deal_in_seat": 1,
            "fan": [{"name": "All Pungs", "value": 6}, {"name": "Prevalent Wind", "value": 2}],
            "fan_total": 8,
            "score_delta": [-8, -16, 24, -8],
            "final_hands": [
                {"seat": 0, "concealed": ["W2", "W3"], "melds": [], "flowers": []},
                {"seat": 1, "concealed": ["T1", "T2"], "melds": [], "flowers": []},
                {"seat": 2, "concealed": ["B5", "B5"], "melds": [], "flowers": []},
                {"seat": 3, "concealed": ["J1", "J2"], "melds": [], "flowers": []},
            ],
        },
    }


async def _wait_for_attached(page: Page) -> None:
    await expect(page.locator("game-pane").locator(".table-ascii")).to_be_visible(timeout=5000)


async def test_no_summary_before_hand_end(page: Page, fake_wire_server: FakeWireServer) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    # Mid-hand: the summary panel must be absent.
    await expect(page.locator("game-pane").locator(".hand-end-summary")).to_have_count(0)


async def test_hand_end_frame_renders_summary(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)

    # The frame that was previously dropped by app.js.
    await fake_wire_server.send(_hand_end_hu())

    gp = page.locator("game-pane")
    summary = gp.locator(".hand-end-summary")
    await expect(summary).to_be_visible(timeout=5000)

    # Winner headline (seat 2 → West), fan list + total, and the per-seat swing.
    await expect(gp.locator(".he-headline")).to_contain_text("wins")
    fan = await gp.locator(".he-fan").inner_text()
    assert "All Pungs" in fan and "Total" in fan and "8" in fan, fan
    winner_row = gp.locator(".he-score-row.he-winner")
    await expect(winner_row).to_contain_text("+24")
