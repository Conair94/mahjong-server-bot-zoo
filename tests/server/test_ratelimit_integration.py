"""Rate-limit wiring through the orchestrator — public-deployment.md § 24.3.

Fixtures 17-20: the limiter is consulted on the right surfaces, keyed on the
real client IP, and the login check short-circuits *before* the argon2 verify.
The limiter's own window/budget mechanics are unit-tested in test_ratelimit.py;
these tests pin the wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.engine.rulesets import MANIFEST
from mahjong.persistence import Persistence
from mahjong.persistence.accounts import get_account_by_username
from mahjong.persistence.auth import create_account
from mahjong.persistence.invites import get_invite, mint_invite
from mahjong.server.orchestrator import MultiTableOrchestrator

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _orch(
    tmp_path: Path, p: Persistence, *, trust_proxy: bool = False
) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=1,
        server_info={"version": "ratelimit-test", "git_sha": "x", "host": "t"},
        persistence=p,
        trust_proxy=trust_proxy,
    )


def _persistence(tmp_path: Path) -> Persistence:
    (tmp_path / "records").mkdir(exist_ok=True)
    return Persistence(tmp_path / "db.sqlite", tmp_path)


# ---------------------------------------------------------------------------
# Fixture 17: login throttle short-circuits before the argon2 verify
# ---------------------------------------------------------------------------


async def test_login_over_budget_short_circuits_before_verify(tmp_path: Path) -> None:
    p = _persistence(tmp_path)
    create_account(
        p._conn,  # type: ignore[attr-defined]
        username="alice",
        display_name="Alice",
        kind="human",
        role="user",
        password="alicealice",
    )
    orch = _orch(tmp_path, p)

    # Pre-exhaust the loopback IP's failure budget.
    for _ in range(10):
        orch._login_limiter.record("127.0.0.1")

    # Spy: the verify must NOT run when the IP is already throttled.
    calls: list[tuple[str, str]] = []
    real = orch._run_auth_request

    def spy(username: str, password: str) -> Any:
        calls.append((username, password))
        return real(username, password)

    orch._run_auth_request = spy  # type: ignore[method-assign]

    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()  # HELLO
            await ws.send(
                json.dumps(
                    {"kind": "AUTH_REQUEST", "username": "alice", "password": "whatever1"}
                )
            )
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "ERROR"
            assert resp["code"] == "rate_limited"
        assert calls == [], "argon2 verify must not run for a throttled IP"
    finally:
        await orch.close()
        p.close()


# ---------------------------------------------------------------------------
# Fixture 18: successful logins do not consume the failure budget
# ---------------------------------------------------------------------------


async def test_successful_login_does_not_consume_budget(tmp_path: Path) -> None:
    p = _persistence(tmp_path)
    create_account(
        p._conn,  # type: ignore[attr-defined]
        username="alice",
        display_name="Alice",
        kind="human",
        role="user",
        password="alicealice",
    )
    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()
            await ws.send(
                json.dumps(
                    {"kind": "AUTH_REQUEST", "username": "alice", "password": "alicealice"}
                )
            )
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["ok"] is True

        # The budget is untouched — a success never records, never even creates
        # a key (would_allow peeks without inserting).
        assert orch._login_limiter.would_allow("127.0.0.1") is True
        assert orch._login_limiter.active_keys() == 0
    finally:
        await orch.close()
        p.close()


# ---------------------------------------------------------------------------
# Fixture 20: REGISTER over budget → rate_limited, invite untouched
# ---------------------------------------------------------------------------


async def test_register_over_budget_rate_limited(tmp_path: Path) -> None:
    p = _persistence(tmp_path)
    admin = create_account(
        p._conn,  # type: ignore[attr-defined]
        username="rootadmin",
        display_name="Root",
        kind="human",
        role="admin",
        password="adminpw123",
    )
    code = mint_invite(p._conn, created_by=admin, created_at_ms=1)  # type: ignore[attr-defined]
    orch = _orch(tmp_path, p)

    for _ in range(5):
        orch._register_limiter.record("127.0.0.1")

    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()
            await ws.send(
                json.dumps(
                    {
                        "kind": "REGISTER",
                        "username": "dave",
                        "password": "davepw12345",
                        "display_name": "Dave",
                        "invite_code": code,
                    }
                )
            )
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "ERROR"
            assert resp["code"] == "rate_limited"

        # Throttled before redeem → no account, invite pristine.
        assert get_account_by_username(p._conn, "dave") is None  # type: ignore[attr-defined]
        assert get_invite(p._conn, code).used_count == 0  # type: ignore[attr-defined]
    finally:
        await orch.close()
        p.close()


# ---------------------------------------------------------------------------
# Fixture 19: budgets are per client IP (end-to-end via CF-Connecting-IP)
# ---------------------------------------------------------------------------


async def test_register_budget_is_per_client_ip(tmp_path: Path) -> None:
    p = _persistence(tmp_path)
    admin = create_account(
        p._conn,  # type: ignore[attr-defined]
        username="rootadmin",
        display_name="Root",
        kind="human",
        role="admin",
        password="adminpw123",
    )
    code = mint_invite(p._conn, created_by=admin, created_at_ms=1)  # type: ignore[attr-defined]
    orch = _orch(tmp_path, p, trust_proxy=True)

    # Exhaust budget for one forwarded IP only.
    for _ in range(5):
        orch._register_limiter.record("10.0.0.1")

    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        # Same IP as the exhausted one → throttled.
        async with websockets.connect(
            url,
            subprotocols=["mahjong-v1"],
            additional_headers={"CF-Connecting-IP": "10.0.0.1"},
        ) as ws:
            await ws.recv()
            await ws.send(
                json.dumps(
                    {
                        "kind": "REGISTER",
                        "username": "dave",
                        "password": "davepw12345",
                        "display_name": "Dave",
                        "invite_code": code,
                    }
                )
            )
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["code"] == "rate_limited"

        # A different forwarded IP has its own budget → registration succeeds.
        async with websockets.connect(
            url,
            subprotocols=["mahjong-v1"],
            additional_headers={"CF-Connecting-IP": "10.0.0.2"},
        ) as ws:
            await ws.recv()
            await ws.send(
                json.dumps(
                    {
                        "kind": "REGISTER",
                        "username": "dave",
                        "password": "davepw12345",
                        "display_name": "Dave",
                        "invite_code": code,
                    }
                )
            )
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "AUTH_RESPONSE"
            assert resp["ok"] is True
    finally:
        await orch.close()
        p.close()
