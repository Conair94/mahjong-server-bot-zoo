"""FB-03 rejoin — lobby rendering of seat holds (reconnect-rejoin.md, client).

Pins the lobby surface of FB-03: when the account holds seats (from
AUTH_RESPONSE.seat_holds[]), <lobby-view> renders "▶ Rejoin" / "▶ Take over"
rows and clicking one emits a `lobby-rejoin` intent carrying {tableId, seat}.
The server-side seat_holds discovery is covered by tests/server/test_seat_holds.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


async def _mount_lobby(page: Page, server: FakeWireServer, state: dict[str, Any]) -> None:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """async (state) => {
          await import('/static/app.js');
          await customElements.whenDefined('lobby-view');
          const el = document.createElement('lobby-view');
          el.id = 'lv';
          document.body.appendChild(el);
          Object.assign(el, state);
          await el.updateComplete;
        }""",
        state,
    )


async def test_no_holds_renders_no_rejoin_block(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _mount_lobby(page, fake_wire_server, {"seatHolds": []})
    has_block = await page.evaluate(
        "() => !!document.getElementById('lv').renderRoot.querySelector('.rejoin-block')"
    )
    assert has_block is False


async def test_held_seat_renders_rejoin_row(page: Page, fake_wire_server: FakeWireServer) -> None:
    holds = [{"table_id": 3, "seat": 1, "state": "HELD", "hand_index": 4}]
    await _mount_lobby(page, fake_wire_server, {"seatHolds": holds})
    info = await page.evaluate(
        """() => {
          const el = document.getElementById('lv');
          const rows = el.renderRoot.querySelectorAll('.rejoin-row');
          const btn = el.renderRoot.querySelector('.rejoin-btn');
          return { rows: rows.length, label: btn ? btn.textContent.trim() : null };
        }"""
    )
    assert info["rows"] == 1
    assert "Rejoin" in info["label"]


async def test_live_seat_renders_take_over(page: Page, fake_wire_server: FakeWireServer) -> None:
    holds = [{"table_id": 5, "seat": 0, "state": "LIVE", "hand_index": 0}]
    await _mount_lobby(page, fake_wire_server, {"seatHolds": holds})
    label = await page.evaluate(
        "() => document.getElementById('lv').renderRoot.querySelector('.rejoin-btn').textContent.trim()"
    )
    assert "Take over" in label


async def test_clicking_rejoin_emits_intent(page: Page, fake_wire_server: FakeWireServer) -> None:
    holds = [{"table_id": 3, "seat": 2, "state": "HELD", "hand_index": 1}]
    await _mount_lobby(page, fake_wire_server, {"seatHolds": holds})
    detail = await page.evaluate(
        """async () => {
          const el = document.getElementById('lv');
          const got = new Promise((resolve) => {
            el.addEventListener('lobby-rejoin', (e) => resolve(e.detail), { once: true });
          });
          el.renderRoot.querySelector('.rejoin-btn').click();
          return await got;
        }"""
    )
    assert detail == {"tableId": 3, "seat": 2}
