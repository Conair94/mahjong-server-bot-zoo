"""Meld display declutter (user feedback 2026-06-11): melds render as tiles
only — no type label, no "from <wind>" provenance. The one tile-invisible
distinction, concealed-vs-exposed kong (they score differently), renders as
the physical-table back-face-face-back pattern for the owner.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page, expect

from mahjong.engine.state import initial_state, project
from mahjong.engine.types import RuleSetRef

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio

_TEST_RULESET: RuleSetRef = cast(RuleSetRef, {"id": "mcr-2006", "version": 1})


def _hello() -> dict[str, Any]:
    return {"kind": "HELLO", "seq": 1, "protocol_version": 1, "server_id": "mahjong-test"}


def _attached_with_melds() -> dict[str, Any]:
    state = initial_state(_TEST_RULESET, seed=42)
    snapshot = cast(dict[str, Any], project(state, 0))
    # Own seat: an exposed pung (claimed) + a concealed kong.
    snapshot["seats"][0]["melds"] = [
        {"type": "PENG", "tiles": ["B5", "B5", "B5"], "called_tile": "B5", "called_from_seat": 2},
        {"type": "GANG_CONCEALED", "tiles": ["T7", "T7", "T7", "T7"], "called_from_seat": 0},
    ]
    # Opponent: a chi with provenance, plus a hidden concealed kong.
    snapshot["seats"][1]["melds"] = [
        {"type": "CHI", "tiles": ["W2", "W3", "W4"], "called_tile": "W3", "called_from_seat": 0},
        {"type": "GANG_CONCEALED", "called_from_seat": 1, "hidden": True},
    ]
    return {
        "kind": "ATTACHED",
        "seq": 2,
        "table_id": 1,
        "seat": 0,
        "hand_index": 0,
        "snapshot": snapshot,
        "resume_buffer_size": 0,
    }


async def test_melds_render_tiles_only_no_labels(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached_with_melds())

    pane = page.locator("game-pane")
    await expect(pane.locator(".minimal-wrap, .table-ascii")).to_be_visible(timeout=5000)

    # Locator CSS pierces the shadow root; the host's own inner_text doesn't.
    text = await pane.locator(".pane").inner_text()
    # No type labels...
    for label in ("PENG", "CHI", "GANG", "GANG_CONCEALED", "GANG_EXPOSED", "GANG_ADDED"):
        assert label not in text, f"meld type label {label!r} leaked into the view"
    # ...and no provenance.
    assert "from " not in text, "meld provenance ('from <wind>') leaked into the view"
    # The exposed pung's tiles are visible (display notation: engine B5 → 5D).
    assert text.count("5D") >= 3
    # Own concealed kong: back-face-face-back — exactly two T7 (→ 7B) faces
    # framed by face-down backs (ASCII tile style: ▒▒).
    assert "[▒▒ 7B 7B ▒▒]" in text
    # Opponent's hidden concealed kong stays fully face-down.
    assert "[▒▒ ▒▒ ▒▒ ▒▒]" in text
