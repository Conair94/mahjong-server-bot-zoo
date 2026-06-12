"""Client-IP resolution behind a trusted proxy — public-deployment.md § 24.1.

Fixtures 1-4 pin the spoof-resistant rule: trust the proxy's CF-Connecting-IP
header ONLY when trust_proxy is on AND the TCP peer is loopback (i.e. the
request actually came through the local cloudflared). The loopback-peer check
is what makes the header unspoofable — a remote attacker can't both present a
loopback peer address and reach a loopback-bound listener.
"""

from __future__ import annotations

from mahjong.wire.server import Connection, resolve_client_ip


def test_fixture1_loopback_peer_header_trust_on_returns_forwarded() -> None:
    assert (
        resolve_client_ip(peer_host="127.0.0.1", forwarded_for="203.0.113.7", trust_proxy=True)
        == "203.0.113.7"
    )


def test_fixture2_loopback_peer_header_trust_off_returns_peer() -> None:
    assert (
        resolve_client_ip(peer_host="127.0.0.1", forwarded_for="203.0.113.7", trust_proxy=False)
        == "127.0.0.1"
    )


def test_fixture3_nonloopback_peer_with_header_returns_peer_not_header() -> None:
    # Spoof defense: a direct (non-tunnel) connection cannot set its own client IP.
    assert (
        resolve_client_ip(peer_host="203.0.113.9", forwarded_for="203.0.113.7", trust_proxy=True)
        == "203.0.113.9"
    )


def test_fixture4_loopback_peer_no_header_returns_peer() -> None:
    assert (
        resolve_client_ip(peer_host="127.0.0.1", forwarded_for=None, trust_proxy=True)
        == "127.0.0.1"
    )


def test_ipv6_loopback_peer_with_header_returns_forwarded() -> None:
    assert (
        resolve_client_ip(peer_host="::1", forwarded_for="203.0.113.7", trust_proxy=True)
        == "203.0.113.7"
    )


def test_forwarded_value_is_stripped() -> None:
    assert (
        resolve_client_ip(peer_host="127.0.0.1", forwarded_for="  203.0.113.7  ", trust_proxy=True)
        == "203.0.113.7"
    )


def test_missing_peer_host_falls_back_to_sentinel() -> None:
    assert resolve_client_ip(peer_host=None, forwarded_for=None, trust_proxy=False) == "unknown"


def test_connection_exposes_client_ip() -> None:
    """The resolved IP is stored on the Connection for the step-5 rate limiter."""
    conn = Connection(0, object(), client_ip="203.0.113.7")  # type: ignore[arg-type]
    assert conn.client_ip == "203.0.113.7"


def test_connection_client_ip_defaults_to_sentinel() -> None:
    conn = Connection(0, object())  # type: ignore[arg-type]
    assert conn.client_ip == "unknown"
