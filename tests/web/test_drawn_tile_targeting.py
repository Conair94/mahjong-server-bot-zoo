"""FB-18 — drawn-tile discard targeting.

Two client input defects, both observable with the seed-42 dealer hand
(concealed sorted: W1 W2 W7 W8 W9 B6 B9 T2 [T3] T4 T5 T5 T6 T6, where T3 is
`last_drawn` at raw index 8; the display pulls it out to the end):

1. Enter with no explicit selection claimed to discard "the just-drawn tile"
   but actually targeted `concealed[length-1]` — the highest-*sorting* tile
   (T6 here), because the engine re-sorts the hand after every draw. The
   authoritative slot is `view.last_drawn.tile` (the 8.7.e lesson).
2. Tile keys (1..9 0 - = [ ]) indexed the raw sorted array while the renderer
   shows a *reordered* hand (drawn tile pulled out to the end) — every tile
   displayed after the drawn tile's sorted slot was off by one from its key,
   and the visually-last tile (the draw) was not selected by the last key.

These drive the real `<mahjong-app>` through Playwright against a scripted
wire server; assertions are on the captured outbound ACTION frames.
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

# Seed-42 dealer facts the assertions below rely on (pinned so a silent
# fixture drift fails loudly here rather than in a confusing assertion).
_DRAWN_TILE = "T3"  # last_drawn, raw index 8
_SORTED_LAST = "T6"  # concealed[-1] — the OLD (buggy) Enter-fallback target
_DISPLAY_NINTH = "T4"  # 9th *displayed* tile once T3 is pulled out to the end


def _hello() -> dict[str, Any]:
    return {
        "kind": "HELLO",
        "seq": 1,
        "protocol_version": 1,
        "server_id": "mahjong-test",
    }


def _attached(own_seat: int = 0) -> dict[str, Any]:
    state = initial_state(_TEST_RULESET, seed=_TEST_SEED)
    concealed = state["seats"][0]["concealed"]
    assert state["last_drawn"] == {"seat": 0, "tile": _DRAWN_TILE}
    assert concealed[-1] == _SORTED_LAST
    display = [t for i, t in enumerate(concealed) if i != concealed.index(_DRAWN_TILE)]
    assert display[8] == _DISPLAY_NINTH
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


def _discard_prompt(prompt_id: str = "p_0_0_DISCARD") -> dict[str, Any]:
    """A DISCARD prompt with one legal PLAY per distinct tile (server shape)."""
    state = initial_state(_TEST_RULESET, seed=_TEST_SEED)
    distinct = list(dict.fromkeys(state["seats"][0]["concealed"]))
    return {
        "kind": "PROMPT",
        "seq": 3,
        "table_id": 1,
        "hand_index": 0,
        "seat": 0,
        "phase": "DISCARD",
        "legal_actions": [{"type": "PLAY", "tile": t} for t in distinct],
        "default_action": {"type": "PLAY", "tile": _DRAWN_TILE},
        "prompt_id": prompt_id,
        "deadline_ms": 0,
    }


async def _wait_for_attached(page: Page) -> None:
    await expect(
        page.locator("game-pane").locator(".table-ascii, .minimal-wrap")
    ).to_be_visible(timeout=5000)


async def _boot_to_discard_prompt(page: Page, server: FakeWireServer) -> None:
    await page.goto(server.url)
    await server.send(_hello())
    await server.send(_attached())
    await _wait_for_attached(page)
    await server.send(_discard_prompt())
    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible(
        timeout=5000
    )


async def test_enter_with_no_selection_discards_the_just_drawn_tile(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Bare Enter = tsumogiri: PLAY `last_drawn.tile`, not `concealed[-1]`."""
    await _boot_to_discard_prompt(page, fake_wire_server)

    await page.keyboard.press("Enter")

    action_msg = await fake_wire_server.wait_for_inbound(
        lambda m: m.get("kind") == "ACTION"
    )
    assert action_msg["action"] == {"type": "PLAY", "tile": _DRAWN_TILE}, action_msg


async def test_last_tile_key_targets_the_visually_last_drawn_tile(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """']' selects the 14th *displayed* tile — the pulled-out drawn tile."""
    await _boot_to_discard_prompt(page, fake_wire_server)

    await page.keyboard.press("]")
    await page.keyboard.press("Enter")

    action_msg = await fake_wire_server.wait_for_inbound(
        lambda m: m.get("kind") == "ACTION"
    )
    assert action_msg["action"] == {"type": "PLAY", "tile": _DRAWN_TILE}, action_msg


async def test_digit_key_targets_the_displayed_position(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """'9' selects the 9th *displayed* tile (T4) — not raw index 8 (the drawn
    T3, which the renderer moved to the end)."""
    await _boot_to_discard_prompt(page, fake_wire_server)

    await page.keyboard.press("9")
    await page.keyboard.press("Enter")

    action_msg = await fake_wire_server.wait_for_inbound(
        lambda m: m.get("kind") == "ACTION"
    )
    assert action_msg["action"] == {"type": "PLAY", "tile": _DISPLAY_NINTH}, action_msg
