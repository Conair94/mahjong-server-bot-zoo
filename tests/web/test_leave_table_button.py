"""FB-14 — the in-game "leave table" button (wire→UI, over the fake wire).

A player in a table — including a *hung* one (FB-13) — must be able to get
back to the main menu.  The header shows a "[ ⌂ menu ]" button in table view;
the first click arms it ("[ leave? ]", two-step confirm against stray clicks),
the second click sends ``DETACH {reason: "leaving"}`` and switches to the
lobby *optimistically* — the client must not wait for a server ack, because
the whole point is escaping a server that may never answer.
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


async def _wait_for_attached(page: Page) -> None:
    await expect(page.locator("game-pane").locator(".table-ascii, .minimal-wrap")).to_be_visible(
        timeout=5000
    )


async def test_leave_button_absent_in_lobby(page: Page, fake_wire_server: FakeWireServer) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await expect(page.locator("lobby-view")).to_be_visible(timeout=5000)
    await expect(page.locator("button.leave-btn")).to_have_count(0)


async def test_leave_button_two_step_returns_to_lobby(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)

    leave_btn = page.locator("button.leave-btn")
    await expect(leave_btn).to_be_visible(timeout=5000)
    await expect(leave_btn).to_contain_text("menu")

    # First click only arms the confirm — still in the table, nothing sent.
    await leave_btn.click()
    await expect(leave_btn).to_contain_text("leave?")
    await expect(page.locator("game-pane").locator(".table-ascii, .minimal-wrap")).to_be_visible()

    # Second click leaves: a DETACH frame goes out and the lobby renders
    # without any server ack (optimistic — the hung-table escape).
    await leave_btn.click()
    detach = await fake_wire_server.wait_for_inbound(lambda m: m.get("kind") == "DETACH")
    assert detach.get("reason") == "leaving"

    await expect(page.locator("lobby-view")).to_be_visible(timeout=5000)
    await expect(page.locator("button.leave-btn")).to_have_count(0)
