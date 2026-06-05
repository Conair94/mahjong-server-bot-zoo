"""Unit tests for the token-gated admin status surface.

Spec: docs/specs/admin-console.md § 1 (`serve` admin-status endpoint).

These are pure-logic tests (no sockets): the status *payload* builder and the
Bearer-token *handler* are exercised directly.  The transport wiring (the
``/admin/status`` HTTP route) is covered separately in ``tests/wire/test_server.py``.

Sync on purpose — kept out of the asyncio-marked files per the pytest-asyncio
mode quirk (see feedback_pytest_asyncio_mode_quirk.md).
"""

from __future__ import annotations

import json

from mahjong.server.admin_status import (
    build_admin_status_payload,
    make_admin_status_handler,
)
from mahjong.server.registry import SeatSummary, TableSummary


class _FakeRegistry:
    """Stand-in exposing only ``list_tables`` — all the builder consumes."""

    def __init__(self, tables: list[TableSummary]) -> None:
        self._tables = tables

    def list_tables(self) -> list[TableSummary]:
        return self._tables


def _table(table_id: str, phase: str, seats: list[SeatSummary]) -> TableSummary:
    return TableSummary(
        table_id=table_id,
        ruleset="mcr-2006",
        hand_index=3,
        phase=phase,
        seats=tuple(seats),
    )


def _two_table_registry() -> _FakeRegistry:
    """One IN_PROGRESS table with two occupied humans + one bot + one empty seat,
    and one WAITING table with a single occupied human."""
    t1 = _table(
        "1",
        "IN_PROGRESS",
        [
            SeatSummary(seat=0, kind="human", occupied=True, user_id="u_7"),
            SeatSummary(seat=1, kind="human", occupied=True, user_id="u_9"),
            SeatSummary(seat=2, kind="bot", occupied=True, bot_id="v0"),
            SeatSummary(seat=3, kind="human", occupied=False),
        ],
    )
    t2 = _table(
        "2",
        "WAITING_FOR_PLAYERS",
        [
            SeatSummary(seat=0, kind="human", occupied=True, user_id="u_4"),
            SeatSummary(seat=1, kind="human", occupied=False),
            SeatSummary(seat=2, kind="human", occupied=False),
            SeatSummary(seat=3, kind="bot", occupied=True, bot_id="v0"),
        ],
    )
    return _FakeRegistry([t1, t2])


# --- payload builder ---


def test_payload_projects_registry_tables() -> None:
    reg = _two_table_registry()
    payload = build_admin_status_payload(
        registry=reg, started_at_monotonic=100.0, listen_addr="0.0.0.0:8400"
    )
    assert payload["listen_addr"] == "0.0.0.0:8400"
    # tables[] is exactly the registry's to_wire() projection (reuse, no new shape).
    assert payload["tables"] == [s.to_wire() for s in reg.list_tables()]
    assert payload["tables"][0]["phase"] == "IN_PROGRESS"


def test_payload_counts_distinct_occupied_humans() -> None:
    """players_connected = distinct user_ids on occupied human seats (bots and
    empty seats excluded)."""
    reg = _two_table_registry()
    payload = build_admin_status_payload(
        registry=reg, started_at_monotonic=0.0, listen_addr="x:1"
    )
    assert payload["players_connected"] == 3  # u_7, u_9, u_4


def test_payload_uptime_is_monotonic_delta(monkeypatch) -> None:
    import mahjong.server.admin_status as mod

    monkeypatch.setattr(mod.time, "monotonic", lambda: 250.0)
    payload = build_admin_status_payload(
        registry=_FakeRegistry([]), started_at_monotonic=100.0, listen_addr="x:1"
    )
    assert payload["uptime_s"] == 150


# --- token handler ---


def _handler():
    return make_admin_status_handler(
        token="s3cret-token",
        registry=_two_table_registry(),
        started_at_monotonic=0.0,
        listen_addr="0.0.0.0:8400",
    )


def test_handler_accepts_correct_bearer_token() -> None:
    status, body = _handler()("Bearer s3cret-token")
    assert status == 200
    payload = json.loads(body)
    assert payload["players_connected"] == 3
    assert len(payload["tables"]) == 2


def test_handler_rejects_wrong_token() -> None:
    status, _body = _handler()("Bearer wrong")
    assert status == 401


def test_handler_rejects_missing_authorization() -> None:
    status, _body = _handler()(None)
    assert status == 401


def test_handler_rejects_malformed_authorization() -> None:
    # No "Bearer " prefix.
    status, _body = _handler()("s3cret-token")
    assert status == 401
