"""Test infrastructure for the web client (Playwright async API).

Provides:

- `FakeWireServer` — a `WebSocketServer` wrapper running on the test's own
  asyncio loop. Pushes outbound frames via an `asyncio.Queue` and captures
  inbound frames into a list. Spec calls this `fake_wire_server`
  (`docs/specs/tui-client.md` §Testing).
- `browser` / `browser_context` / `page` async fixtures backed by
  `playwright.async_api`. We don't use `pytest-playwright`'s sync fixtures
  because they install a foreign asyncio loop that conflicts with
  `pytest-asyncio` for the rest of the suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

# The web-client suite drives a real browser via Playwright's async API. CI
# installs only the runtime + lint/type deps (no playwright, no browser
# binaries), so skip the whole tests/web tree there instead of erroring at
# collection. Local dev with playwright installed runs it normally. Wiring
# browser E2E into CI is deferred — see DEF-22 in docs/specs/feedback-backlog.md.
pytest.importorskip("playwright")

import pytest_asyncio  # noqa: E402
from playwright.async_api import (  # noqa: E402
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)

from mahjong.web import static_root  # noqa: E402
from mahjong.wire.server import Connection, WebSocketServer  # noqa: E402


class FakeWireServer:
    """Scripted server: outbound queue + inbound capture, real WS transport."""

    def __init__(self) -> None:
        self._server: WebSocketServer | None = None
        self._port: int = 0
        self._outbound: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._inbound: list[dict[str, Any]] = []
        self._connected = asyncio.Event()
        self._new_inbound = asyncio.Event()

    # --- lifecycle ---

    async def start(self) -> None:
        self._server = WebSocketServer(
            host="127.0.0.1",
            port=0,
            handler=self._handler,
            static_dir=static_root(),
        )
        await self._server.start()
        self._port = self._server.port

    async def stop(self) -> None:
        if self._server is not None:
            await self._server.close()

    # --- public ---

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/"

    @property
    def port(self) -> int:
        return self._port

    @property
    def inbound(self) -> list[dict[str, Any]]:
        return list(self._inbound)

    async def send(self, frame: dict[str, Any], *, timeout: float = 5.0) -> None:
        """Queue an outbound frame; awaits the first client connection."""
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        await self._outbound.put(frame)

    async def wait_for_inbound(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Await an inbound frame matching `predicate`."""

        async def _scan() -> dict[str, Any]:
            while True:
                for msg in self._inbound:
                    if predicate(msg):
                        return msg
                self._new_inbound.clear()
                await self._new_inbound.wait()

        return await asyncio.wait_for(_scan(), timeout=timeout)

    # --- handler internals ---

    async def _handler(self, conn: Connection) -> None:
        self._connected.set()
        send_task = asyncio.create_task(self._drain_outbound(conn))
        try:
            async for msg in conn:
                self._inbound.append(msg)
                self._new_inbound.set()
        finally:
            send_task.cancel()

    async def _drain_outbound(self, conn: Connection) -> None:
        while True:
            frame = await self._outbound.get()
            if frame is None:
                return
            await conn.send(frame)


# --- pytest fixtures (async) ---


@pytest_asyncio.fixture
async def fake_wire_server() -> AsyncIterator[FakeWireServer]:
    server = FakeWireServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            yield browser
        finally:
            await browser.close()


@pytest_asyncio.fixture
async def browser_context(browser: Browser) -> AsyncIterator[BrowserContext]:
    context = await browser.new_context()
    try:
        yield context
    finally:
        await context.close()


@pytest_asyncio.fixture
async def page(browser_context: BrowserContext) -> AsyncIterator[Page]:
    page = await browser_context.new_page()
    try:
        yield page
    finally:
        await page.close()
