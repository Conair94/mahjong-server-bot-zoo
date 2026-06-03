"""Token-gated admin status surface for the live server.

Spec: docs/specs/admin-console.md § 1 (`serve` admin-status endpoint).

The control console (Spec 25) needs to read the server's *in-memory* state — which
tables are live, who is seated — because that state never reaches the DB.  This
module provides:

- ``build_admin_status_payload`` — the JSON payload, projected straight off
  ``TableRegistry.list_tables()`` (reuse the existing wire projection; no new shape).
- ``make_admin_status_handler`` — a ``(Authorization-header) -> (status, body)``
  closure that enforces a ``Bearer`` token before disclosing anything.

The handler is mounted on the public listener (same mechanism as ``/health``) and
the listener may be exposed through the Cloudflare tunnel, so the token is the only
thing standing between the internet and this data.  The comparison is constant-time
(``hmac.compare_digest``) so an attacker can't recover the token byte-by-byte from
response timing.
"""

from __future__ import annotations

import hmac
import json
import time
from collections.abc import Callable
from typing import Any, Protocol

# (status_code, body_bytes) — matches the HealthHandler convention.
AdminStatusHandler = Callable[["str | None"], "tuple[int, bytes]"]


class _RegistryLike(Protocol):
    """Just the slice of ``TableRegistry`` the payload builder consumes."""

    def list_tables(self) -> list[Any]: ...


def build_admin_status_payload(
    *,
    registry: _RegistryLike,
    started_at_monotonic: float,
    listen_addr: str,
) -> dict[str, Any]:
    """Project the live registry into the ``/admin/status`` JSON payload.

    ``players_connected`` counts *distinct* user_ids on occupied human seats — a
    v1 approximation that excludes spectators and lobby-idle connections (see
    spec Open question 3).  Uptime uses the monotonic clock: it measures elapsed
    time and is immune to wall-clock/NTP adjustments.
    """
    summaries = list(registry.list_tables())
    tables = [s.to_wire() for s in summaries]

    user_ids: set[str] = set()
    for table in tables:
        for seat in table.get("seats", []):
            if seat.get("kind") == "human" and seat.get("occupied") and seat.get("user_id"):
                user_ids.add(seat["user_id"])

    return {
        "uptime_s": int(time.monotonic() - started_at_monotonic),
        "listen_addr": listen_addr,
        "players_connected": len(user_ids),
        "tables": tables,
    }


def make_admin_status_handler(
    *,
    token: str,
    registry: _RegistryLike,
    started_at_monotonic: float,
    listen_addr: str,
) -> AdminStatusHandler:
    """Build the ``/admin/status`` request handler bound to *token* + *registry*.

    Returns a closure taking the request's ``Authorization`` header value and
    returning ``(status, body)``: ``401`` unless the header is exactly
    ``Bearer <token>`` (constant-time check), else ``200`` with the status JSON.
    """
    expected = f"Bearer {token}"

    def handler(authorization: str | None) -> tuple[int, bytes]:
        if authorization is None or not hmac.compare_digest(authorization, expected):
            return 401, b'{"error":"unauthorized"}'
        payload = build_admin_status_payload(
            registry=registry,
            started_at_monotonic=started_at_monotonic,
            listen_addr=listen_addr,
        )
        return 200, json.dumps(payload, separators=(",", ":")).encode("utf-8")

    return handler


__all__ = [
    "AdminStatusHandler",
    "build_admin_status_payload",
    "make_admin_status_handler",
]
