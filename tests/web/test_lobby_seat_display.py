"""FB-05 lobby seat display (table-management.md, client).

Pins the "who's at the table" surface: occupied human seats show the display
name (not the raw user_id), and a dropped (HELD) player is marked "away" so
others know the seat isn't free.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


async def _mount_lobby(page: Page, server: FakeWireServer, tables: list[dict[str, Any]]) -> None:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """async (tables) => {
          await import('/static/app.js');
          await customElements.whenDefined('lobby-view');
          const el = document.createElement('lobby-view');
          el.id = 'lv';
          document.body.appendChild(el);
          el.tables = tables;
          await el.updateComplete;
        }""",
        tables,
    )


def _table(seats: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "table_id": 3,
        "ruleset": "mcr-2006",
        "phase": "WAITING_FOR_PLAYERS",
        "hand_index": 0,
        "seats": seats,
    }


async def test_live_human_shows_display_name(page: Page, fake_wire_server: FakeWireServer) -> None:
    tables = [
        _table(
            [
                {
                    "seat": 0,
                    "kind": "human",
                    "occupied": True,
                    "user_id": "u_7",
                    "display_name": "ConnorL",
                    "state": "LIVE",
                },
                {"seat": 1, "kind": "human", "occupied": False},
                {"seat": 2, "kind": "bot", "occupied": True, "bot_id": "v0"},
                {"seat": 3, "kind": "bot", "occupied": True, "bot_id": "v0"},
            ]
        )
    ]
    await _mount_lobby(page, fake_wire_server, tables)
    text = await page.evaluate(
        "() => document.getElementById('lv').renderRoot.querySelector('.seat-occupied').textContent.trim()"
    )
    assert "ConnorL" in text
    assert "away" not in text  # LIVE is not away


async def test_held_human_marked_away(page: Page, fake_wire_server: FakeWireServer) -> None:
    tables = [
        _table(
            [
                {
                    "seat": 0,
                    "kind": "human",
                    "occupied": True,
                    "user_id": "u_9",
                    "display_name": "Sam",
                    "state": "HELD",
                },
                {"seat": 1, "kind": "human", "occupied": False},
                {"seat": 2, "kind": "bot", "occupied": True, "bot_id": "v0"},
                {"seat": 3, "kind": "bot", "occupied": True, "bot_id": "v0"},
            ]
        )
    ]
    await _mount_lobby(page, fake_wire_server, tables)
    info = await page.evaluate(
        """() => {
          const el = document.getElementById('lv');
          const occ = el.renderRoot.querySelector('.seat-occupied');
          const away = el.renderRoot.querySelector('.seat-away');
          return { text: occ.textContent.trim(), hasAway: !!away };
        }"""
    )
    assert "Sam" in info["text"]
    assert info["hasAway"] is True


async def test_open_human_seat_still_joinable(page: Page, fake_wire_server: FakeWireServer) -> None:
    tables = [
        _table(
            [
                {"seat": 0, "kind": "human", "occupied": False},
                {"seat": 1, "kind": "human", "occupied": False},
                {"seat": 2, "kind": "bot", "occupied": True, "bot_id": "v0"},
                {"seat": 3, "kind": "bot", "occupied": True, "bot_id": "v0"},
            ]
        )
    ]
    await _mount_lobby(page, fake_wire_server, tables)
    detail = await page.evaluate(
        """async () => {
          const el = document.getElementById('lv');
          const got = new Promise((r) => el.addEventListener('lobby-join', (e) => r(e.detail), { once: true }));
          el.renderRoot.querySelector('.seat-join').click();
          return await got;
        }"""
    )
    assert detail == {"tableId": 3, "seat": 0}
