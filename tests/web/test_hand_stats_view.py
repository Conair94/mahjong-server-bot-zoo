"""Spec 37 hand-stats: wire→UI seam for the Alt+S detail pane.

Verification fixtures 13–14 of docs/specs/hand-stats.md (the 2026-06-12
revision: no inline strip, stats pane only, per-table opt-out). Real PROMPT
frames through `FakeWireServer` into the real `<mahjong-app>` — never pre-set
view state (the wire→UI dispatch branch is exactly what regressed before; see
the test-the-wire-to-ui-seam rule).
"""

from __future__ import annotations

import time
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


def _attached(own_seat: int = 0, *, stats_enabled: bool | None = None) -> dict[str, Any]:
    state = initial_state(_TEST_RULESET, seed=_TEST_SEED)
    snapshot = cast(dict[str, Any], project(state, own_seat))
    if stats_enabled is not None:
        snapshot["stats_enabled"] = stats_enabled
    return {
        "kind": "ATTACHED",
        "seq": 2,
        "table_id": 1,
        "seat": own_seat,
        "hand_index": 0,
        "snapshot": snapshot,
        "resume_buffer_size": 0,
    }


_DISCARD_STATS: dict[str, Any] = {
    "floor": 3,
    "wall_remaining": 42,
    "discards": [
        {
            "tile": "J3",
            "shanten": 0,
            "tiles": [
                {"tile": "B6", "remaining": 3, "fan_discard": 4, "fan_self_draw": 6},
                {"tile": "B9", "remaining": 0, "fan_discard": 2, "fan_self_draw": 4},
            ],
        },
        {
            "tile": "W1",
            "shanten": 1,
            "tiles": [
                {"tile": "B6", "remaining": 3},
                {"tile": "T5", "remaining": 2},
            ],
        },
    ],
}


def _discard_prompt(stats: dict[str, Any] | None) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "kind": "PROMPT",
        "seq": 3,
        "table_id": 1,
        "hand_index": 0,
        "seat": 0,
        "phase": "DISCARD",
        "legal_actions": [
            {"type": "PLAY", "tile": "J3"},
            {"type": "PLAY", "tile": "W1"},
        ],
        "default_action": {"type": "PLAY", "tile": "J3"},
        "deadline_ms": int(time.time() * 1000) + 30_000,
        "prompt_id": "p_0_5_DISCARD",
    }
    if stats is not None:
        frame["stats"] = stats
    return frame


async def _attach(page: Page, server: FakeWireServer, *, stats_enabled: bool | None = None) -> None:
    await page.goto(server.url)
    await server.send(_hello())
    await server.send(_attached(stats_enabled=stats_enabled))
    await expect(page.locator("game-pane").locator(".table-ascii, .minimal-wrap")).to_be_visible(
        timeout=5000
    )


# --- fixture 13: no inline strip; the Alt+S pane holds the analysis ---


async def test_discard_prompt_renders_no_inline_strip(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """The 2026-06-12 revision removed the in-board strip: a DISCARD prompt
    carrying stats shows the prompt bar but no inline analysis."""
    await _attach(page, fake_wire_server)
    await fake_wire_server.send(_discard_prompt(_DISCARD_STATS))

    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible(timeout=5000)
    await expect(page.locator("game-pane").locator(".stats-strip")).to_have_count(0)


async def test_alt_s_detail_pane_lists_every_candidate(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _attach(page, fake_wire_server)
    await fake_wire_server.send(_discard_prompt(_DISCARD_STATS))
    # Stats are pane-only now; wait on the prompt bar (proof the frame landed)
    # rather than a strip that no longer exists.
    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible(timeout=5000)

    await page.keyboard.press("Alt+KeyS")
    pane = page.locator("stats-pane")
    await expect(pane).to_be_visible(timeout=5000)
    await expect(pane.locator(".stats-meta")).to_contain_text("floor 3f")
    await expect(pane.locator(".stats-meta")).to_contain_text("wall 42")
    rows = pane.locator("table.stats-table tbody tr")
    await expect(rows).to_have_count(len(_DISCARD_STATS["discards"]))
    await expect(rows.nth(0)).to_contain_text("TENPAI")
    await expect(rows.nth(1)).to_contain_text("1-shanten")

    # Toggle off again.
    await page.keyboard.press("Alt+KeyS")
    await expect(page.locator("stats-pane")).to_have_count(0)


async def test_alt_s_pane_without_discard_prompt_shows_placeholder(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Opening the pane with no live DISCARD prompt shows the guidance text,
    not an empty table."""
    await _attach(page, fake_wire_server)
    await page.keyboard.press("Alt+KeyS")
    pane = page.locator("stats-pane")
    await expect(pane).to_be_visible(timeout=5000)
    await expect(pane.locator(".placeholder")).to_contain_text("your turn to discard")


# --- fixture 14: per-table opt-out ---


async def test_alt_s_pane_shows_disabled_message(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """A table created with stats_enabled=false (carried on the snapshot) makes
    the pane say so explicitly."""
    await _attach(page, fake_wire_server, stats_enabled=False)
    await fake_wire_server.send(_discard_prompt(None))  # disabled tables send no stats

    await page.keyboard.press("Alt+KeyS")
    pane = page.locator("stats-pane")
    await expect(pane).to_be_visible(timeout=5000)
    await expect(pane.locator(".placeholder")).to_contain_text("stats disabled")
    # And never an analysis table.
    await expect(pane.locator("table.stats-table")).to_have_count(0)
