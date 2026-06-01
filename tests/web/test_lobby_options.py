"""Lobby advanced-options builder (§22.6 Part A, client side).

Pins `LobbyView._buildOptions` — the non-trivial bit: defaults collapse to
null (server applies its own defaults), and each control maps to the right
CREATE_TABLE.options field.
"""

from __future__ import annotations

from typing import Any, cast

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
