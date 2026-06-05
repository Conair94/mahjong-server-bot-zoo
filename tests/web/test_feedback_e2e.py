"""End-to-end browser feedback flow (Spec 23 — the § 23.4 browser-verify gate).

Drives the *real* stack through a headless browser: a running
``MultiTableOrchestrator`` with persistence + auth, the served static client,
and a real WebSocket.  Steps:

  1. sign in through the auth form,
  2. click [feedback], pick a type, type a suggestion, submit,
  3. observe the "Thank you" confirmation in the UI,
  4. assert the sanitised report file landed in ``data_dir/reports/``.

This pins the app.js glue (``_onFeedbackSubmit`` → ``FEEDBACK`` frame, and the
``FEEDBACK_ACK`` → child ``onResult`` routing) that the isolated component test
(test_feedback_component.py) and the server-side integration test
(tests/server/test_feedback_integration.py) do not jointly cover.
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
from mahjong.server.orchestrator import MultiTableOrchestrator
from mahjong.web import static_root

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


@pytest_asyncio.fixture
async def auth_server(tmp_path: Path) -> AsyncIterator[tuple[MultiTableOrchestrator, Path]]:
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(
        p._conn,  # type: ignore[attr-defined]
        username="alice",
        display_name="Alice",
        kind="human",
        role="user",
        password="alicealice",
    )
    orch = MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=1,
        server_info={"version": "fb-e2e", "git_sha": "x", "host": "t"},
        static_dir=static_root(),
        persistence=p,
    )
    await orch.start()
    try:
        yield orch, tmp_path
    finally:
        await orch.close()


async def test_feedback_e2e_login_submit_writes_file(
    page: Page, auth_server: tuple[MultiTableOrchestrator, Path]
) -> None:
    orch, data_dir = auth_server
    url = f"http://127.0.0.1:{orch.port}/"

    await page.goto(url)

    # --- sign in -------------------------------------------------------------
    await page.fill('input[name="username"]', "alice")
    await page.fill('input[name="password"]', "alicealice")
    await page.click('button.auth-submit')

    # --- feedback button appears once authed (token set) ---------------------
    launcher = page.locator(".launcher")
    await expect(launcher).to_be_visible(timeout=10_000)

    await launcher.click()

    # --- fill + submit -------------------------------------------------------
    await page.select_option("select#fb-type", "feature")
    await page.fill(
        "textarea#fb-text", "Please add a colour-blind friendly tile palette."
    )
    await page.click("button.act")

    # --- confirmation in UI --------------------------------------------------
    # The confirmation auto-closes after ~1.4s (Spec 29 Bug E); Playwright
    # resolves to_contain_text as soon as it first matches, well within that.
    await expect(page.locator(".done")).to_contain_text("Feedback received", timeout=10_000)

    # --- file landed on disk -------------------------------------------------
    reports_dir = data_dir / "reports"

    async def _wait_for_report() -> Path:
        for _ in range(50):
            files = list(reports_dir.glob("*.txt"))
            if files:
                return files[0]
            await asyncio.sleep(0.05)
        raise AssertionError("no report file written within timeout")

    report = await _wait_for_report()
    content = report.read_text()
    assert "type: feature" in content
    assert "submitter: Alice" in content
    assert "Please add a colour-blind friendly tile palette." in content
    assert report.name.endswith("_feature.txt")
