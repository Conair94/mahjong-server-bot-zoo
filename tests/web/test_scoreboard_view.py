"""Spec 40 in-game scoreboard: wire→UI seam.

A real ATTACHED snapshot carrying the server's `match_scores` block, sent
through FakeWireServer into the real `<mahjong-app>`:

- the live table header reads round / wall / hand / table id (no longer the
  hardcoded "demo / —" placeholder), and is hidden in the lobby;
- the roster shows each seat's running match total (not the always-0 per-hand
  score);
- the Alt+P score pane renders one per-seat cumulative line graph.
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
_NAMES = ["Alice", "Bob", "Carol", "Dave"]
_CUMULATIVE = [16, 16, -16, -16]


def _hello() -> dict[str, Any]:
    return {"kind": "HELLO", "seq": 1, "protocol_version": 1, "server_id": "mahjong-test"}


def _attached_with_scores(own_seat: int = 0) -> dict[str, Any]:
    state = initial_state(_TEST_RULESET, seed=42)
    snapshot = cast(dict[str, Any], project(state, own_seat))
    # What the registry's _annotate_seat_names + _annotate_match_scores do live.
    for sv in snapshot["seats"]:
        s = sv["seat"]
        sv["name"] = _NAMES[s]
        sv["is_bot"] = s == 3
        sv["match_score"] = _CUMULATIVE[s]
    snapshot["match_scores"] = {
        "cumulative": _CUMULATIVE,
        "series": [[24, -8, -8, -8], [16, 16, -16, -16]],
        "hands_complete": 2,
    }
    return {
        "kind": "ATTACHED",
        "seq": 2,
        "table_id": 7,
        "seat": own_seat,
        "hand_index": 0,
        "snapshot": snapshot,
        "resume_buffer_size": 0,
    }


async def _attach(page: Page, server: FakeWireServer) -> None:
    await page.goto(server.url)
    await server.send(_hello())
    await server.send(_attached_with_scores())
    await expect(page.locator("game-pane").locator(".minimal-wrap, .table-ascii")).to_be_visible(
        timeout=5000
    )


async def test_header_hidden_in_lobby(page: Page, fake_wire_server: FakeWireServer) -> None:
    """Before any ATTACHED the client is in the lobby; the table header (and the
    whole table-page) must not show — the :host([hidden]) regression."""
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await expect(page.locator("table-page")).to_be_hidden(timeout=5000)


async def test_header_shows_live_round_wall_hand(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _attach(page, fake_wire_server)
    header = page.locator("table-page").locator(".table-header")
    await expect(header).to_contain_text("Table 7", timeout=5000)
    await expect(header).to_contain_text("Hand 1")
    await expect(header).to_contain_text("Round East")  # round_wind F1 → East
    await expect(header).to_contain_text("Wall")
    # The old hardcoded placeholder must be gone.
    await expect(header).not_to_contain_text("demo")


async def test_inline_match_total_in_roster(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Own seat (0) has a +16 running total; it shows in the roster, not 0."""
    await _attach(page, fake_wire_server)
    own_score = page.locator("game-pane").locator(".mv-own-head .mv-score")
    await expect(own_score).to_contain_text("+16", timeout=5000)


async def test_score_pane_renders_per_player_graphs(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _attach(page, fake_wire_server)
    await page.keyboard.press("Alt+KeyP")
    pane = page.locator("score-pane")
    await expect(pane).to_be_visible(timeout=5000)
    await expect(pane.locator(".sp-player")).to_have_count(4)  # one per seat
    await expect(pane).to_contain_text("Alice")
    # renderScoreGraph draws data points with ● — its presence proves a real
    # series rendered, not the empty-state.
    await expect(pane.locator(".sp-graph").first).to_contain_text("●")
