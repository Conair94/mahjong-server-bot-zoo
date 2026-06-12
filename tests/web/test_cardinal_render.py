"""Pinwheel-widget fixtures (Step 8.9 — revised 2026-05-27).

Spec: docs/specs/cardinal-ui.md § Verification fixtures.

The pinwheel is a compact 3×3 indicator that sits next to the stacked
seat blocks.  Layout: north badge top, east/center/west in the middle
row, south badge at the bottom.  YOU is always at the south position.

Badges display the **wind number** of each seat (MCR convention):
    F1 East = 1, F2 South = 2, F3 West = 3, F4 North = 4.

The center cell carries an arrow that points at the cardinal position
of whoever just discarded the tile shown in the center.  It points at the
discarder in every phase that has a last-discard (including CLAIM_WINDOW —
a `?` there leaked a claim-deciding tell, Spec 22 § 22.1) and falls back to
a neutral ``·`` at TERMINAL or before the first discard.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.async_api import Page

from mahjong.engine.state import initial_state, project
from mahjong.engine.types import RuleSetRef

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


_TEST_RULESET: RuleSetRef = cast(RuleSetRef, {"id": "mcr-2006", "version": 1})


def _base_view(own_seat: int = 0) -> dict[str, Any]:
    """Per-seat projection at deal time (dealer = seat 0 by default).

    Wind assignment with dealer_seat=0:
        seat 0 → F1 (East,  badge "1")
        seat 1 → F2 (South, badge "2")
        seat 2 → F3 (West,  badge "3")
        seat 3 → F4 (North, badge "4")
    """
    state = initial_state(_TEST_RULESET, seed=42)
    return cast(dict[str, Any], project(state, own_seat))


async def _render_pinwheel(
    page: Page, server: FakeWireServer, view: dict[str, Any], own_seat: int
) -> None:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """
        async ({ view, own_seat }) => {
          const lit = await import('lit');
          const { renderPinwheel } = await import('/static/render.js');
          const root = document.createElement('div');
          root.id = '__pinwheel_test_root';
          document.body.appendChild(root);
          lit.render(renderPinwheel(view, own_seat), root);
          return null;
        }
        """,
        {"view": view, "own_seat": own_seat},
    )


async def _text(page: Page, selector: str) -> str | None:
    return await page.evaluate(
        """sel => {
          const el = document.getElementById('__pinwheel_test_root').querySelector(sel);
          return el ? el.textContent.trim() : null;
        }""",
        selector,
    )


async def _exists(page: Page, selector: str) -> bool:
    return await page.evaluate(
        """sel => !!document.getElementById('__pinwheel_test_root').querySelector(sel)""",
        selector,
    )


async def _classes(page: Page, selector: str) -> list[str]:
    return cast(
        list[str],
        await page.evaluate(
            """sel => {
              const el = document.getElementById('__pinwheel_test_root').querySelector(sel);
              return el ? Array.from(el.classList) : [];
            }""",
            selector,
        ),
    )


# --- Fixture 1: badges are wind numbers, not position labels ---------------


async def test_fixture_1_badges_are_wind_numbers(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """With ``ownSeat = 2`` (West wind in the default-dealer projection):
    south badge = ``3`` (own seat's wind), east badge = ``4`` (seat 3 = North),
    north badge = ``1`` (seat 0 = East), west badge = ``2`` (seat 1 = South).

    The spatial layout has YOU at south, but the badge content is the seat's
    *wind number*, not a relative position letter.
    """
    view = _base_view(own_seat=2)
    await _render_pinwheel(page, fake_wire_server, view, own_seat=2)

    assert await _text(page, ".pw-south .pw-badge") == "3"
    assert await _text(page, ".pw-east .pw-badge") == "4"
    assert await _text(page, ".pw-north .pw-badge") == "1"
    assert await _text(page, ".pw-west .pw-badge") == "2"


@pytest.mark.parametrize("own_seat", [0, 1, 2, 3])
async def test_fixture_1b_dealer_is_always_one(
    page: Page, fake_wire_server: FakeWireServer, own_seat: int
) -> None:
    """East (dealer) is always badge ``1``, regardless of which seat the
    viewer occupies.  This is the load-bearing MCR-wind invariant."""
    view = _base_view(own_seat=own_seat)
    await _render_pinwheel(page, fake_wire_server, view, own_seat=own_seat)

    badges = [
        await _text(page, ".pw-south .pw-badge"),
        await _text(page, ".pw-east .pw-badge"),
        await _text(page, ".pw-north .pw-badge"),
        await _text(page, ".pw-west .pw-badge"),
    ]
    # Exactly one badge says "1" (the dealer / East), one each says "2", "3", "4".
    assert sorted(badges) == ["1", "2", "3", "4"], badges


# --- Fixture 2: arrow points at the last discarder -------------------------


@pytest.mark.parametrize(
    "discarder_seat,expected_arrow",
    [(0, "↓"), (1, "→"), (2, "↑"), (3, "←")],
)
async def test_fixture_2_arrow_points_at_last_discarder(
    page: Page,
    fake_wire_server: FakeWireServer,
    discarder_seat: int,
    expected_arrow: str,
) -> None:
    """``ownSeat = 0``; the arrow's direction is determined by which seat
    the ``last_discard`` came from, not by ``current_actor``."""
    view = _base_view(own_seat=0)
    view["last_discard"] = {"seat": discarder_seat, "tile": "T5", "turn_index": 1}
    # current_actor intentionally different to prove the arrow doesn't follow it
    view["current_actor"] = (discarder_seat + 1) % 4
    view["phase"] = "DISCARD"
    await _render_pinwheel(page, fake_wire_server, view, own_seat=0)

    arrow = await _text(page, ".pw-arrow")
    assert (
        arrow == expected_arrow
    ), f"discarder={discarder_seat} → expected {expected_arrow!r}, got {arrow!r}"


# --- Fixture 3: claim-window arrow follows the discarder (no info leak) -----


async def test_fixture_3_claim_window_arrow_points_at_discarder(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Spec 22 § 22.1: during a CLAIM_WINDOW the arrow must point at the
    discarder exactly as in DISCARD phase — never a `?`. A `?` leaked the
    fact that someone is deciding whether to claim, a tell you'd only have
    by reading body language at a physical table."""
    view = _base_view(own_seat=0)
    view["phase"] = "CLAIM_WINDOW"
    view["pending_claims"] = [{"seat": 1}, {"seat": 2}]
    view["last_discard"] = {"seat": 1, "tile": "T5", "turn_index": 1}
    await _render_pinwheel(page, fake_wire_server, view, own_seat=0)
    assert await _text(page, ".pw-arrow") == "→"  # discarder seat 1, relative to own seat 0


async def test_fixture_3b_claim_window_without_discard_is_neutral(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Defensive: a CLAIM_WINDOW with no last_discard (currently impossible
    per spec) falls back to the neutral marker, not a directional arrow."""
    view = _base_view(own_seat=0)
    view["phase"] = "CLAIM_WINDOW"
    view["last_discard"] = None
    await _render_pinwheel(page, fake_wire_server, view, own_seat=0)
    assert await _text(page, ".pw-arrow") == "·"


# --- Fixture 4: terminal phase shows neutral indicator ---------------------


async def test_fixture_4_terminal_phase_has_neutral_marker(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    view = _base_view(own_seat=0)
    view["phase"] = "TERMINAL"
    await _render_pinwheel(page, fake_wire_server, view, own_seat=0)
    arrow = await _text(page, ".pw-arrow")
    assert arrow not in {"↑", "↓", "←", "→", "?"}


# --- Fixture 5: last-discard tile renders as a unicode glyph ---------------


async def test_fixture_5_last_discard_uses_unicode_tile(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """The pinwheel forces ``tileStyle: "unicode"`` regardless of the caller's
    option, because the tile is the visual anchor and the unicode glyph
    reads better at a large size."""
    view = _base_view(own_seat=0)
    view["last_discard"] = {"seat": 1, "tile": "T5", "turn_index": 7}
    view["phase"] = "DISCARD"
    # Caller passes ascii; the pinwheel ignores it.
    await page.goto(fake_wire_server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """
        async ({ view, own_seat }) => {
          const lit = await import('lit');
          const { renderPinwheel } = await import('/static/render.js');
          const root = document.createElement('div');
          root.id = '__pinwheel_test_root';
          document.body.appendChild(root);
          lit.render(renderPinwheel(view, own_seat, { tileStyle: "ascii" }), root);
          return null;
        }
        """,
        {"view": view, "own_seat": 0},
    )

    # T5 in unicode = bamboo 5 = U+1F014 (0x1f010 + 4).
    tile_text = await _text(page, ".pw-last-discard .tile")
    expected_glyph = chr(0x1F010 + 4)
    assert (
        tile_text == expected_glyph
    ), f"expected unicode bamboo-5 glyph {expected_glyph!r}, got {tile_text!r}"


# --- Fixture 6: no last-discard placeholder --------------------------------


async def test_fixture_6_no_last_discard_placeholder(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    view = _base_view(own_seat=0)
    view["last_discard"] = None
    view["phase"] = "DISCARD"
    await _render_pinwheel(page, fake_wire_server, view, own_seat=0)

    assert not await _exists(page, ".pw-last-discard .tile")
    assert await _exists(page, ".pw-last-discard.pw-empty")


# --- Fixture 7: active highlight follows last_discard.seat ------------------


async def test_fixture_7_active_badge_follows_discarder(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """With ``last_discard.seat = 1`` and ``ownSeat = 0``, the east badge
    picks up ``.active`` — same source of truth as the arrow."""
    view = _base_view(own_seat=0)
    view["last_discard"] = {"seat": 1, "tile": "T5", "turn_index": 1}
    view["phase"] = "DISCARD"
    await _render_pinwheel(page, fake_wire_server, view, own_seat=0)

    east = await _classes(page, ".pw-east .pw-badge")
    south = await _classes(page, ".pw-south .pw-badge")
    north = await _classes(page, ".pw-north .pw-badge")
    west = await _classes(page, ".pw-west .pw-badge")

    assert "active" in east
    assert "active" not in south
    assert "active" not in north
    assert "active" not in west


# --- Fixture 8: own-seat badge is marked .own ------------------------------


@pytest.mark.parametrize("own_seat", [0, 1, 2, 3])
async def test_fixture_8_south_badge_carries_own_class(
    page: Page, fake_wire_server: FakeWireServer, own_seat: int
) -> None:
    """The south-position badge is always YOU — pinned via the ``.own``
    CSS class so the styling can mark "this is your seat" (e.g.,
    underline + accent) regardless of which wind you hold this hand."""
    view = _base_view(own_seat=own_seat)
    await _render_pinwheel(page, fake_wire_server, view, own_seat=own_seat)

    south_classes = await _classes(page, ".pw-south .pw-badge")
    assert "own" in south_classes

    for cardinal in ("east", "north", "west"):
        other_classes = await _classes(page, f".pw-{cardinal} .pw-badge")
        assert "own" not in other_classes, f"{cardinal} badge incorrectly marked own"
