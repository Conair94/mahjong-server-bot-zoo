"""Unauthenticated `/health` liveness endpoint.

Spec: docs/specs/server-lifecycle.md § Health endpoint (fixtures 9, 10, 11).

A fast, dependency-aware probe for systemd ``Restart=on-failure`` predicates,
the Cloudflare-tunnel health checker, and a LAN operator running ``curl``.  It
is mounted on the public listener (same mechanism as ``/admin/status``) but,
unlike that route, requires no auth — it exposes only counts and version
strings, no per-user or table-content data (threat model: the LAN is trusted).

Distinct from the admin console's Health *pane*, which reads the token-gated
``/admin/status``.  This is the plain liveness signal for an uptime checker.

Status codes:

- ``200`` — process up, DB responsive (``SELECT 1`` succeeded), accepting.
- ``503`` — shutdown drain in progress (``!registry.accepting_new``).  Lets a
  load balancer stop sending new traffic.  Does *not* ping the DB (it may be
  mid-checkpoint/close).
- ``500`` — the DB ping raised.  Process is up but unhealthy; an operator (or
  systemd) should restart.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, Protocol

# (status_code, body_bytes) — matches the HealthHandler convention in
# mahjong.wire.server.
HealthHandler = Callable[[], "tuple[int, bytes]"]


class _RegistryLike(Protocol):
    """The slice of ``TableRegistry`` the health payload consumes."""

    @property
    def accepting_new(self) -> bool: ...

    def list_tables(self) -> list[Any]: ...

    # Monotonic timestamp of when drain began, or None if not draining.
    drain_started_monotonic: float | None


class _PingableLike(Protocol):
    def ping(self) -> None: ...


def build_health_payload(
    *,
    registry: _RegistryLike,
    persistence: _PingableLike,
    started_at_monotonic: float,
    server_id: str,
    shutdown_timeout_s: float,
) -> tuple[int, dict[str, Any]]:
    """Compute ``(status_code, payload_dict)`` for a ``/health`` probe.

    Drain is checked *before* the DB ping: during shutdown the DB may be
    mid-checkpoint, and a draining server is reported as 503 regardless.
    """
    if not registry.accepting_new:
        drain_at = registry.drain_started_monotonic
        if drain_at is None:
            remaining = int(shutdown_timeout_s)
        else:
            elapsed = time.monotonic() - drain_at
            remaining = max(0, int(shutdown_timeout_s - elapsed))
        return 503, {"status": "draining", "drain_remaining_s": remaining}

    try:
        persistence.ping()
    except Exception as exc:  # any failure to reach the DB is "unhealthy"
        return 500, {"status": "unhealthy", "reason": f"db: {exc}"}

    return 200, {
        "status": "ok",
        "server_id": server_id,
        "tables": len(registry.list_tables()),
        "uptime_s": int(time.monotonic() - started_at_monotonic),
    }


def make_health_handler(
    *,
    registry: _RegistryLike,
    persistence: _PingableLike,
    started_at_monotonic: float,
    server_id: str,
    shutdown_timeout_s: float,
) -> HealthHandler:
    """Build the ``/health`` request handler.  Returns a zero-arg closure
    yielding ``(status, json_body_bytes)``."""

    def handler() -> tuple[int, bytes]:
        status, payload = build_health_payload(
            registry=registry,
            persistence=persistence,
            started_at_monotonic=started_at_monotonic,
            server_id=server_id,
            shutdown_timeout_s=shutdown_timeout_s,
        )
        return status, json.dumps(payload, separators=(",", ":")).encode("utf-8")

    return handler


__all__ = ["HealthHandler", "build_health_payload", "make_health_handler"]
