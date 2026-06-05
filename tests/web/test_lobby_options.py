"""Lobby advanced-options builder (§22.6 Part A, client side).

Pins `LobbyView._buildOptions` — the non-trivial bit: defaults collapse to
null (server applies its own defaults), and each control maps to the right
CREATE_TABLE.options field.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


async def _build_options(page: Page, server: FakeWireServer, state: dict[str, Any]) -> Any:
    """Mount <lobby-view>, apply state, return _buildOptions()."""
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    return await page.evaluate(
        """async (state) => {
          await import('/static/app.js');
          await customElements.whenDefined('lobby-view');
          const el = document.createElement('lobby-view');
          document.body.appendChild(el);
          Object.assign(el, state);
          await el.updateComplete;
          return el._buildOptions();
        }""",
        state,
    )


async def test_all_defaults_build_null(page: Page, fake_wire_server: FakeWireServer) -> None:
    opts = await _build_options(
        page,
        fake_wire_server,
        {"pacingPreset": "normal", "decideTimeout": 60, "timeoutsEnabled": True},
    )
    assert opts is None


async def test_preset_pacing_builds_string(page: Page, fake_wire_server: FakeWireServer) -> None:
    opts = await _build_options(
        page,
        fake_wire_server,
        {"pacingPreset": "slow", "decideTimeout": 60, "timeoutsEnabled": True},
    )
    assert opts["bot_pacing"] == "slow"
    assert opts["decide_timeout_seconds"] == 60
    assert opts["timeouts_enabled"] is True


async def test_custom_pacing_builds_minmax(page: Page, fake_wire_server: FakeWireServer) -> None:
    opts = await _build_options(
        page,
        fake_wire_server,
        {"pacingPreset": "custom", "customMin": 2, "customMax": 4, "decideTimeout": 60, "timeoutsEnabled": True},
    )
    assert opts["bot_pacing"] == {"min_s": 2, "max_s": 4}


async def test_timeouts_disabled_omits_decide_timeout(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    opts = await _build_options(
        page,
        fake_wire_server,
        {"pacingPreset": "normal", "decideTimeout": 60, "timeoutsEnabled": False},
    )
    assert opts["timeouts_enabled"] is False
    assert "decide_timeout_seconds" not in opts


async def test_default_is_untimed(page: Page, fake_wire_server: FakeWireServer) -> None:
    """A freshly-mounted lobby (no state override) defaults to NO turn timer, so
    new tables wait for humans indefinitely unless the creator opts in."""
    await page.goto(fake_wire_server.url)
    await page.wait_for_load_state("domcontentloaded")
    result = await page.evaluate(
        """async () => {
          await import('/static/app.js');
          await customElements.whenDefined('lobby-view');
          const el = document.createElement('lobby-view');
          document.body.appendChild(el);
          await el.updateComplete;
          return { enabled: el.timeoutsEnabled, opts: el._buildOptions() };
        }"""
    )
    assert result["enabled"] is False
    # The default build therefore carries timeouts_enabled:false (not null).
    assert result["opts"] is not None
    assert result["opts"]["timeouts_enabled"] is False


async def test_timer_toggle_is_surfaced_not_buried(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """The turn-timer checkbox must be visible in the create form WITHOUT
    expanding the collapsed 'advanced' section (the bug: it was buried)."""
    await page.goto(fake_wire_server.url)
    await page.wait_for_load_state("domcontentloaded")
    found = await page.evaluate(
        """async () => {
          await import('/static/app.js');
          await customElements.whenDefined('lobby-view');
          const el = document.createElement('lobby-view');
          document.body.appendChild(el);
          await el.updateComplete;
          // showAdvanced is false by default; the timer-toggle lives outside adv-body.
          const toggle = el.renderRoot.querySelector('.timer-toggle');
          const insideAdvanced = !!(toggle && toggle.closest('.adv-body'));
          return { present: !!toggle, insideAdvanced, advancedOpen: el.showAdvanced };
        }"""
    )
    assert found["present"] is True
    assert found["advancedOpen"] is False  # advanced stays collapsed
    assert found["insideAdvanced"] is False  # yet the toggle is still shown


# --- Bot picker (agent selection on table creation) -----------------------


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


async def test_bot_picker_renders_select_per_bot_seat(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """With 1 human + 3 bots and an advertised menu, three bot <select>s show
    with the v0 option present."""
    bots = [{"bot_id": "v0", "label": "v0 — greedy offense", "description": "d"}]
    await _mount_lobby(page, fake_wire_server, {"desiredHumans": 1, "availableBots": bots})
    counts = await page.evaluate(
        """() => {
          const el = document.getElementById('lv');
          const selects = el.renderRoot.querySelectorAll('.bot-select');
          const opts = selects.length
            ? [...selects[0].options].map((o) => o.value)
            : [];
          return { selects: selects.length, firstOptions: opts };
        }"""
    )
    assert counts["selects"] == 3  # seats 1,2,3 are bots
    assert "v0" in counts["firstOptions"]


async def test_seats_payload_carries_selected_bot_id(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """_seatsPayload reflects the per-seat bot choice; unset seats fall back to
    the default (first advertised) bot."""
    bots = [
        {"bot_id": "v0", "label": "v0", "description": ""},
        {"bot_id": "x9", "label": "x9", "description": ""},
    ]
    await _mount_lobby(page, fake_wire_server, {"desiredHumans": 1, "availableBots": bots})
    payload = await page.evaluate(
        """async () => {
          const el = document.getElementById('lv');
          el._pickBot(2, 'x9');      // override seat 2 only
          await el.updateComplete;
          return el._seatsPayload();
        }"""
    )
    assert payload[0] == {"kind": "human"}
    assert payload[1] == {"kind": "bot", "bot_id": "v0"}  # default
    assert payload[2] == {"kind": "bot", "bot_id": "x9"}  # picked
    assert payload[3] == {"kind": "bot", "bot_id": "v0"}  # default


async def test_bot_picker_falls_back_to_v0_without_menu(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Old server (no HELLO.bots): payload still defaults bot seats to v0."""
    await _mount_lobby(page, fake_wire_server, {"desiredHumans": 2, "availableBots": []})
    payload = await page.evaluate(
        "() => document.getElementById('lv')._seatsPayload()"
    )
    assert payload[2] == {"kind": "bot", "bot_id": "v0"}
    assert payload[3] == {"kind": "bot", "bot_id": "v0"}
