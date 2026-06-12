"""End-to-end browser registration flow (public-deployment.md § 24.2, fixture 21).

Drives the real stack through a headless browser: a running
``MultiTableOrchestrator`` with persistence + auth, the served static client,
and a real WebSocket. Two paths:

  1. Happy: toggle to Register, fill username/display/password/invite, submit,
     observe auto-login into the lobby, assert the account + spent invite
     landed in the DB.
  2. Negative control: a bad invite shows the inline error and does NOT log in.

This pins the app.js glue (the login↔register mode toggle, the ``REGISTER``
frame, and the ``ERROR register_rejected`` → inline-error routing) that the
server-side tests (tests/server/test_auth_wire.py) don't cover.

Playwright async API only (pytest-playwright's sync fixtures install a foreign
event loop — see feedback_playwright_async_only memory).
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
from mahjong.persistence.accounts import get_account_by_username
from mahjong.persistence.auth import create_account
from mahjong.persistence.invites import get_invite, mint_invite
from mahjong.server.orchestrator import MultiTableOrchestrator
from mahjong.web import static_root

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


@pytest_asyncio.fixture
async def invite_server(
    tmp_path: Path,
) -> AsyncIterator[tuple[MultiTableOrchestrator, Persistence, str]]:
    """Orchestrator with an admin account and one fresh single-use invite."""
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    admin = create_account(
        p._conn,  # type: ignore[attr-defined]
        username="rootadmin",
        display_name="Root",
        kind="human",
        role="admin",
        password="adminpw123",
    )
    code = mint_invite(p._conn, created_by=admin, created_at_ms=1)  # type: ignore[attr-defined]
    orch = MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=1,
        server_info={"version": "register-e2e", "git_sha": "x", "host": "t"},
        static_dir=static_root(),
        persistence=p,
    )
    await orch.start()
    try:
        yield orch, p, code
    finally:
        await orch.close()


async def test_register_e2e_valid_invite_auto_logs_in(
    page: Page, invite_server: tuple[MultiTableOrchestrator, Persistence, str]
) -> None:
    orch, p, code = invite_server
    url = f"http://127.0.0.1:{orch.port}/"

    await page.goto(url)

    # --- switch to the register form -----------------------------------------
    await page.click("a.auth-toggle-link")
    await expect(page.locator(".auth-title")).to_contain_text("Create account")

    # --- fill + submit -------------------------------------------------------
    await page.fill('input[name="username"]', "dave")
    await page.fill('input[name="display_name"]', "Dave")
    await page.fill('input[name="password"]', "davepw12345")
    await page.fill('input[name="invite_code"]', code)
    await page.click("button.auth-submit")

    # --- auto-login: the feedback launcher only appears once a token is set ---
    await expect(page.locator(".launcher")).to_be_visible(timeout=10_000)

    # --- account + invite landed in the DB -----------------------------------
    acct = get_account_by_username(p._conn, "dave")  # type: ignore[attr-defined]
    assert acct is not None
    assert acct.display_name == "Dave"
    assert acct.kind == "human"
    assert get_invite(p._conn, code).used_count == 1  # type: ignore[attr-defined]


async def test_register_e2e_bad_invite_shows_error_and_does_not_log_in(
    page: Page, invite_server: tuple[MultiTableOrchestrator, Persistence, str]
) -> None:
    orch, p, _code = invite_server
    url = f"http://127.0.0.1:{orch.port}/"

    await page.goto(url)
    await page.click("a.auth-toggle-link")

    await page.fill('input[name="username"]', "mallory")
    await page.fill('input[name="display_name"]', "Mallory")
    await page.fill('input[name="password"]', "mallorypw123")
    await page.fill('input[name="invite_code"]', "inv_not_a_real_code")
    await page.click("button.auth-submit")

    # The server's generic register_rejected message surfaces inline...
    await expect(page.locator(".auth-error")).to_be_visible(timeout=10_000)
    # ...and we are NOT logged in (no token → no feedback launcher).
    await expect(page.locator(".launcher")).to_have_count(0)
    # No account was created.
    assert get_account_by_username(p._conn, "mallory") is None  # type: ignore[attr-defined]
