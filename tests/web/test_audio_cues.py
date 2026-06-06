"""FB-06: audio cues fire on the right frames (wire→UI) and respect mute.

The cue *selection* is pure (cueForEvent / cueForPrompt); here we drive the real
<mahjong-app> over the fake wire and assert the shared ``audioCues.lastCue`` after
an own-seat DRAW and a claim PROMPT, plus that muting suppresses it. (Actual sound
is browser-verify-owed — headless Web Audio needs a user gesture; ``play`` records
``lastCue`` before the synth, and no-ops silently when muted.)
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page, expect

from mahjong.engine.state import initial_state, project
from mahjong.engine.types import RuleSetRef

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio

_SEED = 42
_RULESET: RuleSetRef = cast(RuleSetRef, {"id": "mcr-2006", "version": 1})


def _hello() -> dict[str, Any]:
    return {"kind": "HELLO", "seq": 1, "protocol_version": 1, "server_id": "mahjong-test"}


def _attached(own_seat: int = 0) -> dict[str, Any]:
    snapshot = cast(dict[str, Any], project(initial_state(_RULESET, seed=_SEED), own_seat))
    return {
        "kind": "ATTACHED", "seq": 2, "table_id": 1, "seat": own_seat,
        "hand_index": 0, "snapshot": snapshot, "resume_buffer_size": 0,
    }


def _own_draw() -> dict[str, Any]:
    return {
        "kind": "EVENT", "seq": 3,
        "event": {"event": "DRAW", "seat": 0, "tile": "B5", "turn_index": 1, "phase": "DISCARD"},
    }


def _peng_prompt() -> dict[str, Any]:
    return {
        "kind": "PROMPT", "seq": 4, "prompt_id": "p1", "phase": "CLAIM_WINDOW",
        "deadline": 0,
        "legal_actions": [{"type": "PENG", "tile": "B5"}, {"type": "PASS"}],
        "default_action": {"type": "PASS"},
    }


async def _last_cue(page: Page) -> Any:
    return await page.evaluate("async () => (await import('/static/audio.js')).audioCues.lastCue")


async def _wait_for_cue(page: Page, cue: str) -> None:
    await page.wait_for_function(
        "cue => import('/static/audio.js').then(m => m.audioCues.lastCue === cue)",
        arg=cue,
        timeout=5000,
    )


async def _set_audio(page: Page, *, muted: bool) -> None:
    await page.evaluate(
        "async (m) => { const a = (await import('/static/audio.js')).audioCues;"
        " a.lastCue = null; a.setMuted(m); }",
        muted,
    )


async def _attach(page: Page, server: FakeWireServer) -> None:
    await page.goto(server.url)
    await server.send(_hello())
    await server.send(_attached())
    await expect(page.locator("game-pane").locator(".table-ascii")).to_be_visible(timeout=5000)


async def test_own_draw_plays_draw_cue(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _attach(page, fake_wire_server)
    await _set_audio(page, muted=False)
    await fake_wire_server.send(_own_draw())
    await _wait_for_cue(page, "draw")


async def test_claim_prompt_plays_escalated_cue(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _attach(page, fake_wire_server)
    await _set_audio(page, muted=False)
    await fake_wire_server.send(_peng_prompt())
    await _wait_for_cue(page, "peng")


async def test_mute_suppresses_cue(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _attach(page, fake_wire_server)
    await _set_audio(page, muted=True)
    await fake_wire_server.send(_own_draw())
    # Give the frame time to dispatch, then assert nothing was recorded.
    await page.wait_for_timeout(200)
    assert await _last_cue(page) is None
