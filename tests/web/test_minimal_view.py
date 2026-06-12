"""Minimal play-view fixtures (minimal-play-view.md).

The minimal view (render.js ``renderMinimal``) is the decluttered, large-print
default. These fixtures pin its load-bearing contracts:

- player **names** (threaded onto the snapshot as ``seat.name``) headline every
  seat, and bot seats are badged ``·bot``;
- the **combined discard pond** renders ``view.discard_pond`` in arrival order
  with the latest tile marked;
- the **whose-turn** banner reads the active seat (YOUR TURN for the own seat);
- the **last discard** shows large with the discarder's name;
- a CLAIM_WINDOW raises a **prominent claim banner** in ``<game-pane>``.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio

_NAMES = ["Alice", "Bob", "Carol", "Dave"]


def _seat(seat: int, *, own: bool, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "seat": seat,
        "seat_wind": ["F1", "F2", "F3", "F4"][seat],
        "score": 100 * (seat + 1),
        "name": _NAMES[seat],
        "is_bot": seat == 3,  # Dave is the bot
        "melds": [],
        "flowers": [],
        "discards": [],
        "concealed": ["W2", "W3", "W4"] if own else {"count": 13},
    }
    base.update(over)
    return base


def _view(
    *,
    own_seat: int = 0,
    current_actor: int = 0,
    phase: str = "DISCARD",
    last_discard: dict[str, Any] | None = None,
    discard_pond: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "round_wind": "F1",
        "hand_index": 0,
        "turn_index": 0,
        "dealer_seat": 0,
        "current_actor": current_actor,
        "phase": phase,
        "wall": {"remaining_count": 70, "drawn_count": 0},
        "seats": [_seat(s, own=(s == own_seat)) for s in range(4)],
        "last_discard": last_discard,
        "last_drawn": None,
        "pending_claims": [],
        **({"discard_pond": discard_pond} if discard_pond is not None else {}),
    }


async def _render_minimal(
    page: Page,
    server: FakeWireServer,
    view: dict[str, Any],
    own_seat: int,
    discard_layout: str | None = None,
) -> None:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """async ({ view, own_seat, discardLayout }) => {
          const lit = await import('lit');
          const { renderMinimal } = await import('/static/render.js');
          const root = document.createElement('div');
          root.id = '__mv_root';
          document.body.appendChild(root);
          const opts = { tileStyle: "ascii" };
          if (discardLayout) opts.discardLayout = discardLayout;
          lit.render(renderMinimal(view, own_seat, opts), root);
          return null;
        }""",
        {"view": view, "own_seat": own_seat, "discardLayout": discard_layout},
    )


async def _text_of(page: Page, selector: str) -> str:
    return cast(
        str,
        await page.evaluate(
            """(sel) => {
              const el = document.getElementById('__mv_root').querySelector(sel);
              return el ? el.textContent.replace(/\\s+/g, ' ').trim() : 'MISSING';
            }""",
            selector,
        ),
    )


async def test_names_headline_seats_and_bots_badged(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Opponent roster rows show player names; the bot seat is badged ·bot;
    the own block shows YOU + the player's name."""
    await _render_minimal(page, fake_wire_server, _view(own_seat=0), own_seat=0)
    roster = await _text_of(page, ".mv-roster")
    # Opponents are seats 1,2,3 (Bob, Carol, Dave) in play order from own.
    assert "Bob" in roster and "Carol" in roster and "Dave" in roster, roster
    assert "·bot" in roster, roster  # Dave is the bot
    own = await _text_of(page, ".mv-own-head")
    assert "YOU" in own and "Alice" in own, own


async def test_combined_pond_in_arrival_order_latest_marked(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """The pond renders ``discard_pond`` tiles in order; the last carries
    ``.pond-latest``. (Pond is now opt-in via discardLayout; rows is default —
    Spec 40.)"""
    pond = [
        {"seat": 0, "tile": "W3"},
        {"seat": 1, "tile": "B5"},
        {"seat": 2, "tile": "T7"},
    ]
    await _render_minimal(
        page, fake_wire_server, _view(discard_pond=pond), own_seat=0, discard_layout="pond"
    )
    tokens = cast(
        list[str],
        await page.evaluate(
            """() => Array.from(
                 document.getElementById('__mv_root').querySelectorAll('.mv-pond-tiles .tile')
               ).map(el => el.textContent.trim())"""
        ),
    )
    # ascii display remap: W3→3C, B5→5D, T7→7B.
    assert tokens == ["3C", "5D", "7B"], tokens
    latest_is_last = cast(
        bool,
        await page.evaluate(
            """() => {
              const tiles = document.getElementById('__mv_root').querySelectorAll('.mv-pond-tiles .pond-tile');
              return tiles.length > 0 && tiles[tiles.length - 1].classList.contains('pond-latest');
            }"""
        ),
    )
    assert latest_is_last


async def test_default_layout_is_per_player_rows(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Spec 40: with no discardLayout option, discards render as one row per
    seat (in seat order), not the combined pond."""
    view = _view(own_seat=0)
    view["seats"][0]["discards"] = ["W3"]
    view["seats"][1]["discards"] = ["B5", "T7"]
    await _render_minimal(page, fake_wire_server, view, own_seat=0)  # default → rows

    row_count = cast(
        int,
        await page.evaluate(
            "() => document.getElementById('__mv_root').querySelectorAll('.mv-drow').length"
        ),
    )
    assert row_count == 4, row_count  # one row per seat, seat order

    # The combined pond must NOT render in the default layout.
    pond_count = cast(
        int,
        await page.evaluate(
            "() => document.getElementById('__mv_root').querySelectorAll('.mv-pond-tiles').length"
        ),
    )
    assert pond_count == 0, pond_count

    rows_text = await _text_of(page, ".mv-drows")
    assert "Alice" in rows_text and "Bob" in rows_text, rows_text


async def test_turn_banner_marks_your_turn(page: Page, fake_wire_server: FakeWireServer) -> None:
    """When current_actor is the own seat, the YOUR TURN banner shows."""
    await _render_minimal(page, fake_wire_server, _view(current_actor=0), own_seat=0)
    banner = await _text_of(page, ".mv-turn")
    assert "YOUR TURN" in banner, banner
    has_you_class = cast(
        bool,
        await page.evaluate(
            "() => !!document.getElementById('__mv_root').querySelector('.mv-turn-you')"
        ),
    )
    assert has_you_class


async def test_turn_banner_names_the_active_opponent(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """When another seat is active, the banner names them (not the own seat)."""
    await _render_minimal(page, fake_wire_server, _view(current_actor=1), own_seat=0)
    banner = await _text_of(page, ".mv-turn")
    assert "Bob" in banner and "turn" in banner.lower(), banner


async def test_claim_window_does_not_leak_in_turn_banner(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """During CLAIM_WINDOW the banner must NOT announce the window (info leak,
    Spec 22 § 22.1) — it stays on the discarder's turn (current_actor is left
    pointing at the discarder), revealing nothing about pending claims."""
    # Bob (seat 1) just discarded; the engine opened a claim window.
    view = _view(current_actor=1, phase="CLAIM_WINDOW")
    await _render_minimal(page, fake_wire_server, view, own_seat=0)
    banner = await _text_of(page, ".mv-turn")
    assert "claim" not in banner.lower(), banner  # no "Claim window" tell
    assert "Bob" in banner, banner  # stable on the discarder's turn
    no_claim_class = cast(
        bool,
        await page.evaluate(
            "() => !document.getElementById('__mv_root').querySelector('.mv-turn-claim')"
        ),
    )
    assert no_claim_class


async def test_last_discard_shows_large_with_discarder_name(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """The most-recent discard renders with the discarder's name + the tile."""
    ld = {"seat": 1, "tile": "B5", "turn_index": 3}
    await _render_minimal(page, fake_wire_server, _view(last_discard=ld), own_seat=0)
    label = await _text_of(page, ".mv-ld-label")
    assert "Bob" in label, label
    tile_tok = await _text_of(page, ".mv-ld-tile .tile")
    assert tile_tok == "5D", tile_tok


async def test_claim_window_raises_prominent_banner_in_game_pane(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """A CLAIM_WINDOW prompt with a real (non-PASS) option raises the
    ``.claim-chip.mv-claim`` banner when the pane is in minimal mode."""
    await page.goto(fake_wire_server.url)
    await page.wait_for_load_state("domcontentloaded")
    text = cast(
        str,
        await page.evaluate(
            """async ({ view }) => {
              await import('/static/app.js');
              await customElements.whenDefined('game-pane');
              const el = document.createElement('game-pane');
              el.id = '__gp_mv';
              el.viewMode = 'minimal';
              document.body.appendChild(el);
              el.setSnapshot(view, 0);
              el.currentPrompt = {
                phase: 'CLAIM_WINDOW',
                legal_actions: [{ type: 'PASS' }, { type: 'PENG', tile: 'B5' }],
              };
              await el.updateComplete;
              const banner = el.shadowRoot.querySelector('.claim-chip.mv-claim');
              return banner ? banner.textContent.trim() : 'MISSING';
            }""",
            {"view": _view()},
        ),
    )
    assert "CLAIM AVAILABLE" in text, text
