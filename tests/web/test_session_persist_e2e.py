"""A page reload restores the session via RESUME (Spec 29 Bug A).

Regression for "if I refresh the page I have to log back in" (which also made the
profile page unreachable). The session token used to live only in memory, so a
full reload dropped it. The fix persists it to localStorage and RESUMEs on load.

Drives the real stack (orchestrator + persistence + auth + served client + real
WebSocket) so the RESUME round-trip is exercised end to end.

Playwright async API only (see feedback_playwright_async_only memory).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from playwright.async_api import Page, expect

from mahjong.engine.rulesets import MANIFEST
from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account
from mahjong.server.orchestrator import MultiTableOrchestrator
from mahjong.web import static_root

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


@pytest_asyncio.fixture
async def account_server(
    tmp_path: Path,
) -> AsyncIterator[tuple[MultiTableOrchestrator, str, str]]:
    """Orchestrator with one ready-to-log-in human account."""
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(
        p._conn,  # type: ignore[attr-defined]
        username="connorl",
        display_name="ConnorL",
        kind="human",
        role="user",
        password="hunter2hunter2",
    )
    orch = MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=1,
        server_info={"version": "session-e2e", "git_sha": "x", "host": "t"},
        static_dir=static_root(),
        persistence=p,
    )
    await orch.start()
    try:
        yield orch, "connorl", "hunter2hunter2"
    finally:
        await orch.close()


async def _login(page: Page, url: str, username: str, password: str) -> None:
    await page.goto(url)
    await page.fill('input[name="username"]', username)
    await page.fill('input[name="password"]', password)
    await page.click("button.auth-submit")
    # The feedback launcher only renders once a session token is set.
    await expect(page.locator(".launcher")).to_be_visible(timeout=10_000)


async def test_reload_keeps_session_no_relogin(
    page: Page, account_server: tuple[MultiTableOrchestrator, str, str]
) -> None:
    orch, username, password = account_server
    url = f"http://127.0.0.1:{orch.port}/"

    await _login(page, url, username, password)
    # Sanity: the token was persisted for the reload to pick up.
    token = await page.evaluate("() => localStorage.getItem('mahjong.session_token')")
    assert token, "expected a session token in localStorage after login"

    # --- the actual bug: refresh the page --------------------------------------
    await page.reload()

    # We must land back in the app WITHOUT the login form: RESUME re-auths us.
    await expect(page.locator(".launcher")).to_be_visible(timeout=10_000)
    await expect(page.locator(".auth-overlay")).to_have_count(0)
    await expect(page.locator('input[name="username"]')).to_have_count(0)
    # The profile button (gated on the session) is reachable again.
    await expect(page.locator("button.theme-btn", has_text="profile")).to_be_visible()


async def test_stale_token_falls_back_to_login(
    page: Page, account_server: tuple[MultiTableOrchestrator, str, str]
) -> None:
    """A garbage stored token must not wedge the client: RESUME is rejected, the
    token is cleared, and the login form is shown."""
    orch, _username, _password = account_server
    url = f"http://127.0.0.1:{orch.port}/"

    await page.goto(url)
    await page.evaluate("() => localStorage.setItem('mahjong.session_token', 'tok_bogus_not_real')")
    await page.reload()

    # RESUME fails → login form appears, and the dead token is cleared so it
    # won't be retried on the next reconnect.
    await expect(page.locator('input[name="username"]')).to_be_visible(timeout=10_000)
    leftover = await page.evaluate("() => localStorage.getItem('mahjong.session_token')")
    assert leftover is None, f"stale token should be cleared; got {leftover!r}"
