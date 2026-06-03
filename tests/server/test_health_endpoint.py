"""Step 8.8.a — `/health` liveness endpoint.

Spec: docs/specs/server-lifecycle.md § Health endpoint + fixtures 9, 10, 11.

The wire-level `/health` *hook* is already covered in tests/wire/test_server.py;
this pins the *handler logic*: 200 healthy, 503 draining, 500 on DB stall, plus
the orchestrator wiring (a real GET over HTTP) and `persistence.ping`.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from mahjong.persistence import Persistence
from mahjong.server.health import build_health_payload, make_health_handler

# --- fakes -----------------------------------------------------------------


class _FakeRegistry:
    def __init__(self, *, accepting: bool, n_tables: int, drain_at: float | None = None):
        self._accepting = accepting
        self._n = n_tables
        self.drain_started_monotonic = drain_at

    @property
    def accepting_new(self) -> bool:
        return self._accepting

    def list_tables(self) -> list[object]:
        return [object()] * self._n


class _OkPersistence:
    def ping(self) -> None:
        return None


class _DeadPersistence:
    def ping(self) -> None:
        raise RuntimeError("disk I/O error")


# --- build_health_payload --------------------------------------------------


def test_healthy_returns_200_with_documented_shape() -> None:
    status, payload = build_health_payload(
        registry=_FakeRegistry(accepting=True, n_tables=3),
        persistence=_OkPersistence(),
        started_at_monotonic=0.0,
        server_id="mahjong-server-0.1.0",
        shutdown_timeout_s=30.0,
    )
    assert status == 200
    assert payload["status"] == "ok"
    assert payload["server_id"] == "mahjong-server-0.1.0"
    assert payload["tables"] == 3
    assert isinstance(payload["uptime_s"], int)
    assert payload["uptime_s"] >= 0


def test_uptime_is_monotonic_nonnegative() -> None:
    # started "in the past" → uptime is a positive integer.
    import time

    status, payload = build_health_payload(
        registry=_FakeRegistry(accepting=True, n_tables=0),
        persistence=_OkPersistence(),
        started_at_monotonic=time.monotonic() - 5.0,
        server_id="x",
        shutdown_timeout_s=30.0,
    )
    assert status == 200
    assert payload["uptime_s"] >= 5


def test_draining_returns_503() -> None:
    import time

    status, payload = build_health_payload(
        registry=_FakeRegistry(
            accepting=False, n_tables=1, drain_at=time.monotonic()
        ),
        persistence=_OkPersistence(),
        started_at_monotonic=0.0,
        server_id="x",
        shutdown_timeout_s=30.0,
    )
    assert status == 503
    assert payload["status"] == "draining"
    assert 0 <= payload["drain_remaining_s"] <= 30


def test_draining_does_not_ping_db() -> None:
    # During drain we must NOT touch the DB (it may be mid-checkpoint/close).
    status, _payload = build_health_payload(
        registry=_FakeRegistry(accepting=False, n_tables=0, drain_at=None),
        persistence=_DeadPersistence(),  # would raise if pinged
        started_at_monotonic=0.0,
        server_id="x",
        shutdown_timeout_s=30.0,
    )
    assert status == 503


def test_db_stall_returns_500_with_db_reason() -> None:
    status, payload = build_health_payload(
        registry=_FakeRegistry(accepting=True, n_tables=0),
        persistence=_DeadPersistence(),
        started_at_monotonic=0.0,
        server_id="x",
        shutdown_timeout_s=30.0,
    )
    assert status == 500
    assert payload["status"] == "unhealthy"
    assert "db" in payload["reason"].lower()


def test_handler_returns_json_bytes() -> None:
    handler = make_health_handler(
        registry=_FakeRegistry(accepting=True, n_tables=2),
        persistence=_OkPersistence(),
        started_at_monotonic=0.0,
        server_id="mahjong-server-0.1.0",
        shutdown_timeout_s=30.0,
    )
    status, body = handler()
    assert status == 200
    parsed = json.loads(body)
    assert parsed["status"] == "ok"
    assert parsed["tables"] == 2


# --- persistence.ping ------------------------------------------------------


def test_persistence_ping_succeeds_on_healthy_db(tmp_path) -> None:
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    try:
        p.ping()  # must not raise
    finally:
        p.close()


def test_persistence_ping_raises_after_close(tmp_path) -> None:
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    p.close()
    with pytest.raises(sqlite3.ProgrammingError):
        p.ping()
