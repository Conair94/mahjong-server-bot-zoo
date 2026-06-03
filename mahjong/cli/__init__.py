"""CLI entry points (play-test, selfplay, ...).

Spec: docs/specs/implementation-order.md.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Project CLI entry point — dispatches to subcommands."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _usage()
        return 0
    sub = args.pop(0)
    if sub == "play-test":
        from mahjong.cli.play_test import main as play_test_main

        return play_test_main(args)
    if sub == "selfplay":
        from mahjong.cli.selfplay import main as selfplay_main

        return selfplay_main(args)
    if sub == "account":
        from mahjong.cli.account import main as account_main

        return account_main(args)
    if sub == "serve":
        from mahjong.cli.serve import main as serve_main

        return serve_main(args)
    if sub == "control":
        from mahjong.cli.control import main as control_main

        return control_main(args)
    print(f"unknown subcommand: {sub!r}", file=sys.stderr)
    _usage()
    return 1


def _usage() -> None:
    print("usage: mahjong <subcommand> [args...]", file=sys.stderr)
    print("subcommands:", file=sys.stderr)
    print("  play-test    drive one hand with four canned seats", file=sys.stderr)
    print("  selfplay     headless N-hand self-play between bots", file=sys.stderr)
    print("  account      account management (create | list)", file=sys.stderr)
    print("  serve        run the WebSocket mahjong server", file=sys.stderr)
    print("  control      run the admin control console (supervises serve)", file=sys.stderr)
