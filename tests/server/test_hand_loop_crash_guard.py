"""FB-01 (live path): a crashed hand task on TableHandle must not silently hang.

Sibling to tests/web/test_hand_loop_crash_guard.py, but for the *live* multi-table
loop (``TableHandle._run_hand_loop`` in mahjong/server/registry.py) — the path the
deployed server actually runs. Same gap: ``try/while/finally`` with no ``except``
let an unhandled exception kill the background task silently (frozen clients, no
HAND_END, truncated record). This pins the guard: crash → log + graceful
``sessions.shutdown(reason="hand_aborted")`` + clean completion, not propagation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.types import RuleSetRef
from mahjong.server import registry as registry_mod
from mahjong.server.registry import TableHandle
from mahjong.server.seats import SeatComposition

pytestmark = pytest.mark.asyncio

_MCR: RuleSetRef = cast(
    RuleSetRef, {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}
)
_SEATS = (
    SeatComposition("human"),
    SeatComposition("bot"),
    SeatComposition("bot"),
    SeatComposition("bot"),
)


async def test_live_hand_loop_crash_tears_down_gracefully(tmp_path: Path, monkeypatch):
    handle = TableHandle(
        table_id="77",
        ruleset=_MCR,
        seed=1,
        hand_id="t77-h0",
        record_path=tmp_path / "hand_0000.jsonl",
        server_info={"version": "test", "git_sha": "test", "host": "test"},
        seats=_SEATS,
    )

    shutdown_calls: list[str] = []

    async def _spy_shutdown(*, reason: str = "server_shutdown") -> None:
        shutdown_calls.append(reason)

    monkeypatch.setattr(handle._sessions, "shutdown", _spy_shutdown)

    async def _boom(**_kw: Any) -> None:
        raise RuntimeError("boom in run_hand")

    monkeypatch.setattr(registry_mod.mgr, "run_hand", _boom)

    # Pre-guard the RuntimeError propagated out (task died silently); the guard
    # surfaces-and-tears-down instead, so this awaits cleanly.
    await handle._run_hand_loop()

    assert handle._match_done.is_set()
    assert shutdown_calls == ["hand_aborted"]
