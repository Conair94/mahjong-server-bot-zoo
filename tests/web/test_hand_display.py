"""Hand-display polish fixtures (Layer-8 close-out §1).

Spec: docs/specs/layer8-closeout.md § §1 Verification fixtures.

Three concerns covered:

- **selection highlight**: the cursor tile (``options.selectedTile`` index
  into the engine-sorted concealed list) renders with ``.selected``.
- **just-drawn offset**: when ``view.last_drawn`` points at the local
  seat's just-drawn tile, that tile is pulled out of sort order and
  rendered last with ``.just-drawn`` (CSS adds a margin-left gap).
- **suit-break gap**: the first tile of each new suit group carries
  ``.suit-break`` (half-gap), making the m/p/s/F/J boundaries readable
  at a glance.

Only the local seat is affected.  Opponents render with a single count
string; nothing to highlight, nothing to offset.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


def _view_with_concealed(
    own_seat: int,
    concealed: list[str],
    *,
    last_drawn: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hand-rolled SeatView with the minimum fields the renderer touches.

    We don't go through ``initial_state`` + ``project`` here because we
    want precise control over concealed ordering and last_drawn semantics
    — both of which are exactly the fields under test.
    """
    seats: list[dict[str, Any]] = []
    for s in range(4):
        if s == own_seat:
            seats.append(
                {
                    "seat": s,
                    "seat_wind": ["F1", "F2", "F3", "F4"][s],
                    "score": 0,
                    "concealed": concealed,
                    "melds": [],
                    "flowers": [],
                    "discards": [],
                }
            )
        else:
            seats.append(
                {
                    "seat": s,
                    "seat_wind": ["F1", "F2", "F3", "F4"][s],
                    "score": 0,
                    "concealed": {"count": 13},
                    "melds": [],
                    "flowers": [],
                    "discards": [],
                }
            )
    return {
        "round_wind": "F1",
        "hand_index": 0,
        "turn_index": 0,
        "dealer_seat": 0,
        "current_actor": own_seat,
        "phase": "DISCARD",
        "wall": {"remaining_count": 70},
        "seats": seats,
        "last_discard": None,
        "last_drawn": last_drawn,
        "pending_claims": [],
    }


async def _render_table(
    page: Page,
    server: FakeWireServer,
    view: dict[str, Any],
    own_seat: int,
    *,
    selected_tile: int | None = None,
) -> None:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """
        async ({ view, own_seat, selected_tile }) => {
          const lit = await import('lit');
          const { renderTable } = await import('/static/render.js');
          const root = document.createElement('div');
          root.id = '__hand_test_root';
          document.body.appendChild(root);
          lit.render(
            renderTable(view, own_seat, { tileStyle: "ascii", selectedTile: selected_tile }),
            root,
          );
          return null;
        }
        """,
        {"view": view, "own_seat": own_seat, "selected_tile": selected_tile},
    )


async def _own_tile_mod_classes(page: Page) -> list[list[str]]:
    """Return classList of each .tile-mod span inside the own-seat block,
    in document order.  The own seat is rendered inside the *second*
    ``<pre class="section">`` (see renderTable in render.js)."""
    return cast(
        list[list[str]],
        await page.evaluate(
            """() => {
              const root = document.getElementById('__hand_test_root');
              const sections = root.querySelectorAll('pre.section');
              // Own seat is rendered in the second <pre class="section">.
              const own = sections[1];
              return Array.from(own.querySelectorAll('.tile-mod')).map(
                el => Array.from(el.classList)
              );
            }"""
        ),
    )


async def _own_tile_mod_tokens(page: Page) -> list[str]:
    """Inner text of each .tile-mod's child .tile in render order."""
    return cast(
        list[str],
        await page.evaluate(
            """() => {
              const root = document.getElementById('__hand_test_root');
              const sections = root.querySelectorAll('pre.section');
              const own = sections[1];
              return Array.from(own.querySelectorAll('.tile-mod .tile')).map(
                el => el.textContent.trim()
              );
            }"""
        ),
    )


# --- Fixture 1: selection highlight ----------------------------------------


async def test_fixture_1_selection_highlight(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """With ``selectedTile = 1``, exactly one ``.tile-mod`` carries
    ``.selected`` and it's the second tile in render order (the engine
    sort puts these in W2 / W3 / W4 order)."""
    view = _view_with_concealed(own_seat=0, concealed=["W2", "W3", "W4"])
    await _render_table(page, fake_wire_server, view, own_seat=0, selected_tile=1)

    cls = await _own_tile_mod_classes(page)
    assert len(cls) == 3, cls
    assert "selected" not in cls[0]
    assert "selected" in cls[1]
    assert "selected" not in cls[2]


async def test_fixture_2_no_selection_no_class(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """When ``selectedTile = None`` (no cursor placed), no tile carries
    ``.selected``."""
    view = _view_with_concealed(own_seat=0, concealed=["W2", "W3", "W4"])
    await _render_table(page, fake_wire_server, view, own_seat=0, selected_tile=None)

    cls = await _own_tile_mod_classes(page)
    assert all("selected" not in c for c in cls), cls


# --- Fixture 3: just-drawn tile is offset ----------------------------------


async def test_fixture_3_just_drawn_offset(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """``last_drawn = {seat: 0, tile: "B5"}`` with concealed
    ``[W2, W3, B5, T7]`` (sorted): the renderer pulls B5 out of sort
    position and renders it last with ``.just-drawn``.  Render order
    becomes [W2, W3, T7, B5].  T7 picks up ``.suit-break`` because it's
    the first tile of a new suit group after pulling B5 out."""
    view = _view_with_concealed(
        own_seat=0,
        concealed=["W2", "W3", "B5", "T7"],
        last_drawn={"seat": 0, "tile": "B5", "turn_index": 1},
    )
    await _render_table(page, fake_wire_server, view, own_seat=0)

    tokens = await _own_tile_mod_tokens(page)
    # Token reads: rank+suit-letter (W2 → "2C", T7 → "7B", B5 → "5D").
    # The engine→display letter remap (W→C, B→D, T→B) is set in render.js.
    assert tokens == ["2C", "3C", "7B", "5D"], tokens

    cls = await _own_tile_mod_classes(page)
    assert "just-drawn" not in cls[0]
    assert "just-drawn" not in cls[1]
    assert "just-drawn" not in cls[2]
    assert "just-drawn" in cls[3], cls[3]
    # Suit-break appears on T7 (first non-W suit after W3) but not on B5
    # (just-drawn carries its own offset).
    assert "suit-break" not in cls[0]
    assert "suit-break" not in cls[1]
    assert "suit-break" in cls[2], cls[2]
    assert "suit-break" not in cls[3]


async def test_fixture_4_just_drawn_only_for_own_seat(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """``last_drawn.seat = 1`` but ``ownSeat = 0`` — the local player
    didn't draw, and the opponent's hand is rendered as a count anyway.
    No ``.just-drawn`` span exists anywhere."""
    view = _view_with_concealed(
        own_seat=0,
        concealed=["W2", "W3", "B5"],
        last_drawn={"seat": 1, "tile": "B5", "turn_index": 1},
    )
    await _render_table(page, fake_wire_server, view, own_seat=0)

    cls = await _own_tile_mod_classes(page)
    assert all("just-drawn" not in c for c in cls), cls


async def test_fixture_5_no_last_drawn_no_offset(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """``last_drawn = None`` (post-DISCARD, no draw pending): no tile
    carries ``.just-drawn`` and the hand renders in straight sort order."""
    view = _view_with_concealed(own_seat=0, concealed=["W2", "W3", "B5"])
    await _render_table(page, fake_wire_server, view, own_seat=0)

    cls = await _own_tile_mod_classes(page)
    assert all("just-drawn" not in c for c in cls), cls


# --- Fixture 6: suit-break separator ---------------------------------------


async def test_fixture_6_suit_break_at_each_transition(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Concealed spans all five suit groups: W (characters), B (dots),
    T (bamboo), F (winds), J (dragons).  Each new group's first tile
    carries ``.suit-break``; the very first tile never does."""
    view = _view_with_concealed(
        own_seat=0,
        concealed=["W2", "W3", "B1", "B2", "T9", "F1", "J1"],
    )
    await _render_table(page, fake_wire_server, view, own_seat=0)

    cls = await _own_tile_mod_classes(page)
    # Suit-break: indices 0 (W2 first → no), 1 (W3 same → no),
    #             2 (B1 new → yes), 3 (B2 same → no),
    #             4 (T9 new → yes), 5 (F1 new → yes), 6 (J1 new → yes).
    expected_breaks = [False, False, True, False, True, True, True]
    actual_breaks = ["suit-break" in c for c in cls]
    assert actual_breaks == expected_breaks, list(zip(actual_breaks, expected_breaks))


# --- Fixture 7: combined — selection + just-drawn + suit-break -------------


async def test_fixture_7_combined_decoration(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """All three concerns at once: ``selectedTile = 0`` (the W2), a
    just-drawn B5, and a hand spanning two suits.  Render order is
    [W2(selected), W3, T7(suit-break), B5(just-drawn)].

    Note: ``selectedTile`` is the *original* index into the sorted
    concealed list, not the post-rearrangement render index — so even
    after B5 is pulled to the end, the W2 (original index 0) still
    carries .selected.
    """
    view = _view_with_concealed(
        own_seat=0,
        concealed=["W2", "W3", "B5", "T7"],
        last_drawn={"seat": 0, "tile": "B5", "turn_index": 1},
    )
    await _render_table(page, fake_wire_server, view, own_seat=0, selected_tile=0)

    cls = await _own_tile_mod_classes(page)
    assert "selected" in cls[0], cls[0]
    assert "selected" not in cls[1]
    assert "selected" not in cls[2]
    assert "selected" not in cls[3]
    assert "just-drawn" in cls[3], cls[3]
    assert "suit-break" in cls[2], cls[2]
