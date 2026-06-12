"""AdminDataService — async wrapper over the (sync) persistence layer.

Spec: docs/specs/admin-console.md § 2 (persistence operations), steps 6-7.

The persistence layer is synchronous SQLite; the control plane is async.  Every
DB call therefore runs in the default executor so it never blocks the event loop
(the project-wide sync-DB-via-run_in_executor convention).  Methods return
JSON-ready dicts for the wire; password hashes are never included.
"""

from __future__ import annotations

import asyncio
import datetime
import time
from typing import Any

from mahjong.persistence import Account, InviteRow, Persistence
from mahjong.persistence.auth import create_account


def _invite_to_wire(iv: InviteRow) -> dict[str, Any]:
    expires_iso = (
        None
        if iv.expires_at_ms is None
        else datetime.datetime.fromtimestamp(iv.expires_at_ms / 1000, tz=datetime.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )
    return {
        "code": iv.code,
        "max_uses": iv.max_uses,
        "used_count": iv.used_count,
        "expires_at_ms": iv.expires_at_ms,
        "expires_iso": expires_iso,
        "disabled": iv.disabled,
        "created_by": iv.created_by,
    }


def _account_to_wire(a: Account) -> dict[str, Any]:
    # NB: password_hash is deliberately omitted.
    return {
        "account_id": a.account_id,
        "username": a.username,
        "display_name": a.display_name,
        "kind": a.kind,
        "role": a.role,
        "disabled": a.disabled,
        "last_login_ms": a.last_login_ms,
    }


class AdminDataService:
    def __init__(self, persistence: Persistence) -> None:
        self._p = persistence

    async def _run(self, fn: Any, *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    # --- invites ---

    async def list_invites(self) -> list[dict[str, Any]]:
        rows = await self._run(self._p.list_invites)
        return [_invite_to_wire(iv) for iv in rows]

    async def create_invite(
        self, *, max_uses: int = 1, expires_days: int = 7
    ) -> list[dict[str, Any]]:
        def _do() -> None:
            created_by = self._first_admin_id()
            now = int(time.time() * 1000)
            expires = None if expires_days <= 0 else now + expires_days * 86_400_000
            self._p.mint_invite(created_by=created_by, max_uses=max_uses, expires_at_ms=expires)

        await self._run(_do)
        return await self.list_invites()

    async def revoke_invite(self, code: str) -> list[dict[str, Any]]:
        await self._run(self._p.revoke_invite, code)
        return await self.list_invites()

    # --- accounts ---

    async def list_accounts(self) -> list[dict[str, Any]]:
        rows = await self._run(self._p.list_accounts)
        return [_account_to_wire(a) for a in rows]

    async def create_account(
        self, *, username: str, display_name: str, password: str, admin: bool = False
    ) -> list[dict[str, Any]]:
        def _do() -> None:
            create_account(
                self._p._conn,
                username=username,
                display_name=display_name or username,
                kind="human",
                role="admin" if admin else "user",
                password=password,
            )

        await self._run(_do)
        return await self.list_accounts()

    async def set_account_disabled(self, account_id: int, disabled: bool) -> list[dict[str, Any]]:
        await self._run(self._p.set_account_disabled, account_id, disabled)
        return await self.list_accounts()

    async def set_account_role(self, account_id: int, role: str) -> list[dict[str, Any]]:
        await self._run(self._p.set_account_role, account_id, role)
        return await self.list_accounts()

    # --- helpers ---

    def _first_admin_id(self) -> int:
        for a in self._p.list_accounts():
            if a.role == "admin":
                return a.account_id
        raise ValueError("no admin account to attribute the invite to")


__all__ = ["AdminDataService"]
