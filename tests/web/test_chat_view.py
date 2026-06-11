"""Spec 38 table chat: wire→UI seam (fixture 4 of docs/specs/table-chat.md).

Real frames through `FakeWireServer` into the real `<mahjong-app>` — the
send path is asserted on the server's captured inbound frames, the render
path on injected CHAT_MESSAGE frames, and the FB-16 keydown guard on the
absence of ACTION frames while typing game-letter keys into the input.
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

_TEST_RULESET: RuleSetRef = cast(RuleSetRef, {"id": "mcr-2006", "version": 1})


def _hello() -> dict[str, Any]:
    return {"kind": "HELLO", "seq": 1, "protocol_version": 1, "server_id": "mahjong-test"}


def _attached(own_seat: int = 0) -> dict[str, Any]:
    state = initial_state(_TEST_RULESET, seed=42)
    snapshot = cast(dict[str, Any], project(state, own_seat))
    # Spec 34 seat-name annotation (the registry does this on live tables).
    snapshot["seats"][1]["name"] = "Bobby"
    return {
        "kind": "ATTACHED",
        "seq": 2,
        "table_id": 1,
        "seat": own_seat,
        "hand_index": 0,
        "snapshot": snapshot,
        "resume_buffer_size": 0,
    }


def _chat_message(seat: int, text: str) -> dict[str, Any]:
    return {
        "kind": "CHAT_MESSAGE",
        "seq": 10,
        "table_id": 1,
        "hand_index": 0,
        "seat": seat,
        "ts": "2026-06-11T22:10:00.000Z",
        "text": text,
    }


def _discard_prompt() -> dict[str, Any]:
    return {
        "kind": "PROMPT",
        "seq": 3,
        "table_id": 1,
        "hand_index": 0,
        "seat": 0,
        "phase": "DISCARD",
        "legal_actions": [{"type": "PLAY", "tile": "W3"}],
        "default_action": {"type": "PLAY", "tile": "W3"},
        "deadline_ms": int(time.time() * 1000) + 30_000,
        "prompt_id": "p_0_5_DISCARD",
    }


async def _attach(page: Page, server: FakeWireServer) -> None:
    await page.goto(server.url)
    await server.send(_hello())
    await server.send(_attached())
    await expect(
        page.locator("game-pane").locator(".table-ascii, .minimal-wrap")
    ).to_be_visible(timeout=5000)


async def test_chat_pane_send_emits_chat_frame(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _attach(page, fake_wire_server)

    await page.keyboard.press("Alt+KeyC")
    pane = page.locator("chat-pane")
    await expect(pane).to_be_visible(timeout=5000)

    box = pane.locator("input")
    await box.click()
    await box.fill("nice kong")
    await box.press("Enter")

    frame = await fake_wire_server.wait_for_inbound(lambda m: m.get("kind") == "CHAT")
    assert frame["text"] == "nice kong"
    # Input cleared after send.
    await expect(box).to_have_value("")


async def test_chat_message_renders_with_seat_label(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _attach(page, fake_wire_server)
    await page.keyboard.press("Alt+KeyC")
    await expect(page.locator("chat-pane")).to_be_visible(timeout=5000)

    await fake_wire_server.send(_chat_message(seat=1, text="hello table"))

    line = page.locator("chat-pane").locator(".chat-line")
    await expect(line).to_have_count(1, timeout=5000)
    await expect(line).to_contain_text("Bobby:")  # annotated name, not Seat 1
    await expect(line).to_contain_text("hello table")


async def test_chat_unread_indicator_when_pane_closed(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _attach(page, fake_wire_server)

    await fake_wire_server.send(_chat_message(seat=1, text="psst"))
    indicator = page.locator("table-page").locator(".panes-indicator .unread")
    await expect(indicator).to_have_count(1, timeout=5000)

    # Opening the pane clears the badge and shows the backlog.
    await page.keyboard.press("Alt+KeyC")
    await expect(page.locator("table-page").locator(".panes-indicator .unread")).to_have_count(0)
    await expect(page.locator("chat-pane").locator(".chat-line")).to_contain_text("psst")


async def test_typing_game_keys_in_chat_fires_no_action(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """FB-16 regression at the chat input: with a live DISCARD prompt,
    typing letters/Space/Enter in the chat box must not submit actions."""
    await _attach(page, fake_wire_server)
    await fake_wire_server.send(_discard_prompt())
    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible(timeout=5000)

    await page.keyboard.press("Alt+KeyC")
    box = page.locator("chat-pane").locator("input")
    await box.click()
    await box.type("h pass 1")  # H, Space, digits — all game-mapped keys
    await box.press("Enter")  # sends chat, must not PLAY

    await fake_wire_server.wait_for_inbound(lambda m: m.get("kind") == "CHAT")
    actions = [m for m in fake_wire_server.inbound if m.get("kind") == "ACTION"]
    assert not actions, f"chat typing leaked game ACTIONs: {actions}"
