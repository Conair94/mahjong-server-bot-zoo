"""Step 8.8.f — SIGKILL crash-recovery reconciliation (server-lifecycle.md
fixture 16).

A `SIGKILL` gives the server no chance to run its drain, so a hand that was
mid-flight leaves two artifacts on disk:

- a ``hand_index`` row with NULL terminals (``reserve_hand`` ran at HEADER time;
  ``finalize_hand`` never ran), and
- a record file with a HEADER and some events but no FOOTER.

On the next startup the server must reconcile the orphaned row to ``ABORTED``
(fixture 8 semantics) *without touching the record file* — an operator can run
``rebuild-index`` later if they want to reclaim the orphan.  Fixture 22 (the S3
gate) exercises this indirectly via a real SIGTERM; this test pins the
reconciliation step in isolation, which is the load-bearing invariant ("a crash
does not corrupt state").
"""

from __future__ import annotations

from pathlib import Path

from mahjong.cli.serve import _mark_in_progress_aborted
from mahjong.persistence import Participant, Persistence
from mahjong.persistence.auth import create_account


def _reserve_crashed_hand(persistence: Persistence, data_dir: Path) -> str:
    """Reserve a hand and write a partial (HEADER-no-FOOTER) record file,
    simulating a process killed mid-hand.  Returns the relative record path."""
    account_id = create_account(
        persistence._conn,  # type: ignore[attr-defined]
        username="alice",
        display_name="Alice",
        kind="human",
        role="user",
        password="alicealice",
    )
    hand_id = "h_crash_0001"
    record_rel = "records/t1/hand_crash.jsonl"
    record_abs = data_dir / record_rel
    record_abs.parent.mkdir(parents=True, exist_ok=True)
    # HEADER + a couple of events, deliberately no FOOTER line.
    record_abs.write_text(
        '{"kind":"HEADER","hand_id":"h_crash_0001"}\n'
        '{"kind":"DRAW","seat":0}\n'
        '{"kind":"DISCARD","seat":0,"tile":"1m"}\n'
    )
    persistence.reserve_hand(
        hand_id=hand_id,
        match_id="match_t1",
        hand_index_in_match=0,
        ruleset_id="mcr-2006",
        ruleset_config_hash="testhash",
        started_at_ms=1_000_000,
        master_seed="seed-crash",
        record_path=record_rel,
        server_version="test",
        source="live",
        participants=[
            Participant(0, account_id, "human", "F1", None),
            Participant(1, None, "canned", "F2", None),
            Participant(2, None, "canned", "F3", None),
            Participant(3, None, "canned", "F4", None),
        ],
    )
    return record_rel


def test_in_progress_hand_is_aborted_on_restart(tmp_path: Path) -> None:
    data_dir = tmp_path
    persistence = Persistence(data_dir / "mahjong.db", data_dir)
    try:
        record_rel = _reserve_crashed_hand(persistence, data_dir)
        record_abs = data_dir / record_rel
        record_before = record_abs.read_bytes()

        # Precondition: the orphaned row is visible as in-progress (NULL terminals).
        in_progress = persistence.find_in_progress_hands()
        assert len(in_progress) == 1
        assert in_progress[0].terminal_kind is None
        assert in_progress[0].ended_at_ms is None

        # The restart reconciliation step.
        count = _mark_in_progress_aborted(persistence)
        assert count == 1

        # Row is now finalised ABORTED with zero score deltas, no winner.
        row = persistence.get_hand("h_crash_0001")
        assert row is not None
        assert row.terminal_kind == "ABORTED"
        assert row.ended_at_ms is not None
        assert row.winner_seat is None
        # A hand killed mid-flight was never scored, so the recovery path leaves
        # score-deltas NULL ("never computed") rather than 0 ("scored zero").
        # NULL is SUM-neutral in SQLite, so leaderboard aggregates are unaffected.
        # (This differs from the runtime ABORTED path, which has live participant
        # state and writes explicit zeros.)
        assert all(p.final_score_delta is None for p in row.participants)

        # No longer in-progress.
        assert persistence.find_in_progress_hands() == []

        # The partial record file is left byte-for-byte untouched.
        assert record_abs.read_bytes() == record_before
    finally:
        persistence.close()


def test_reconciliation_is_idempotent_and_noop_when_clean(tmp_path: Path) -> None:
    """A clean restart (no in-progress rows) finalises nothing; a second pass
    after recovery is a no-op."""
    data_dir = tmp_path
    persistence = Persistence(data_dir / "mahjong.db", data_dir)
    try:
        assert _mark_in_progress_aborted(persistence) == 0

        _reserve_crashed_hand(persistence, data_dir)
        assert _mark_in_progress_aborted(persistence) == 1
        # Second pass: already ABORTED, nothing left in-progress.
        assert _mark_in_progress_aborted(persistence) == 0
    finally:
        persistence.close()
