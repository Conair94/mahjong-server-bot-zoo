"""Web-client tests for Step 7.5c.iii — PROMPT bar, ACTION round-trip,
illegal-action banner.

Backs `docs/specs/tui-client.md` verification fixtures 7, 8, 9.

These tests boot a real browser via the Playwright async API against a
`FakeWireServer` (scripted WS) serving the bundled static assets. The
browser exercises the real `<mahjong-app>` JS; assertions hit rendered
DOM and the server's captured inbound frames.

Async Playwright (rather than `pytest-playwright`'s sync fixtures) is used
on purpose: the sync API installs a separate asyncio loop that conflicts
with `pytest-asyncio` in the rest of the suite. Async API + pytest-asyncio
share the same loop and play nicely.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import pytest
from playwright.async_api import Page, expect

from mahjong.engine.state import initial_state, project
from mahjong.engine.types import RuleSetRef

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio

# --- shared fixtures ---

_TEST_SEED = 42
_TEST_RULESET: RuleSetRef = cast(RuleSetRef, {"id": "mcr-2006", "version": 1})


def _hello() -> dict[str, Any]:
    return {
        "kind": "HELLO",
        "seq": 1,
        "protocol_version": 1,
        "server_id": "mahjong-test",
    }


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


def _prompt_three_actions(prompt_id: str = "p_0_5_CLAIM_WINDOW") -> dict[str, Any]:
    """A CLAIM_WINDOW prompt with three legal actions: PASS, PENG, CHI."""
    return {
        "kind": "PROMPT",
        "seq": 3,
        "table_id": 1,
        "hand_index": 0,
        "seat": 0,
        "phase": "CLAIM_WINDOW",
        "legal_actions": [
            {"type": "PASS"},
            {"type": "PENG", "tile": "W5"},
            {"type": "CHI", "tiles": ["W3", "W4", "W5"]},
        ],
        "default_action": {"type": "PASS"},
        "deadline_ms": int(time.time() * 1000) + 30_000,
        "prompt_id": prompt_id,
    }


def _prompt_multi_chi(prompt_id: str = "p_0_5_CLAIM_WINDOW") -> dict[str, Any]:
    """A CLAIM_WINDOW where the discard admits three distinct chi runs — the
    server emits one CHI action per sequence (e.g. discard B4)."""
    return {
        "kind": "PROMPT",
        "seq": 3,
        "table_id": 1,
        "hand_index": 0,
        "seat": 0,
        "phase": "CLAIM_WINDOW",
        "legal_actions": [
            {"type": "PASS"},
            {"type": "CHI", "tiles": ["B2", "B3", "B4"]},
            {"type": "CHI", "tiles": ["B3", "B4", "B5"]},
            {"type": "CHI", "tiles": ["B4", "B5", "B6"]},
        ],
        "default_action": {"type": "PASS"},
        "deadline_ms": int(time.time() * 1000) + 30_000,
        "prompt_id": prompt_id,
    }


async def _wait_for_attached(page: Page) -> None:
    await expect(page.locator("game-pane").locator(".table-ascii, .minimal-wrap")).to_be_visible(
        timeout=5000
    )


# --- fixture 7: PROMPT renders legal action bar ---


async def test_prompt_renders_legal_action_bar(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    await fake_wire_server.send(_prompt_three_actions())

    bar = page.locator("game-pane").locator(".prompt-bar")
    await expect(bar).to_be_visible(timeout=5000)

    text = await bar.inner_text()
    # The bar lists exactly the three legal actions and their bindings. Per
    # the locked key map: Space→PASS, P→PENG, C→CHI.
    assert "Pass" in text, text
    assert "Space" in text, text
    assert "Peng" in text, text
    assert "[P]" in text, text
    assert "Chi" in text, text
    assert "[C]" in text, text
    # Actions not in legal_actions must not appear in the bar.
    assert "Gang" not in text, text
    assert "Hu" not in text, text


# --- fixture 8: keystroke → ACTION round-trip ---


async def test_keypress_sends_action_with_prompt_id(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)

    prompt_id = "p_0_5_CLAIM_WINDOW"
    await fake_wire_server.send(_prompt_three_actions(prompt_id=prompt_id))
    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible(timeout=5000)

    # Press P → PENG W5 (the unique PENG in legal_actions).
    await page.keyboard.press("p")

    action_msg = await fake_wire_server.wait_for_inbound(lambda m: m.get("kind") == "ACTION")
    assert action_msg["prompt_id"] == prompt_id, action_msg
    assert action_msg["action"] == {"type": "PENG", "tile": "W5"}, action_msg


async def test_space_key_sends_pass_action(page: Page, fake_wire_server: FakeWireServer) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    prompt_id = "p_0_5_CLAIM_WINDOW"
    await fake_wire_server.send(_prompt_three_actions(prompt_id=prompt_id))
    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible(timeout=5000)

    await page.keyboard.press("Space")

    action_msg = await fake_wire_server.wait_for_inbound(lambda m: m.get("kind") == "ACTION")
    assert action_msg["prompt_id"] == prompt_id
    assert action_msg["action"] == {"type": "PASS"}


async def test_typing_in_text_field_does_not_fire_action(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Keystrokes typed into a text input must not be hijacked as game actions.

    Player report (025210): while typing a bug report mid-hand, Space/H/Enter
    were swallowed by the game-pane shortcut handler — Space passed, H toggled
    HU, Enter discarded a tile. The bug-report textarea lives in a shadow root,
    so at window level the keydown target is the *host* element, not the
    textarea; the guard must inspect composedPath()[0], not e.target.
    """
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    await fake_wire_server.send(_prompt_three_actions())
    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible(timeout=5000)

    # A textarea inside a shadow root mirrors the real <feedback-button> form.
    await page.evaluate(
        """() => {
          const host = document.createElement('div');
          const root = host.attachShadow({ mode: 'open' });
          const ta = document.createElement('textarea');
          root.appendChild(ta);
          document.body.appendChild(host);
          ta.focus();
        }"""
    )

    # Space would PASS, Enter would discard — both must be inert while typing.
    await page.keyboard.press("Space")
    await page.keyboard.press("Enter")

    await asyncio.sleep(0.3)
    actions = [m for m in fake_wire_server.inbound if m.get("kind") == "ACTION"]
    assert actions == [], actions
    # Prompt stays open — nothing was submitted on the player's behalf.
    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible()


async def test_alt_chord_does_not_fire_action(page: Page, fake_wire_server: FakeWireServer) -> None:
    """Alt+C is the chat-pane toggle; bare C is Chi. Alt+C must NOT send Chi."""
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    await fake_wire_server.send(_prompt_three_actions())
    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible(timeout=5000)

    await page.keyboard.press("Alt+c")

    # No ACTION should arrive on the wire.
    await asyncio.sleep(0.3)
    actions = [m for m in fake_wire_server.inbound if m.get("kind") == "ACTION"]
    assert actions == [], actions
    # The prompt stays open (pane toggle did fire, but the bar is in game-pane).
    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible()


# --- staged CHI selection (multiple sequences on one discard) ---


async def test_single_chi_press_c_sends_immediately(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """With exactly one CHI option, C submits it directly — no chooser step."""
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    await fake_wire_server.send(_prompt_three_actions())
    await expect(page.locator("game-pane").locator(".prompt-bar")).to_be_visible(timeout=5000)

    await page.keyboard.press("c")

    action_msg = await fake_wire_server.wait_for_inbound(lambda m: m.get("kind") == "ACTION")
    assert action_msg["action"] == {"type": "CHI", "tiles": ["W3", "W4", "W5"]}, action_msg


async def test_multi_chi_staged_pick_sends_chosen_sequence(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """With 2+ CHI options, C opens a numbered chooser and a digit picks which
    sequence — the second option here, not the first (the pre-fix behaviour)."""
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    prompt_id = "p_0_5_CLAIM_WINDOW"
    await fake_wire_server.send(_prompt_multi_chi(prompt_id=prompt_id))
    bar = page.locator("game-pane").locator(".prompt-bar")
    await expect(bar).to_be_visible(timeout=5000)

    # C enters the chooser — no ACTION yet, the bar switches to "Which chi?".
    await page.keyboard.press("c")
    await expect(bar).to_contain_text("Which chi?")
    await asyncio.sleep(0.2)
    assert [m for m in fake_wire_server.inbound if m.get("kind") == "ACTION"] == []

    # Digit 2 → the second sequence.
    await page.keyboard.press("2")
    action_msg = await fake_wire_server.wait_for_inbound(lambda m: m.get("kind") == "ACTION")
    assert action_msg["prompt_id"] == prompt_id, action_msg
    assert action_msg["action"] == {"type": "CHI", "tiles": ["B3", "B4", "B5"]}, action_msg


async def test_multi_chi_escape_cancels_chooser(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Esc backs out of the chooser without sending, and other keys still work."""
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    await fake_wire_server.send(_prompt_multi_chi())
    bar = page.locator("game-pane").locator(".prompt-bar")
    await expect(bar).to_be_visible(timeout=5000)

    await page.keyboard.press("c")
    await expect(bar).to_contain_text("Which chi?")
    await page.keyboard.press("Escape")
    await expect(bar).not_to_contain_text("Which chi?")

    # Space still passes after cancelling.
    await page.keyboard.press("Space")
    action_msg = await fake_wire_server.wait_for_inbound(lambda m: m.get("kind") == "ACTION")
    assert action_msg["action"] == {"type": "PASS"}, action_msg


# --- fixture 9: illegal-action banner ---


async def test_illegal_action_shows_banner_without_closing_prompt(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    await fake_wire_server.send(_prompt_three_actions())
    bar = page.locator("game-pane").locator(".prompt-bar")
    await expect(bar).to_be_visible(timeout=5000)

    await page.keyboard.press("p")
    await fake_wire_server.wait_for_inbound(lambda m: m.get("kind") == "ACTION")

    # Server rejects with illegal_action.
    await fake_wire_server.send(
        {
            "kind": "ERROR",
            "seq": 99,
            "code": "illegal_action",
            "message": "That peng is not legal right now.",
        }
    )

    banner = page.locator("game-pane").locator(".illegal-banner")
    await expect(banner).to_be_visible(timeout=5000)
    await expect(banner).to_contain_text("not legal")

    # The prompt bar is still rendered (player can re-submit).
    await expect(bar).to_be_visible()


# --- §22.2: claim-available alert -----------------------------------------


def _prompt_discard(prompt_id: str = "p_0_5_DISCARD") -> dict[str, Any]:
    """A DISCARD (own-turn) prompt — not a claim, so no alert."""
    return {
        "kind": "PROMPT",
        "seq": 4,
        "table_id": 1,
        "hand_index": 0,
        "seat": 0,
        "phase": "DISCARD",
        "legal_actions": [{"type": "PLAY", "tile": "W5"}],
        "default_action": {"type": "PLAY", "tile": "W5"},
        "deadline_ms": int(time.time() * 1000) + 60_000,
        "prompt_id": prompt_id,
    }


def _prompt_pass_only(prompt_id: str = "p_0_5_CLAIM_PASS") -> dict[str, Any]:
    """A CLAIM_WINDOW prompt with PASS as the only option — no real choice,
    so the alert must NOT fire."""
    return {
        "kind": "PROMPT",
        "seq": 5,
        "table_id": 1,
        "hand_index": 0,
        "seat": 0,
        "phase": "CLAIM_WINDOW",
        "legal_actions": [{"type": "PASS"}],
        "default_action": {"type": "PASS"},
        "deadline_ms": int(time.time() * 1000) + 20_000,
        "prompt_id": prompt_id,
    }


async def test_claim_prompt_triggers_alert(page: Page, fake_wire_server: FakeWireServer) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    await fake_wire_server.send(_prompt_three_actions())

    gp = page.locator("game-pane")
    await expect(gp.locator(".prompt-bar.claim-active")).to_be_visible(timeout=5000)
    await expect(gp.locator(".claim-chip")).to_be_visible()


async def test_discard_prompt_has_no_alert(page: Page, fake_wire_server: FakeWireServer) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    await fake_wire_server.send(_prompt_discard())

    gp = page.locator("game-pane")
    await expect(gp.locator(".prompt-bar")).to_be_visible(timeout=5000)
    await expect(gp.locator(".prompt-bar.claim-active")).to_have_count(0)
    await expect(gp.locator(".claim-chip")).to_have_count(0)


async def test_pass_only_claim_has_no_alert(page: Page, fake_wire_server: FakeWireServer) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    await fake_wire_server.send(_prompt_pass_only())

    gp = page.locator("game-pane")
    await expect(gp.locator(".prompt-bar")).to_be_visible(timeout=5000)
    await expect(gp.locator(".prompt-bar.claim-active")).to_have_count(0)
    await expect(gp.locator(".claim-chip")).to_have_count(0)


async def test_alert_clears_on_prompt_change(page: Page, fake_wire_server: FakeWireServer) -> None:
    """Alert is visible for a claim prompt, then a follow-up DISCARD prompt
    replaces it and both cues disappear."""
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await fake_wire_server.send(_attached())
    await _wait_for_attached(page)
    await fake_wire_server.send(_prompt_three_actions())

    gp = page.locator("game-pane")
    await expect(gp.locator(".claim-chip")).to_be_visible(timeout=5000)

    await fake_wire_server.send(_prompt_discard())
    await expect(gp.locator(".claim-chip")).to_have_count(0)
    await expect(gp.locator(".prompt-bar.claim-active")).to_have_count(0)
