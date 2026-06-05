"""Settings menu opens/closes reliably in both themes (Spec 29 follow-up).

The user reported the settings menu "sometimes not opening and closing, depending
on the UI color." This drives the real <mahjong-app> over the FakeWireServer and
exercises open + every close path (close button, Esc, backdrop) under each theme.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import Page, expect

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


def _hello() -> dict[str, Any]:
    # No "auth" feature → straight to lobby; the header (with the gear) renders.
    return {"kind": "HELLO", "seq": 1, "protocol_version": 1, "server_id": "t", "features": []}


async def _set_theme(page: Page, theme: str) -> None:
    await page.evaluate(
        """(theme) => {
          const app = document.querySelector('mahjong-app');
          app.theme = theme;
        }""",
        theme,
    )


async def _open_settings(page: Page) -> None:
    await page.locator('button.theme-btn[title^="Settings"]').click()


def _modal(page: Page):
    return page.locator("settings-menu").locator(".modal")


@pytest.mark.parametrize("theme", ["dark", "light"])
async def test_settings_open_and_close_paths(
    page: Page, fake_wire_server: FakeWireServer, theme: str
) -> None:
    await page.goto(fake_wire_server.url)
    await fake_wire_server.send(_hello())
    await _set_theme(page, theme)

    # Open via the header gear.
    await _open_settings(page)
    await expect(_modal(page)).to_be_visible()

    # Close via the close button.
    await page.locator("settings-menu").locator("button.close").click()
    await expect(_modal(page)).to_have_count(0)

    # Reopen, close via Esc.
    await _open_settings(page)
    await expect(_modal(page)).to_be_visible()
    await page.keyboard.press("Escape")
    await expect(_modal(page)).to_have_count(0)

    # Reopen, close via backdrop click (top-left, away from the modal card).
    await _open_settings(page)
    await expect(_modal(page)).to_be_visible()
    await page.locator("settings-menu").locator(".backdrop").click(position={"x": 5, "y": 5})
    await expect(_modal(page)).to_have_count(0)
