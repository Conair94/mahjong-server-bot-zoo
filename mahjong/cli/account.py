"""Admin CLI for account management.

Spec: docs/specs/auth.md § Account creation.

Usage:
    python -m mahjong account create --username alice --display "Alice" [--admin]
    python -m mahjong account list

Password is read from stdin (with prompt) so it never appears in shell history
or ``ps`` output.  Use ``--password-stdin`` in non-interactive contexts.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account
from mahjong.server.config import load_config_from_env


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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
