"""FB-06: audio cues fire on the right frames (wire→UI) and respect mute.

The cue *selection* is pure (cueForEvent / cueForPrompt / cueForTerminal); here we
drive the real <mahjong-app> over the fake wire and assert the shared
``audioCues.lastCue`` — exercising the actual frame-dispatch seam, not pre-set
view state (the missing-dispatch class of bug is exactly what this guards). We
cover: an own-seat DRAW blip, a claim PROMPT *notification* (alert), the public
DECLARATION cues heard by every seat (claimed peng, self-kong, winning hu), the
silent exhaustive-draw terminal, and that muting suppresses everything. (Actual
sound is browser-verify-owed — headless Web Audio needs a user gesture; ``play``
records ``lastCue`` before the synth, and no-ops silently when muted.)
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


def _peng_resolution(winning_seat: int = 2) -> dict[str, Any]:
    """An opponent's claim landing — a public declaration we (seat 0) should hear."""
    return {
        "kind": "EVENT", "seq": 5,
        "event": {
            "event": "CLAIM_RESOLUTION", "outcome": "CLAIMED",
            "winning_seat": winning_seat, "winning_claim": "PENG",
            "called_tile": "B5", "turn_index": 2, "phase": "DISCARD",
        },
    }


def _self_kong_decision(seat: int = 2) -> dict[str, Any]:
    """A self-declared concealed kong — no resolution event, public gang cue."""
    return {
        "kind": "EVENT", "seq": 6,
        "event": {
            "event": "CLAIM_DECISION", "seat": seat, "decision": "GANG",
            "kind": "CONCEALED", "tile": "B5", "turn_index": 2, "phase": "DISCARD",
        },
    }


def _hand_end(*, winner: list[int]) -> dict[str, Any]:
    return {
        "kind": "HAND_END", "seq": 7, "table_id": 1, "hand_index": 0,
        "terminal": {
            "kind": "HU" if winner else "DRAW",
            "winner": winner,
            "win_tile": "B5" if winner else None,
            "win_type": "SELF_DRAW" if winner else None,
            "fan": [], "fan_total": 8 if winner else 0,
            "score_delta": [10, -3, -3, -4] if winner else [0, 0, 0, 0],
            "final_hands": None,
        },
    }


async def _last_cue(page: Page) -> Any:
    # Reads the SAME shared instance app.js plays through (stashed on window by
    # `_attach`). Synchronous so the value is the real cue, not a pending promise.
    return await page.evaluate("() => window.__cues.lastCue")


async def _wait_for_cue(page: Page, cue: str) -> None:
    # The predicate must be SYNCHRONOUS: wait_for_function treats a returned
    # Promise (e.g. from `import().then(...)`) as truthy and resolves on the
    # first poll without ever inspecting the cue — silently vacuous. We stash the
    # module on `window.__cues` up front so the poll is a plain value comparison.
    await page.wait_for_function(
        "cue => window.__cues && window.__cues.lastCue === cue",
        arg=cue,
        timeout=5000,
    )


async def _set_audio(page: Page, *, muted: bool) -> None:
    await page.evaluate(
        "(m) => { window.__cues.lastCue = null; window.__cues.setMuted(m); }",
        muted,
    )


async def _attach(page: Page, server: FakeWireServer) -> None:
    await page.goto(server.url)
    # Stash the shared AudioCues singleton synchronously-readable on window so the
    # poll/assert helpers don't depend on an awaited dynamic import (see above).
    await page.evaluate(
        "async () => { window.__cues = (await import('/static/audio.js')).audioCues; }"
    )
    await server.send(_hello())
    await server.send(_attached())
    await expect(page.locator("game-pane").locator(".table-ascii, .minimal-wrap")).to_be_visible(timeout=5000)


async def test_own_draw_plays_draw_cue(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _attach(page, fake_wire_server)
    await _set_audio(page, muted=False)
    await fake_wire_server.send(_own_draw())
    await _wait_for_cue(page, "draw")


async def test_claim_prompt_plays_alert_cue(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    # Your own claim window → the "call or pass" notification, not a call sound.
    await _attach(page, fake_wire_server)
    await _set_audio(page, muted=False)
    await fake_wire_server.send(_peng_prompt())
    await _wait_for_cue(page, "alert")


async def test_claimed_peng_plays_public_declaration(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    # An OPPONENT pengs (winning_seat=2); seat 0 still hears the public cue.
    await _attach(page, fake_wire_server)
    await _set_audio(page, muted=False)
    await fake_wire_server.send(_peng_resolution(winning_seat=2))
    await _wait_for_cue(page, "peng")


async def test_self_kong_plays_gang(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    # A concealed kong has no resolution event — the gang cue rides the decision.
    await _attach(page, fake_wire_server)
    await _set_audio(page, muted=False)
    await fake_wire_server.send(_self_kong_decision(seat=2))
    await _wait_for_cue(page, "gang")


async def test_winning_hand_end_plays_hu(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _attach(page, fake_wire_server)
    await _set_audio(page, muted=False)
    await fake_wire_server.send(_hand_end(winner=[0]))
    await _wait_for_cue(page, "hu")


async def test_drawn_hand_end_is_silent(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _attach(page, fake_wire_server)
    await _set_audio(page, muted=False)
    await fake_wire_server.send(_hand_end(winner=[]))
    await page.wait_for_timeout(200)
    assert await _last_cue(page) is None


async def test_mute_suppresses_cue(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _attach(page, fake_wire_server)
    await _set_audio(page, muted=True)
    await fake_wire_server.send(_own_draw())
    # Give the frame time to dispatch, then assert nothing was recorded.
    await page.wait_for_timeout(200)
    assert await _last_cue(page) is None


# --- Iconic-motif contract (user feedback 2026-06-11) -----------------------
#
# "Instantly recognizable" is a structural property: each declaration must be
# distinguishable WITHOUT hearing the others. Pinned here as contour/register/
# rhythm/timbre assertions over the exported VOICES table — headless CI can't
# listen, but it can verify the shapes that make the sounds tellable-apart.


async def _voices(page: Page, fake_wire_server: FakeWireServer) -> dict:
    await page.goto(fake_wire_server.url)
    return await page.evaluate("async () => (await import('/static/audio.js')).VOICES")


async def test_chi_is_a_fast_ascending_run(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    voices = await _voices(page, fake_wire_server)
    notes = voices["chi"]["notes"]
    pitches = [n["f"] for n in notes]
    assert len(notes) == 3
    assert pitches == sorted(pitches) and len(set(pitches)) == 3, "chi must rise"
    assert max(n["t"] + n["d"] for n in notes) < 0.35, "chi is a quick flick"


async def test_peng_is_three_identical_knocks(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    voices = await _voices(page, fake_wire_server)
    v = voices["peng"]
    pitches = {n["f"] for n in v["notes"]}
    assert len(v["notes"]) == 3
    assert len(pitches) == 1, "peng knocks on ONE pitch (opposite contour of chi)"
    assert v["wave"] == "square", "peng is percussive"
    starts = sorted(n["t"] for n in v["notes"])
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g > 0.05 for g in gaps), "knocks are separated, not a chord"


async def test_gang_is_four_heavy_low_hits_with_a_slam(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    voices = await _voices(page, fake_wire_server)
    gang = voices["gang"]["notes"]
    peng_pitch = voices["peng"]["notes"][0]["f"]
    base = [n for n in gang if n["f"] == gang[0]["f"]]
    assert len(base) >= 4, "gang hits four times (four of a kind)"
    assert gang[0]["f"] <= peng_pitch / 2, "gang lives at least an octave below peng"
    assert any(n.get("p", 1) > 1 for n in gang), "the final hit is slammed (accent)"
    assert voices["gang"]["wave"] == "sawtooth", "gang is the heaviest timbre"


async def test_hu_is_the_biggest_flourish(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    voices = await _voices(page, fake_wire_server)
    hu = voices["hu"]["notes"]
    for other in ("chi", "peng", "gang"):
        assert len(hu) > len(voices[other]["notes"]), f"hu outsizes {other}"
    # The finale holds a chord: at least three notes sharing one start time.
    from collections import Counter

    start_counts = Counter(n["t"] for n in hu)
    assert max(start_counts.values()) >= 3, "hu resolves into a held triad"
