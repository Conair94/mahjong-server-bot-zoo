"""AdminDataService over a real temp DB + ControlPlane invite/account dispatch.

Spec: docs/specs/admin-console.md § 2, steps 6-7, fixtures ctl_invite_ops /
ctl_account_ops.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mahjong.control.plane import ControlPlane
from mahjong.control.services import AdminDataService
from mahjong.control.supervisor import ServerState
from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account, handle_auth_request

pytestmark = pytest.mark.asyncio


def _persistence(tmp_path: Path) -> Persistence:
    return Persistence(str(tmp_path / "mj.db"), tmp_path)


def _seed_admin(p: Persistence) -> None:
    create_account(
        p._conn,
        username="root",
        display_name="Root",
        kind="human",
        role="admin",
        password="pw-12345678",
    )


# --- AdminDataService against a real DB ---


async def test_invite_create_list_revoke_roundtrip(tmp_path: Path) -> None:
    p = _persistence(tmp_path)
    _seed_admin(p)
    svc = AdminDataService(p)
    try:
        invites = await svc.create_invite(max_uses=3, expires_days=7)
        assert len(invites) == 1
        code = invites[0]["code"]
        assert invites[0]["max_uses"] == 3
        assert invites[0]["disabled"] is False

        after = await svc.revoke_invite(code)
        assert after[0]["disabled"] is True
    finally:
        p.close()


async def test_account_create_makes_loginable_account(tmp_path: Path) -> None:
    p = _persistence(tmp_path)
    svc = AdminDataService(p)
    try:
        accounts = await svc.create_account(
            username="alice", display_name="Alice", password="pw-12345678"
        )
        assert any(a["username"] == "alice" for a in accounts)
        # No password hash leaks to the wire.
        assert all("password_hash" not in a for a in accounts)
        # The created account can actually authenticate.
        result = handle_auth_request(p._conn, "alice", "pw-12345678")
        assert result.ok
    finally:
        p.close()


async def test_account_set_role_and_disabled(tmp_path: Path) -> None:
    p = _persistence(tmp_path)
    svc = AdminDataService(p)
    try:
        await svc.create_account(username="bob", display_name="Bob", password="pw-12345678")
        bob_id = (await svc.list_accounts())[0]["account_id"]
        accounts = await svc.set_account_role(bob_id, "admin")
        assert accounts[0]["role"] == "admin"
        accounts = await svc.set_account_disabled(bob_id, True)
        assert accounts[0]["disabled"] is True
    finally:
        p.close()


# --- ControlPlane dispatch (fake service) ---


class _FakeSup:
    state = ServerState.STOPPED
    pid = None
    started_at_monotonic = None


class _FakeMetrics:
    latest = None


class _FakeData:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_invites(self) -> list[dict]:
        self.calls.append("list_invites")
        return [{"code": "inv_x"}]

    async def create_invite(self, *, max_uses: int, expires_days: int) -> list[dict]:
        self.calls.append(f"create_invite:{max_uses}:{expires_days}")
        return [{"code": "inv_new"}]

    async def list_accounts(self) -> list[dict]:
        self.calls.append("list_accounts")
        return [{"username": "root"}]


def _plane(data: object | None) -> ControlPlane:
    async def fetch() -> dict | None:
        return None

    return ControlPlane(
        supervisor=_FakeSup(),  # type: ignore[arg-type]
        metrics=_FakeMetrics(),  # type: ignore[arg-type]
        admin_status_fetch=fetch,
        server_listen_url="ws://x:1",
        data=data,
    )


async def test_invite_create_command_returns_invite_list() -> None:
    data = _FakeData()
    plane = _plane(data)
    reply = await plane.handle_command({"kind": "INVITE_CREATE", "max_uses": 5, "expires_days": 3})
    assert reply["kind"] == "INVITE_LIST"
    assert reply["invites"] == [{"code": "inv_new"}]
    assert "create_invite:5:3" in data.calls


async def test_accounts_list_command() -> None:
    plane = _plane(_FakeData())
    reply = await plane.handle_command({"kind": "ACCOUNTS_LIST"})
    assert reply["kind"] == "ACCOUNT_LIST"
    assert reply["accounts"] == [{"username": "root"}]


async def test_data_command_without_service_errors() -> None:
    plane = _plane(None)
    reply = await plane.handle_command({"kind": "INVITES_LIST"})
    assert reply["kind"] == "ERROR"
    assert reply["code"] == "data_unavailable"
