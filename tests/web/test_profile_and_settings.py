"""Profile home page + settings menu (Spec 28, client side).

Spec: docs/specs/profile-and-settings.md § Verification fixtures 11-14.

- Graph: pure `renderScoreGraph` (fixtures 11-12).
- Settings menu: descriptor-driven rows + cycle event (fixture 14 / S-A1..S-A3).
- Profile page: renders a PROFILE payload (stats + graph + recent).
- Seam: a PROFILE frame dispatched into the live <mahjong-app> switches to the
  profile view and renders it (wire→UI seam, not pre-set view state).
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


_PROFILE: dict[str, Any] = {
    "kind": "PROFILE",
    "seq": 5,
    "account": {"account_id": 3, "username": "connor", "display_name": "Connor"},
    "stats": {
        "hands_played": 4,
        "hands_won": 1,
        "draws": 1,
        "total_score": 12,
        "total_win_points": 24,
        "best_win_fan": 8,
        "first_played_ms": 1717500000000,
        "last_played_ms": 1717589000000,
    },
    "recent": [
        {
            "hand_id": "h2",
            "started_at_ms": 1717589000000,
            "ended_at_ms": 1717589120000,
            "terminal_kind": "HU",
            "won": True,
            "score_delta": 24,
            "fan_total": 8,
            "seat": 0,
        },
        {
            "hand_id": "h1",
            "started_at_ms": 1717500000000,
            "ended_at_ms": 1717500120000,
            "terminal_kind": "HU",
            "won": False,
            "score_delta": -8,
            "fan_total": None,
            "seat": 0,
        },
    ],
    "series": [
        {"ended_at_ms": 1717500120000, "cumulative": -8},
        {"ended_at_ms": 1717589120000, "cumulative": 24},
    ],
}


# ---------------------------------------------------------------------------
# Graph — pure function (fixtures 11, 12)
# ---------------------------------------------------------------------------


async def _graph(page: Page, server: FakeWireServer, series: Any, opts: Any = None) -> str:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    return await page.evaluate(
        """async ({ series, opts }) => {
          const { renderScoreGraph } = await import('/static/render.js');
          return renderScoreGraph(series, opts ?? {});
        }""",
        {"series": series, "opts": opts},
    )


async def test_graph_empty_is_empty_state(page: Page, fake_wire_server: FakeWireServer) -> None:
    """Fixture 11: empty series → empty-state string, no exception."""
    out = await _graph(page, fake_wire_server, [])
    assert out == "(no games yet)"


async def test_graph_single_point_is_flat_no_crash(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Fixture 11: single point renders without NaN / exception."""
    out = await _graph(page, fake_wire_server, [{"ended_at_ms": 1, "cumulative": 5}])
    assert "NaN" not in out
    assert "●" in out


async def test_graph_scaling_max_on_top_row(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Fixture 12: requested height; the max value labels the top row."""
    series = [{"ended_at_ms": i, "cumulative": v} for i, v in enumerate([-8, 0, 24])]
    out = await _graph(page, fake_wire_server, series, {"width": 24, "height": 7})
    lines = out.split("\n")
    assert len(lines) == 7
    assert "+24" in lines[0]   # max at the top
    assert "-8" in lines[-1]   # min at the bottom
    assert "NaN" not in out


# ---------------------------------------------------------------------------
# Settings menu (fixture 14 / S-A1..S-A3)
# ---------------------------------------------------------------------------


async def _mount_settings(
    page: Page, server: FakeWireServer, values: dict[str, str], table_active: bool
) -> None:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """async ({ values, tableActive }) => {
          await import('/static/app.js');
          await customElements.whenDefined('settings-menu');
          const el = document.createElement('settings-menu');
          el.id = '__sm';
          el.values = values;
          el.tableActive = tableActive;
          window.__cycles = [];
          el.addEventListener('setting-cycle', (e) => window.__cycles.push(e.detail.key));
          document.body.appendChild(el);
          await el.updateComplete;
        }""",
        {"values": values, "tableActive": table_active},
    )


async def test_settings_lists_all_rows_with_values(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """S-A1: every descriptor row renders with its current value + hotkey."""
    await _mount_settings(
        page,
        fake_wire_server,
        {"theme": "dark", "tile-style": "ascii", "pane-chat": "off", "pane-stats": "on", "pane-spectator": "off"},
        table_active=True,
    )
    text = await page.evaluate(
        "document.getElementById('__sm').shadowRoot.textContent"
    )
    for label in ("Theme", "Tiles", "Chat pane", "Stats pane", "Spectator pane"):
        assert label in text
    assert "Alt+T" in text and "Alt+," not in text  # hotkeys shown, no stray
    assert "dark" in text and "ascii" in text


async def test_settings_cycle_emits_event(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """S-A2: clicking the Theme value emits setting-cycle {key:'theme'}."""
    await _mount_settings(
        page,
        fake_wire_server,
        {"theme": "dark", "tile-style": "ascii", "pane-chat": "off", "pane-stats": "off", "pane-spectator": "off"},
        table_active=True,
    )
    # First .val button is the Theme row.
    await page.evaluate(
        "document.getElementById('__sm').shadowRoot.querySelectorAll('.val')[0].click()"
    )
    cycles = await page.evaluate("window.__cycles")
    assert cycles == ["theme"]


async def test_settings_pane_rows_disabled_in_lobby(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """S-A3: with tableActive=false the table-scoped pane rows are disabled."""
    await _mount_settings(
        page,
        fake_wire_server,
        {"theme": "dark", "tile-style": "ascii", "sound": "on", "pane-chat": "off", "pane-stats": "off", "pane-spectator": "off"},
        table_active=False,
    )
    # .val buttons order matches SETTINGS: [theme, tiles, sound, chat, stats, spectator].
    # The first three are global (enabled); the pane rows are table-scoped (disabled).
    disabled = await page.evaluate(
        """Array.from(
            document.getElementById('__sm').shadowRoot.querySelectorAll('.val')
        ).map((b) => b.disabled)"""
    )
    assert disabled == [False, False, False, True, True, True]


# ---------------------------------------------------------------------------
# Profile page render
# ---------------------------------------------------------------------------


async def _mount_profile(page: Page, server: FakeWireServer, profile: Any) -> str:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    return await page.evaluate(
        """async (profile) => {
          await import('/static/app.js');
          await customElements.whenDefined('profile-page');
          const el = document.createElement('profile-page');
          el.id = '__pp';
          el.profile = profile;
          document.body.appendChild(el);
          await el.updateComplete;
          return el.shadowRoot.textContent;
        }""",
        profile,
    )


async def test_profile_renders_stats_and_graph(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Stats grid derives win-rate / avg-win, shows total standing, and the
    graph + recent rows render."""
    text = await _mount_profile(page, fake_wire_server, _PROFILE)
    assert "Connor" in text
    assert "25.0%" in text      # win rate 1/4
    assert "+24" in text        # avg win size (1 win of +24) and/or recent
    assert "+12" in text        # total standing
    # Graph present (some plotted marker), recent table present.
    graph = await page.evaluate(
        "document.getElementById('__pp').shadowRoot.querySelector('pre.graph').textContent"
    )
    assert "●" in graph
    assert await page.evaluate(
        "!!document.getElementById('__pp').shadowRoot.querySelector('table.recent')"
    )


async def test_profile_empty_state(page: Page, fake_wire_server: FakeWireServer) -> None:
    empty = dict(_PROFILE)
    empty["stats"] = {**_PROFILE["stats"], "hands_played": 0, "hands_won": 0}
    empty["recent"] = []
    empty["series"] = []
    text = await _mount_profile(page, fake_wire_server, empty)
    assert "No games yet" in text


# ---------------------------------------------------------------------------
# Wire→UI seam: PROFILE frame dispatched into the live app shows the page
# ---------------------------------------------------------------------------


async def test_profile_frame_switches_view_and_renders(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """A real PROFILE frame through the app's message dispatch switches the
    view to 'profile' and renders the profile-page (not pre-set view state)."""
    await page.goto(fake_wire_server.url)
    await page.wait_for_load_state("domcontentloaded")
    result = await page.evaluate(
        """async (profile) => {
          await import('/static/app.js');
          await customElements.whenDefined('mahjong-app');
          const app = document.querySelector('mahjong-app');
          await app.updateComplete;
          // Let firstUpdated's queueMicrotask attach the message listener.
          await new Promise((r) => setTimeout(r, 50));
          // Dispatch a PROFILE frame exactly as the WS layer would.
          app._conn.dispatchEvent(new CustomEvent('message', { detail: profile }));
          await app.updateComplete;
          const pp = app.shadowRoot.querySelector('profile-page');
          if (pp) await pp.updateComplete;
          return {
            view: app._view,
            hasPage: !!pp,
            text: pp ? pp.shadowRoot.textContent : null,
          };
        }""",
        _PROFILE,
    )
    assert result["view"] == "profile"
    assert result["hasPage"] is True
    assert "Connor" in result["text"]
