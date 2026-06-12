"""Hand-end scoring summary (§22.9).

Renderer-only: the summary reads view.terminal (populated by applyHandEnd
from the HAND_END event). Sections are modular — these fixtures pin the
visible contract (winner, fan list + total, per-seat point swing, revealed
hands) and that the panel is absent mid-hand.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


def _view(terminal: dict[str, Any] | None) -> dict[str, Any]:
    seats = [
        {
            "seat": s,
            "seat_wind": ["F1", "F2", "F3", "F4"][s],
            "score": 0,
            "concealed": {"count": 13} if s != 0 else ["W2", "W3"],
            "melds": [],
            "flowers": [],
            "discards": [],
        }
        for s in range(4)
    ]
    return {
        "round_wind": "F1",
        "hand_index": 0,
        "turn_index": 40,
        "dealer_seat": 0,
        "current_actor": 0,
        "phase": "TERMINAL" if terminal else "DISCARD",
        "wall": {"remaining_count": 0},
        "seats": seats,
        "last_discard": None,
        "last_drawn": None,
        "pending_claims": [],
        "terminal": terminal,
    }


async def _render(page: Page, server: FakeWireServer, view: dict[str, Any]) -> None:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """async (view) => {
          const lit = await import('lit');
          const { renderHandEndSummary } = await import('/static/render.js');
          const root = document.createElement('div');
          root.id = '__he_root';
          document.body.appendChild(root);
          lit.render(renderHandEndSummary(view, 0, { tileStyle: "ascii" }), root);
          return null;
        }""",
        view,
    )


async def _text(page: Page, selector: str) -> str:
    return cast(
        str,
        await page.evaluate(
            """(sel) => {
              const root = document.getElementById('__he_root');
              const el = root.querySelector(sel);
              return el ? el.textContent : "MISSING";
            }""",
            selector,
        ),
    )


def _hu_terminal() -> dict[str, Any]:
    return {
        "kind": "HU",
        "winner": 2,
        "win_tile": "B5",
        "win_type": "DISCARD",
        "deal_in_seat": 1,
        "fan": [{"name": "All Pungs", "value": 6}, {"name": "Prevalent Wind", "value": 2}],
        "fan_total": 8,
        "score_delta": [-8, -16, 24, -8],
        "final_hands": [
            {"seat": 0, "concealed": ["W2", "W3"], "melds": [], "flowers": []},
            {"seat": 1, "concealed": ["T1", "T2"], "melds": [], "flowers": []},
            {"seat": 2, "concealed": ["B5", "B5"], "melds": [], "flowers": []},
            {"seat": 3, "concealed": ["J1", "J2"], "melds": [], "flowers": []},
        ],
    }


async def test_hu_summary_shows_winner_and_fan(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _render(page, fake_wire_server, _view(_hu_terminal()))
    headline = await _text(page, ".he-headline")
    assert "West (Seat 3) wins" in headline, headline  # winner seat 2 → West
    fan = await _text(page, ".he-fan")
    assert "All Pungs" in fan and "6" in fan
    assert "Prevalent Wind" in fan and "2" in fan
    assert "Total" in fan and "8" in fan


async def test_self_draw_vs_discard_headline(page: Page, fake_wire_server: FakeWireServer) -> None:
    t = _hu_terminal()
    t["win_type"] = "SELF_DRAW"
    t["deal_in_seat"] = None
    await _render(page, fake_wire_server, _view(t))
    assert "self-draw" in await _text(page, ".he-headline")

    t2 = _hu_terminal()  # DISCARD, deal_in_seat=1 (South)
    await _render(page, fake_wire_server, _view(t2))
    headline = await _text(page, ".he-headline")
    assert "on South (Seat 2)'s discard" in headline, headline


async def test_draw_summary_shows_no_winner(page: Page, fake_wire_server: FakeWireServer) -> None:
    t = {
        "kind": "DRAW",
        "winner": None,
        "win_tile": None,
        "win_type": None,
        "deal_in_seat": None,
        "fan": [],
        "fan_total": 0,
        "score_delta": [0, 0, 0, 0],
        "final_hands": [
            {"seat": s, "concealed": ["W2"], "melds": [], "flowers": []} for s in range(4)
        ],
    }
    await _render(page, fake_wire_server, _view(t))
    assert "Exhausted draw" in await _text(page, ".he-headline")
    # No fan section on a draw.
    assert await _text(page, ".he-fan") == "MISSING"


async def test_score_delta_rendered_per_seat_with_winner_highlight(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _render(page, fake_wire_server, _view(_hu_terminal()))
    rows = cast(
        list[list[str]],
        await page.evaluate(
            """() => {
              const root = document.getElementById('__he_root');
              return Array.from(root.querySelectorAll('.he-score-row')).map(
                r => [r.textContent.trim(), ...Array.from(r.classList)]
              );
            }"""
        ),
    )
    assert len(rows) == 4
    # Seat 2 is the winner → +24 and the he-winner class.
    winner_row = next(r for r in rows if "he-winner" in r)
    assert "+24" in winner_row[0], winner_row


async def test_final_hands_revealed_for_all_seats(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Every seat gets a row, and opponents show actual tiles (rendered as
    .tile spans) rather than a hidden count — the hand-end reveal."""
    await _render(page, fake_wire_server, _view(_hu_terminal()))
    tile_counts = cast(
        list[int],
        await page.evaluate(
            """() => {
              const root = document.getElementById('__he_root');
              return Array.from(root.querySelectorAll('.he-hand-row')).map(
                r => r.querySelectorAll('.he-hand-tiles .tile').length
              );
            }"""
        ),
    )
    # Four seats, each revealing its 2-tile crafted hand.
    assert tile_counts == [2, 2, 2, 2], tile_counts


async def test_no_summary_before_terminal(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _render(page, fake_wire_server, _view(None))
    present = await page.evaluate(
        """() => document.getElementById('__he_root').querySelector('.hand-end-summary') !== null"""
    )
    assert present is False
