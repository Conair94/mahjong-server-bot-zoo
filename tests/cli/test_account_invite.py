"""`account invite` CLI — public-deployment.md § 24.2.

The operator path that makes invite registration usable: mint a code, list,
revoke. Without this CLI the registration feature has no way to issue invites.
Driven through ``account.main()`` against a temp data dir (MAHJONG_DATA_DIR).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mahjong.cli import account
from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account
from mahjong.persistence.invites import get_invite


def _seed_admin(data_dir: Path) -> None:
    (data_dir / "records").mkdir(parents=True, exist_ok=True)
    p = Persistence(data_dir / "mahjong.db", data_dir)
    try:
        create_account(
            p._conn,  # type: ignore[attr-defined]
            username="root",
            display_name="Root",
            kind="human",
            role="admin",
            password="adminpw123",
        )
    finally:
        p.close()


def _open(data_dir: Path) -> Persistence:
    return Persistence(data_dir / "mahjong.db", data_dir)


def test_invite_create_requires_an_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MAHJONG_DATA_DIR", str(tmp_path))
    rc = account.main(["invite", "create"])
    assert rc == 1
    assert "no admin" in capsys.readouterr().err


def test_invite_create_list_revoke_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MAHJONG_DATA_DIR", str(tmp_path))
    _seed_admin(tmp_path)

    # mint
    assert account.main(["invite", "create", "--max-uses", "3", "--expires-days", "0"]) == 0
    out = capsys.readouterr().out
    assert "created invite code=inv_" in out
    code = out.split("code=")[1].split()[0]

    # persisted with the requested shape (multi-use, never-expiring)
    p = _open(tmp_path)
    try:
        inv = get_invite(p._conn, code)  # type: ignore[attr-defined]
        assert inv is not None
        assert inv.max_uses == 3
        assert inv.expires_at_ms is None
        assert inv.disabled is False
    finally:
        p.close()

    # list shows it
    assert account.main(["invite", "list"]) == 0
    assert code in capsys.readouterr().out

    # revoke disables it
    assert account.main(["invite", "revoke", code]) == 0
    p = _open(tmp_path)
    try:
        assert get_invite(p._conn, code).disabled is True  # type: ignore[attr-defined]
    finally:
        p.close()

    # revoking a bogus code errors
    assert account.main(["invite", "revoke", "inv_not_real"]) == 1


def test_invite_create_default_is_single_use_with_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MAHJONG_DATA_DIR", str(tmp_path))
    _seed_admin(tmp_path)
    assert account.main(["invite", "create"]) == 0
    code = capsys.readouterr().out.split("code=")[1].split()[0]
    p = _open(tmp_path)
    try:
        inv = get_invite(p._conn, code)  # type: ignore[attr-defined]
        assert inv.max_uses == 1
        assert inv.expires_at_ms is not None  # 7-day default expiry
    finally:
        p.close()
