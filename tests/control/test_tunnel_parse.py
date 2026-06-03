"""Pure (sync) tunnel-parsing tests — the ``tunnel_url_parse`` contract.

Kept separate from test_tunnel.py (which is asyncio-marked) so these sync tests
don't trip pytest-asyncio's "marked async but isn't" warning.
"""

from __future__ import annotations

from mahjong.control.tunnel import cloudflared_argv, parse_tunnel_url

# A real boxed line cloudflared prints for a quick tunnel.
_RECORDED = (
    "2026-06-03T12:00:00Z INF |  "
    "https://calm-tree-1234.trycloudflare.com                          |"
)


def test_parse_extracts_trycloudflare_url() -> None:
    assert parse_tunnel_url(_RECORDED) == "https://calm-tree-1234.trycloudflare.com"


def test_parse_ignores_unrelated_lines() -> None:
    assert parse_tunnel_url("2026-06-03T12:00:00Z INF Starting tunnel") is None
    assert parse_tunnel_url("just some text") is None


def test_cloudflared_argv_targets_the_loopback_server() -> None:
    argv = cloudflared_argv("http://127.0.0.1:8400", binary="cloudflared")
    assert argv == ["cloudflared", "tunnel", "--url", "http://127.0.0.1:8400"]
