"""Admin CLI for account management.

Spec: docs/specs/auth.md § Account creation,
      docs/specs/public-deployment.md § 24.2 (invite codes).

Usage:
    python -m mahjong account create --username alice --display "Alice" [--admin]
    python -m mahjong account list
    python -m mahjong account invite create [--max-uses N] [--expires-days D]
    python -m mahjong account invite list
    python -m mahjong account invite revoke <code>

Password is read from stdin (with prompt) so it never appears in shell history
or ``ps`` output.  Use ``--password-stdin`` in non-interactive contexts.
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import sys
import time

from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account
from mahjong.persistence.invites import get_invite, mint_invite, set_invite_disabled
from mahjong.server.config import load_config_from_env


def _iso(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm:  ")
    if pw != confirm:
        print("passwords do not match", file=sys.stderr)
        sys.exit(2)
    return pw


def _open_persistence() -> Persistence:
    cfg, _ = load_config_from_env()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.records_dir.mkdir(parents=True, exist_ok=True)
    return Persistence(cfg.db_path, cfg.data_dir)


def _cmd_create(args: argparse.Namespace) -> int:
    pw = _read_password(args)
    role = "admin" if args.admin else "user"
    kind = args.kind
    p = _open_persistence()
    try:
        try:
            account_id = create_account(
                p._conn,
                username=args.username,
                display_name=args.display or args.username,
                kind=kind,
                role=role,
                password=pw,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"created account_id={account_id} username={args.username} "
            f"kind={kind} role={role}"
        )
        return 0
    finally:
        p.close()


def _cmd_list(args: argparse.Namespace) -> int:
    p = _open_persistence()
    try:
        rows = p._conn.execute(
            "SELECT account_id, username, display_name, kind, role, disabled "
            "FROM accounts ORDER BY account_id"
        ).fetchall()
        if not rows:
            print("(no accounts)")
            return 0
        print(f"{'id':>4}  {'username':<20} {'kind':<6} {'role':<6} {'disabled':<8} display")
        for r in rows:
            print(
                f"{r['account_id']:>4}  {r['username']:<20} "
                f"{r['kind']:<6} {r['role']:<6} "
                f"{('yes' if r['disabled'] else 'no'):<8} {r['display_name']}"
            )
        return 0
    finally:
        p.close()


def _resolve_admin_id(p: Persistence) -> int | None:
    """The lowest-id admin account, or None if there is no admin yet."""
    row = p._conn.execute(
        "SELECT account_id FROM accounts WHERE role = 'admin' ORDER BY account_id LIMIT 1"
    ).fetchone()
    return row["account_id"] if row is not None else None


def _cmd_invite_create(args: argparse.Namespace) -> int:
    p = _open_persistence()
    try:
        created_by = args.created_by
        if created_by is None:
            created_by = _resolve_admin_id(p)
            if created_by is None:
                print(
                    "error: no admin account to attribute the invite to; "
                    "create one with `account create --admin` first",
                    file=sys.stderr,
                )
                return 1
        now = int(time.time() * 1000)
        expires = (
            None if args.expires_days <= 0 else now + args.expires_days * 86_400_000
        )
        code = mint_invite(
            p._conn,
            created_by=created_by,
            created_at_ms=now,
            max_uses=args.max_uses,
            expires_at_ms=expires,
        )
        when = "never" if expires is None else _iso(expires)
        print(f"created invite code={code} max_uses={args.max_uses} expires={when}")
        return 0
    finally:
        p.close()


def _cmd_invite_list(args: argparse.Namespace) -> int:
    p = _open_persistence()
    try:
        rows = p._conn.execute(
            "SELECT code, created_by, expires_at_ms, max_uses, used_count, disabled "
            "FROM invites ORDER BY created_at_ms DESC"
        ).fetchall()
        if not rows:
            print("(no invites)")
            return 0
        print(f"{'code':<24} {'uses':<8} {'expires':<22} {'disabled':<8} by")
        for r in rows:
            uses = f"{r['used_count']}/{r['max_uses']}"
            expires = "never" if r["expires_at_ms"] is None else _iso(r["expires_at_ms"])
            print(
                f"{r['code']:<24} {uses:<8} {expires:<22} "
                f"{('yes' if r['disabled'] else 'no'):<8} {r['created_by']}"
            )
        return 0
    finally:
        p.close()


def _cmd_invite_revoke(args: argparse.Namespace) -> int:
    p = _open_persistence()
    try:
        if get_invite(p._conn, args.code) is None:
            print(f"error: no such invite: {args.code}", file=sys.stderr)
            return 1
        set_invite_disabled(p._conn, args.code, True)
        p._conn.commit()
        print(f"revoked invite code={args.code}")
        return 0
    finally:
        p.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mahjong account")
    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create", help="create an account")
    create.add_argument("--username", required=True)
    create.add_argument("--display", default=None, help="display name (default: username)")
    create.add_argument(
        "--kind",
        default="human",
        choices=["human", "bot"],
        help="account kind (default human)",
    )
    create.add_argument("--admin", action="store_true", help="grant admin role")
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="read password from stdin instead of prompting",
    )
    create.set_defaults(func=_cmd_create)

    lst = sub.add_parser("list", help="list accounts")
    lst.set_defaults(func=_cmd_list)

    invite = sub.add_parser("invite", help="manage invite codes")
    inv_sub = invite.add_subparsers(dest="invite_cmd", required=True)

    inv_create = inv_sub.add_parser("create", help="mint an invite code")
    inv_create.add_argument(
        "--max-uses", type=int, default=1, help="redemptions allowed (default 1)"
    )
    inv_create.add_argument(
        "--expires-days",
        type=int,
        default=7,
        help="days until expiry; 0 = never (default 7)",
    )
    inv_create.add_argument(
        "--created-by",
        type=int,
        default=None,
        help="admin account_id to attribute it to (default: first admin)",
    )
    inv_create.set_defaults(func=_cmd_invite_create)

    inv_list = inv_sub.add_parser("list", help="list invite codes")
    inv_list.set_defaults(func=_cmd_invite_list)

    inv_revoke = inv_sub.add_parser("revoke", help="disable an invite code")
    inv_revoke.add_argument("code", help="the invite code to revoke")
    inv_revoke.set_defaults(func=_cmd_invite_revoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
