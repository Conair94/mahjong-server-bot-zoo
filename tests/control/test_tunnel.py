"""TunnelSupervisor — cloudflared quick-tunnel control + URL scraping.

Spec: docs/specs/admin-console.md § 2 (TunnelSupervisor) + fixture
``tunnel_url_parse``.

``parse_tunnel_url`` is the pure contract (a recorded cloudflared line → the
public URL); the supervisor wraps a child process around it.  The not-found path
is checked against a deliberately missing binary so the GUI gets a clear error
rather than a stack trace when cloudflared isn't installed.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from mahjong.control.tunnel import TunnelSupervisor

# NB: the pure parse/argv tests live in test_tunnel_parse.py — keeping them out of
# this asyncio-marked module avoids the pytest-asyncio "sync test marked async"
# warning (the project's documented pytest-asyncio mode quirk).
pytestmark = pytest.mark.asyncio


async def test_missing_binary_yields_cloudflared_not_found() -> None:
    tun = TunnelSupervisor(argv=["definitely-not-a-real-binary-xyz", "tunnel"])
    wire = await tun.start()
    assert wire["running"] is False
    assert wire["url"] is None
    assert wire["error"] == "cloudflared_not_found"


async def test_start_scrapes_url_then_stop_clears_it() -> None:
    # A fake "cloudflared": emit the boxed URL line, then idle until killed.
    script = (
        "import sys, time\n"
        "sys.stderr.write('INF |  https://fake-tunnel-9.trycloudflare.com  |\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(30)\n"
    )
    tun = TunnelSupervisor(argv=[sys.executable, "-c", script], url_timeout_s=5.0)
    wire = await tun.start()
    assert wire["running"] is True
    assert wire["url"] == "https://fake-tunnel-9.trycloudflare.com"
    assert wire["error"] is None

    await tun.stop()
    after = tun.to_wire()
    assert after["running"] is False
    assert after["url"] is None


async def test_url_updates_after_cloudflared_reconnect() -> None:
    """A quick tunnel re-registers with a NEW hostname when its edge connection
    drops; the supervisor must surface the *current* URL rather than pinning the
    first one. Pinning the first is the live bug that left the console showing a
    dead (NXDOMAIN) trycloudflare URL after a reconnect."""
    script = (
        "import sys, time\n"
        "sys.stderr.write('INF |  https://first-url-1.trycloudflare.com  |\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(0.3)\n"
        "sys.stderr.write('INF Connection terminated, reconnecting...\\n')\n"
        "sys.stderr.write('INF |  https://second-url-2.trycloudflare.com  |\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(30)\n"
    )
    tun = TunnelSupervisor(argv=[sys.executable, "-c", script], url_timeout_s=5.0)
    wire = await tun.start()
    assert wire["url"] == "https://first-url-1.trycloudflare.com"

    # The drain loop should pick up the re-registered URL within a beat.
    try:
        async with asyncio.timeout(5.0):
            while tun.to_wire()["url"] == "https://first-url-1.trycloudflare.com":
                await asyncio.sleep(0.05)
    finally:
        current = tun.to_wire()["url"]
        await tun.stop()
    assert current == "https://second-url-2.trycloudflare.com"
