"""FB-04 replay viewer — <replay-view> (account-records-replay.md, client).

Pins the replay dispatch + transport: a REPLAY-shaped object folds through the
*live* reducer (applyEvent) at the user's pace. Board correctness is the live
renderer's job (tested elsewhere); this pins that stepping advances the cursor,
folds events, clamps at the ends, and that the viewer is read-only (no prompt).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page

from mahjong.engine.state import initial_state, project
from mahjong.engine.types import RuleSetRef

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio

_RULESET: RuleSetRef = cast(RuleSetRef, {"id": "mcr-2006", "version": 1})


def _replay_frame(seat: int = 0) -> dict[str, Any]:
    snapshot = cast(dict[str, Any], project(initial_state(_RULESET, seed=42), seat))
    events = [
        {"event": "DRAW", "seat": seat, "tile": "B5", "turn_index": 1,
         "phase": "DISCARD", "ts": "t"},
        {"event": "DISCARD", "seat": seat, "tile": "B5", "from_hand": False,
         "turn_index": 1, "phase": "DISCARD", "ts": "t"},
    ]
    return {
        "kind": "REPLAY", "seq": 5, "hand_id": "h1", "seat": seat,
        "snapshot": snapshot, "events": events,
        "meta": {"ruleset_id": "mcr-2006", "winner_seat": None, "fan_total": None},
    }


async def _mount(page: Page, server: FakeWireServer, frame: dict[str, Any]) -> None:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """async (frame) => {
          await import('/static/app.js');
          await customElements.whenDefined('replay-view');
          const el = document.createElement('replay-view');
          el.id = 'rv';
          document.body.appendChild(el);
          el.replay = frame;
          await el.updateComplete;
        }""",
        frame,
    )


async def test_mounts_at_start_with_scrubber(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _mount(page, fake_wire_server, _replay_frame())
    info = await page.evaluate(
        """() => {
          const el = document.getElementById('rv');
          const scrub = el.renderRoot.querySelector('.scrub');
          return { cursor: el.cursor, max: scrub.max, pos: el.renderRoot.querySelector('.pos').textContent.trim() };
        }"""
    )
    assert info["cursor"] == 0
    assert info["max"] == "2"
    assert info["pos"] == "0 / 2"


async def test_step_advances_and_clamps(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _mount(page, fake_wire_server, _replay_frame())
    cursors = await page.evaluate(
        """async () => {
          const el = document.getElementById('rv');
          const seen = [];
          el._step(1); seen.push(el.cursor);
          el._step(1); seen.push(el.cursor);
          el._step(1); seen.push(el.cursor);   // clamp at 2
          el._step(-1); seen.push(el.cursor);
          await el.updateComplete;
          return seen;
        }"""
    )
    assert cursors == [1, 2, 2, 1]


async def test_folding_changes_the_view(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _mount(page, fake_wire_server, _replay_frame())
    differs = await page.evaluate(
        """() => {
          const el = document.getElementById('rv');
          el.cursor = 0;
          const v0 = JSON.stringify(el._currentView());
          el.cursor = 2;
          const v2 = JSON.stringify(el._currentView());
          return v0 !== v2;   // folding the DRAW+DISCARD must change the board
        }"""
    )
    assert differs is True


async def test_read_only_no_prompt_bar(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _mount(page, fake_wire_server, _replay_frame())
    has_prompt = await page.evaluate(
        """() => {
          const el = document.getElementById('rv');
          // No prompt/action affordances in a replay — only transport controls.
          return !!el.renderRoot.querySelector('.prompt-bar, [data-action]');
        }"""
    )
    assert has_prompt is False
