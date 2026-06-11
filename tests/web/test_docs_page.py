"""<docs-page> navigation + app wiring (Spec 36 § Verification 2-3).

Playwright async API (see conftest — never the sync plugin). The component
tests mount <docs-page> directly; the wiring tests boot the full app against
the FakeWireServer and drive the header button / Esc.
"""

from __future__ import annotations

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


async def _mount_docs_page(page: Page, server: FakeWireServer) -> None:
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """async () => {
          await import('/static/docs.js');
          await customElements.whenDefined('docs-page');
          const el = document.createElement('docs-page');
          document.body.appendChild(el);
          await el.updateComplete;
        }"""
    )


async def test_first_doc_renders_by_default(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _mount_docs_page(page, fake_wire_server)
    body = page.locator("docs-page pre.body")
    await body.wait_for()
    assert "PLAYING ON THIS SERVER" in await body.inner_text()


async def test_topic_click_swaps_document(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _mount_docs_page(page, fake_wire_server)
    await page.locator("docs-page button.topic", has_text="Fan chart").click()
    body = await page.locator("docs-page pre.body").inner_text()
    assert "FAN CHART" in body
    assert "88 FAN" in body
    # The sidebar marks the active topic.
    active = await page.locator("docs-page button.topic.active").inner_text()
    assert "Fan chart" in active


async def test_every_menu_topic_opens_its_doc(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _mount_docs_page(page, fake_wire_server)
    topics = page.locator("docs-page button.topic")
    count = await topics.count()
    assert count >= 9
    for i in range(count):
        await topics.nth(i).click()
        body = await page.locator("docs-page pre.body").inner_text()
        assert len(body) > 400, f"topic #{i} rendered a stub"


async def test_back_button_emits_docs_back(page: Page, fake_wire_server: FakeWireServer) -> None:
    await _mount_docs_page(page, fake_wire_server)
    # Arm the listener (awaited, so it's attached) BEFORE the click; read the
    # resolved promise after. A promise-returning evaluate around the click
    # would race the event and can hang forever (evaluate has no timeout).
    await page.evaluate(
        """() => {
          window._docsBackFired = new Promise((resolve) => {
            document.querySelector('docs-page')
              .addEventListener('docs-back', () => resolve(true), { once: true });
          });
        }"""
    )
    await page.locator("docs-page button.back").click()
    assert await page.evaluate("() => window._docsBackFired") is True


# --- App wiring -------------------------------------------------------------


async def test_header_button_opens_docs_and_esc_returns(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await page.goto(fake_wire_server.url)
    await page.wait_for_load_state("domcontentloaded")
    docs_btn = page.locator("mahjong-app button", has_text="[ docs ]")
    await docs_btn.wait_for()
    await docs_btn.click()
    await page.locator("docs-page pre.body").wait_for()
    await page.keyboard.press("Escape")
    await page.locator("docs-page").wait_for(state="detached")


async def test_docs_reachable_from_auth_screen(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    # Spec 36: docs must not sit behind the auth wall. HELLO with the auth
    # feature shows the login form; [ docs ] still opens, Esc returns to auth.
    await page.goto(fake_wire_server.url)
    await page.wait_for_load_state("domcontentloaded")
    await fake_wire_server.send(
        {
            "kind": "HELLO",
            "seq": 1,
            "protocol_version": 1,
            "server_id": "mahjong-test",
            "features": ["auth"],
        }
    )
    await page.locator("mahjong-app input[name='username']").wait_for(timeout=10_000)
    await page.locator("mahjong-app button", has_text="[ docs ]").click()
    await page.locator("docs-page pre.body").wait_for(timeout=10_000)
    # The auth form is hidden while reading.
    assert await page.locator("mahjong-app input[name='username']").count() == 0
    await page.keyboard.press("Escape")
    await page.locator("mahjong-app input[name='username']").wait_for(timeout=10_000)
