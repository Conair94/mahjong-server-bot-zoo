"""CLI entry points (play-test, selfplay, ...).

Spec: docs/specs/implementation-order.md.
"""


def main() -> int:
    """Project CLI entry point. Subcommands are wired as they come online."""
    import sys

    print("mahjong CLI scaffolded; no subcommands implemented yet.", file=sys.stderr)
    print("See docs/specs/implementation-order.md for the build sequence.", file=sys.stderr)
    return 0
