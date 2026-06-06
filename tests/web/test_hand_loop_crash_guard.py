"""FB-01: a crashed hand task must surface gracefully, not hang the table.

Spec: docs/specs/live-play-bugfixes.md (FB-01 — concealed-gang hang).

WebOrchestrator._run_hand_loop runs the hand in a background task. Before the
guard, *any* unhandled exception in that loop died silently: clients received no
HAND_END and no error — just a frozen last frame (an indefinite "hang") — and the
record truncated mid-hand. That is the exact signature of the ConnorL concealed-gang
report (record dead-stops with no terminal).

The investigation proved run_hand's own game logic is sound (engine/projection/diff/
client reducer+renderer all clean; every await in run_hand is timeout-bounded and
every exception caught). The remaining gap is the *surrounding* loop. This test pins
the guard: on a crash the loop logs and tears the table down
(``sessions.shutdown(reason="hand_aborted")``) and completes — instead of
propagating out of the task and leaving clients frozen.
"""

from __future__ import annotations

from typing import Any

import pytest

from mahjong.adapters.base import HumanIdentity
from mahjong.engine.rulesets import MANIFEST
from mahjong.table import manager as mgr
from mahjong.web.server import WebOrchestrator

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _identity(_conn: Any = None) -> HumanIdentity:
    return {"kind": "human", "user_id": "u_fb01", "display": "FB01"}


async def test_hand_loop_crash_tears_down_gracefully(tmp_path, monkeypatch):
    orch = WebOrchestrator(
        ruleset=MCR_REF,
        seed=1,
        hand_id="fb01-hand",
        record_path=tmp_path / "record.jsonl",
        server_info={"version": "t", "git_sha": "t", "host": "t"},
        identity_factory=_identity,
        max_hands=1,
    )

    # Spy on the graceful-teardown path.
    shutdown_calls: list[str] = []

    async def _spy_shutdown(*, reason: str = "server_shutdown") -> None:
        shutdown_calls.append(reason)

    monkeypatch.setattr(orch._sessions, "shutdown", _spy_shutdown)

    # Force the hand to crash the way an unforeseen live edge would.
    async def _boom(**_kw: Any) -> None:
        raise RuntimeError("boom in run_hand")

    monkeypatch.setattr(mgr, "run_hand", _boom)

    # Pre-guard this RuntimeError propagated out of _run_hand_loop (the task died
    # silently); the guard must surface-and-teardown instead, so this awaits cleanly.
    await orch._run_hand_loop(_identity())

    assert orch._match_done.is_set()
    assert shutdown_calls == ["hand_aborted"]
