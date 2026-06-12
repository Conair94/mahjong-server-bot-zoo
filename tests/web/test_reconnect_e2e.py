"""WebSocket auto-reconnect — the client must recover from a dropped socket.

Before this fix, ConnectionManager opened the WebSocket once and never
reconnected: a transient drop (tunnel warm-up, Wi-Fi↔cellular handoff, sleep)
permanently bricked the session with "Failed to send" until a manual reload.

This drives the real stack through a headless browser: register (token stored),
then force-close the client's WebSocket to simulate a network drop, and assert
the client opens a NEW socket and silently re-authenticates via RESUME — landing
back in an authed lobby without the user touching anything.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from playwright.async_api import Page, expect

from mahjong.engine.rulesets import MANIFEST
from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account
from mahjong.persistence.invites import mint_invite
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
) -> AsyncIterator[tuple[MultiTableOrchestrator, str]]:
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
    code = mint_invite(p._conn, created_by=admin, created_at_ms=1, max_uses=5)  # type: ignore[attr-defined]
    orch = MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=1,
        server_info={"version": "reconnect-e2e", "git_sha": "x", "host": "t"},
        static_dir=static_root(),
        persistence=p,
    )
    await orch.start()
    try:
        yield orch, code
    finally:
        await orch.close()


async def test_client_reconnects_and_reauths_after_drop(
    page: Page, invite_server: tuple[MultiTableOrchestrator, str]
) -> None:
    orch, code = invite_server
    url = f"http://127.0.0.1:{orch.port}/"

    # Track every WebSocket the page opens, and the frames on each.
    sockets: list[Any] = []
    frames: list[tuple[int, str, str]] = []

    def on_ws(ws: Any) -> None:
        idx = len(sockets)
        sockets.append(ws)
        ws.on("framesent", lambda p: frames.append((idx, "sent", str(p))))
        ws.on("framereceived", lambda p: frames.append((idx, "recv", str(p))))

    page.on("websocket", on_ws)

    await page.goto(url)

    # Register → logged in (launcher visible once a token is set).
    await page.click("a.auth-toggle-link")
    await page.fill('input[name="username"]', "dave")
    await page.fill('input[name="display_name"]', "Dave")
    await page.fill('input[name="password"]', "davepw12345")
    await page.fill('input[name="invite_code"]', code)
    await page.click("button.auth-submit")
    await expect(page.locator(".launcher")).to_be_visible(timeout=10_000)

    assert len(sockets) == 1, "exactly one socket before the drop"

    # Simulate a network drop: force-close the client's WebSocket.
    await page.evaluate("document.querySelector('mahjong-app')._conn.ws.close()")

    # The client should open a SECOND socket and re-auth via RESUME, with no
    # user action — and stay logged in (launcher never disappears for good).
    for _ in range(60):
        if len(sockets) >= 2:
            break
        await asyncio.sleep(0.25)
    assert len(sockets) >= 2, "client did not open a new socket after the drop"

    # On the new socket: a RESUME was sent and a successful AUTH_RESPONSE came back.
    new_sent = [f.replace(" ", "") for (i, d, f) in frames if i >= 1 and d == "sent"]
    new_recv = [f.replace(" ", "") for (i, d, f) in frames if i >= 1 and d == "recv"]
    assert any(
        "RESUME" in f for f in new_sent
    ), f"no RESUME re-auth on the reconnected socket; sent={new_sent}"
    assert any(
        '"AUTH_RESPONSE"' in f and '"ok":true' in f for f in new_recv
    ), f"no successful AUTH_RESPONSE on reconnect; recv={new_recv}"

    # Still authed: the feedback launcher (token-gated) is visible again.
    await expect(page.locator(".launcher")).to_be_visible(timeout=10_000)
