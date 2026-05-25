"""Persistence API tests — Step 8.3.

Spec: docs/specs/persistence-api.md (fixtures 1-13).
Also covers sqlite-schema.md fixtures 11-12 (hand round-trip, rebuild from records).

All tests use an in-memory SQLite DB + tmp_path for record files via the
``Persistence`` class.  File operations (integrity_check, rebuild) need real
paths so they use tmp_path.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from mahjong.persistence import Persistence
from mahjong.persistence.models import Participant

# ---------------------------------------------------------------------------
# Record-file helpers
# ---------------------------------------------------------------------------


_CANNED_SEATS = [
    {"seat": i, "wind": f"F{i + 1}", "identity": {"kind": "canned", "script": "pass"}}
    for i in range(4)
]

_CANNED_PARTICIPANTS = [
    Participant(seat=i, account_id=None, seat_kind="canned", wind=f"F{i + 1}", final_score_delta=None)
    for i in range(4)
]


def _make_header_json(
    hand_id: str,
    match_id: str | None = None,
    hand_index: int = 0,
    ts: str = "2026-01-01T00:00:00.000Z",
) -> str:
    return json.dumps(
        {
            "event": "HEADER",
            "seq": 0,
            "turn_index": 0,
            "phase": "DEAL",
            "ts": ts,
            "hand_id": hand_id,
            "match_id": match_id,
            "hand_index_in_match": hand_index,
            "ruleset": {"id": "mcr-2006", "config_hash": "abc123"},
            "seed": "12345",
            "seats": _CANNED_SEATS,
            "server": {"version": "0.0.1"},
            "meta": {"master_seed": "0xdeadbeef", "source": "selfplay"},
        },
        separators=(",", ":"),
    )


def _make_hand_end_json(
    seq: int = 1,
    ts: str = "2026-01-01T00:00:59.000Z",
) -> str:
    return json.dumps(
        {
            "event": "HAND_END",
            "seq": seq,
            "turn_index": 1,
            "phase": "TERMINAL",
            "ts": ts,
            "kind": "HU",
            "winner": [0],
            "win_tile": "J1",
            "win_type": "SELF_DRAW",
            "deal_in_seat": None,
            "fan": [{"name": "TestFan", "value": 8}],
            "fan_total": 8,
            "score_delta": [24, -8, -8, -8],
        },
        separators=(",", ":"),
    )


def _compute_checksum(content_before_footer: str) -> str:
    """sha256 over all content before the FOOTER line."""
    return "sha256:" + hashlib.sha256(content_before_footer.encode("utf-8")).hexdigest()


def _make_record_file(
    data_dir: Path,
    hand_id: str,
    match_id: str | None = None,
    hand_index: int = 0,
    *,
    with_footer: bool = True,
    with_hand_end: bool = True,
    tamper_header: bool = False,
    ts_header: str = "2026-01-01T00:00:00.000Z",
    ts_footer: str = "2026-01-01T00:01:00.000Z",
) -> tuple[str, str | None]:
    """Create a JSONL record file under data_dir/records/.

    Returns ``(record_path_relative_to_data_dir, checksum_or_None)``.
    ``checksum_or_None`` is the FOOTER.checksum value; None for no-footer files.
    """
    records_dir = data_dir / "records"
    records_dir.mkdir(exist_ok=True)
    record_rel_path = f"records/{hand_id}.jsonl"

    header_line = _make_header_json(hand_id, match_id, hand_index, ts=ts_header)
    if tamper_header:
        # Corrupt the header after computing the right checksum — simulated at
        # write time so the stored checksum won't match.
        pass  # handled below

    lines_before_footer = [header_line]
    if with_hand_end and with_footer:
        lines_before_footer.append(_make_hand_end_json(seq=1))

    content_before_footer = "\n".join(lines_before_footer) + "\n"
    checksum = _compute_checksum(content_before_footer)

    if with_footer:
        footer = json.dumps(
            {
                "event": "FOOTER",
                "seq": len(lines_before_footer),
                "turn_index": 1,
                "phase": "TERMINAL",
                "ts": ts_footer,
                "event_count": len(lines_before_footer) + 1,
                "checksum": checksum,
            },
            separators=(",", ":"),
        )

        if tamper_header:
            # Write a tampered version of the header (checksum already computed
            # from the clean version above, so the stored checksum won't match
            # what's actually in the file now).
            tampered_header = header_line.replace("0xdeadbeef", "0xcafebabe")
            lines_before_footer[0] = tampered_header
            content_before_footer_tampered = "\n".join(lines_before_footer) + "\n"
            content = content_before_footer_tampered + footer + "\n"
        else:
            content = content_before_footer + footer + "\n"

        (data_dir / record_rel_path).write_text(content, encoding="utf-8")
        return record_rel_path, checksum
    else:
        (data_dir / record_rel_path).write_text(content_before_footer, encoding="utf-8")
        return record_rel_path, None


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------


def _reserve(
    p: Persistence,
    hand_id: str,
    record_path: str,
    **kwargs: Any,
) -> None:
    p.reserve_hand(
        hand_id=hand_id,
        match_id=kwargs.get("match_id"),
        hand_index_in_match=kwargs.get("hand_index_in_match", 0),
        ruleset_id="mcr-2006",
        ruleset_config_hash="abc123",
        started_at_ms=kwargs.get("started_at_ms", int(time.time() * 1000)),
        master_seed="0xdeadbeef",
        record_path=record_path,
        server_version="0.0.1",
        source="selfplay",
        participants=kwargs.get("participants", _CANNED_PARTICIPANTS),
    )


def _finalize(p: Persistence, hand_id: str, checksum: str) -> None:
    p.finalize_hand(
        hand_id,
        ended_at_ms=int(time.time() * 1000),
        terminal_kind="HU",
        winner_seat=0,
        fan_total=8,
        record_checksum=checksum,
        participants_scores={0: 24, 1: -8, 2: -8, 3: -8},
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "records").mkdir()
    return tmp_path


@pytest.fixture()
def p(data_dir: Path) -> Persistence:
    return Persistence(":memory:", data_dir)


# ---------------------------------------------------------------------------
# Fixture 1: reserve_hand round-trip
# ---------------------------------------------------------------------------


def test_reserve_hand_roundtrip(p: Persistence, data_dir: Path) -> None:
    """Fixture 1: reserve_hand → get_hand returns NULL terminals; find_in_progress includes it."""
    record_path, _ = _make_record_file(data_dir, "hand-001", with_footer=False)
    _reserve(p, "hand-001", record_path)

    row = p.get_hand("hand-001")
    assert row is not None
    assert row.hand_id == "hand-001"
    assert row.ended_at_ms is None
    assert row.terminal_kind is None
    assert row.record_checksum is None

    in_progress = p.find_in_progress_hands()
    assert any(h.hand_id == "hand-001" for h in in_progress)


# ---------------------------------------------------------------------------
# Fixture 2: finalize_hand round-trip
# ---------------------------------------------------------------------------


def test_finalize_hand_roundtrip(p: Persistence, data_dir: Path) -> None:
    """Fixture 2: finalize_hand → terminals populated; excluded from in_progress; scores materialised."""
    record_path, checksum = _make_record_file(data_dir, "hand-002")
    assert checksum is not None
    _reserve(p, "hand-002", record_path)
    _finalize(p, "hand-002", checksum)

    row = p.get_hand("hand-002")
    assert row is not None
    assert row.terminal_kind == "HU"
    assert row.winner_seat == 0
    assert row.fan_total == 8
    assert row.record_checksum == checksum
    assert row.ended_at_ms is not None

    # Not in in-progress
    in_progress = p.find_in_progress_hands()
    assert not any(h.hand_id == "hand-002" for h in in_progress)

    # Participants populated by get_hand
    assert len(row.participants) == 4
    scores = {pp.seat: pp.final_score_delta for pp in row.participants}
    assert scores[0] == 24
    assert scores[1] == -8


# ---------------------------------------------------------------------------
# Fixture 3: reserve_hand atomicity
# ---------------------------------------------------------------------------


def test_reserve_hand_atomicity(p: Persistence, data_dir: Path) -> None:
    """Fixture 3: participant INSERT fails (seat=99) → hand_index row also rolled back."""
    record_path, _ = _make_record_file(data_dir, "hand-003", with_footer=False)

    bad_participants = [
        Participant(seat=0, account_id=None, seat_kind="canned", wind="F1", final_score_delta=None),
        Participant(seat=1, account_id=None, seat_kind="canned", wind="F2", final_score_delta=None),
        Participant(seat=2, account_id=None, seat_kind="canned", wind="F3", final_score_delta=None),
        # seat=99 violates CHECK (seat BETWEEN 0 AND 3)
        Participant(seat=99, account_id=None, seat_kind="canned", wind="F4", final_score_delta=None),
    ]

    with pytest.raises(sqlite3.IntegrityError):
        _reserve(p, "hand-003", record_path, participants=bad_participants)

    # hand_index row must NOT exist after rollback
    assert p.get_hand("hand-003") is None


# ---------------------------------------------------------------------------
# Fixture 4: find_hands_by_account orders by started_at_ms DESC
# ---------------------------------------------------------------------------


def test_find_hands_by_account_order_desc(p: Persistence, data_dir: Path) -> None:
    """Fixture 4: 10 hands for one account → returned in started_at_ms DESC."""
    account_id = p.insert_account(
        username="player1",
        display_name="Player 1",
        kind="human",
        role="user",
        password_hash="hash",
    )
    participants = [
        Participant(seat=0, account_id=account_id, seat_kind="human", wind="F1", final_score_delta=None),
        *_CANNED_PARTICIPANTS[1:],
    ]

    t0 = 1_700_000_000_000
    for i in range(10):
        hand_id = f"hand-4-{i:03d}"
        record_path, _ = _make_record_file(data_dir, hand_id, with_footer=False)
        _reserve(p, hand_id, record_path, started_at_ms=t0 + i * 1000, participants=participants)

    rows = p.find_hands_by_account(account_id)
    assert len(rows) == 10
    times = [r.started_at_ms for r in rows]
    assert times == sorted(times, reverse=True), "Must be DESC by started_at_ms"


# ---------------------------------------------------------------------------
# Fixture 5: find_hands_by_account paginates with before_hand_id
# ---------------------------------------------------------------------------


def test_find_hands_by_account_pagination(p: Persistence, data_dir: Path) -> None:
    """Fixture 5: before_hand_id returns next page without overlap."""
    account_id = p.insert_account(
        username="player2",
        display_name="Player 2",
        kind="human",
        role="user",
        password_hash="hash",
    )
    participants = [
        Participant(seat=0, account_id=account_id, seat_kind="human", wind="F1", final_score_delta=None),
        *_CANNED_PARTICIPANTS[1:],
    ]

    t0 = 1_700_000_000_000
    for i in range(120):
        hand_id = f"hand-5-{i:03d}"
        record_path, _ = _make_record_file(data_dir, hand_id, with_footer=False)
        _reserve(p, hand_id, record_path, started_at_ms=t0 + i * 1000, participants=participants)

    page1 = p.find_hands_by_account(account_id, limit=50)
    assert len(page1) == 50

    page2 = p.find_hands_by_account(account_id, limit=50, before_hand_id=page1[-1].hand_id)
    assert len(page2) == 50

    # No overlap
    page1_ids = {r.hand_id for r in page1}
    page2_ids = {r.hand_id for r in page2}
    assert not (page1_ids & page2_ids), "Pages must not overlap"

    # Page 2 is strictly earlier
    assert max(r.started_at_ms for r in page2) < min(r.started_at_ms for r in page1)


# ---------------------------------------------------------------------------
# Fixture 6: find_hands_by_match orders by hand_index_in_match ASC
# ---------------------------------------------------------------------------


def test_find_hands_by_match_order(p: Persistence, data_dir: Path) -> None:
    """Fixture 6: hands inserted in random order → returned by hand_index_in_match."""
    match_id = "match-001"
    for i in [2, 0, 1]:
        hand_id = f"hand-6-{i}"
        record_path, _ = _make_record_file(data_dir, hand_id, match_id=match_id, hand_index=i, with_footer=False)
        _reserve(p, hand_id, record_path, match_id=match_id, hand_index_in_match=i)

    rows = p.find_hands_by_match(match_id)
    assert [r.hand_index_in_match for r in rows] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Fixture 7: integrity_check detects missing record file
# ---------------------------------------------------------------------------


def test_integrity_check_missing_file(p: Persistence, data_dir: Path) -> None:
    """Fixture 7: hand_index row whose record_path doesn't exist → missing_files ≥ 1."""
    # Reserve then finalize so ended_at_ms is set (finalized rows are checked)
    record_path, checksum = _make_record_file(data_dir, "hand-7")
    assert checksum is not None
    _reserve(p, "hand-7", record_path)
    _finalize(p, "hand-7", checksum)

    # Now delete the record file
    (data_dir / record_path).unlink()

    report = p.integrity_check()
    assert report.missing_files >= 1


# ---------------------------------------------------------------------------
# Fixture 8: integrity_check detects orphaned record file
# ---------------------------------------------------------------------------


def test_integrity_check_orphaned_file(p: Persistence, data_dir: Path) -> None:
    """Fixture 8: .jsonl file with no hand_index row → orphaned_files ≥ 1."""
    records_dir = data_dir / "records"
    records_dir.mkdir(exist_ok=True)
    (records_dir / "orphan.jsonl").write_text(
        '{"event":"HEADER","seq":0}\n',
        encoding="utf-8",
    )

    report = p.integrity_check()
    assert report.orphaned_files >= 1


# ---------------------------------------------------------------------------
# Fixture 9: integrity_check detects checksum mismatch after file tamper
# ---------------------------------------------------------------------------


def test_integrity_check_checksum_mismatch(p: Persistence, data_dir: Path) -> None:
    """Fixture 9: tamper header after finalizing → integrity_check reports mismatch."""
    record_path, checksum = _make_record_file(data_dir, "hand-9")
    assert checksum is not None
    _reserve(p, "hand-9", record_path)
    _finalize(p, "hand-9", checksum)

    # Tamper: change a non-footer line so the recomputed hash differs
    full_path = data_dir / record_path
    original = full_path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    assert len(lines) >= 2
    # Modify the HEADER line but leave FOOTER intact
    lines[0] = lines[0].replace("0xdeadbeef", "0xcafebabe")
    full_path.write_text("".join(lines), encoding="utf-8")

    report = p.integrity_check()
    assert report.checksum_mismatches >= 1


# ---------------------------------------------------------------------------
# Fixture 10: rebuild from records produces equivalent DB  (schema.md fixture 11)
# ---------------------------------------------------------------------------


def test_rebuild_from_records(p: Persistence, data_dir: Path) -> None:
    """Fixture 10 / schema.md-11: clear DB; rebuild; rows match originals."""
    record_path_a, cs_a = _make_record_file(data_dir, "hand-10a")
    record_path_b, _ = _make_record_file(data_dir, "hand-10b", with_footer=False)
    assert cs_a is not None

    _reserve(p, "hand-10a", record_path_a)
    _finalize(p, "hand-10a", cs_a)
    _reserve(p, "hand-10b", record_path_b)

    # Snapshot before clearing
    original_a = p.get_hand("hand-10a")
    original_b = p.get_hand("hand-10b")
    assert original_a is not None
    assert original_b is not None

    # Clear the DB (order matters: participants FK → hand_index)
    p._conn.execute("DELETE FROM hand_participants")
    p._conn.execute("DELETE FROM hand_index")
    p._conn.commit()

    assert p.get_hand("hand-10a") is None

    report = p.rebuild_index_from_records()
    assert report.errors == 0
    assert report.inserted == 2

    rebuilt_a = p.get_hand("hand-10a")
    rebuilt_b = p.get_hand("hand-10b")

    assert rebuilt_a is not None
    assert rebuilt_a.hand_id == original_a.hand_id
    assert rebuilt_a.terminal_kind == original_a.terminal_kind
    assert rebuilt_a.record_checksum == original_a.record_checksum
    assert rebuilt_a.winner_seat == original_a.winner_seat

    assert rebuilt_b is not None
    assert rebuilt_b.hand_id == original_b.hand_id
    # No FOOTER → rebuild marks as ABORTED
    assert rebuilt_b.terminal_kind == "ABORTED"


# ---------------------------------------------------------------------------
# Fixture 11: rebuild is idempotent  (schema.md fixture 12)
# ---------------------------------------------------------------------------


def test_rebuild_is_idempotent(p: Persistence, data_dir: Path) -> None:
    """Fixture 11 / schema.md-12: running rebuild twice is a no-op on the second run."""
    record_path, checksum = _make_record_file(data_dir, "hand-11")
    assert checksum is not None
    _reserve(p, "hand-11", record_path)
    _finalize(p, "hand-11", checksum)

    # Clear and rebuild once
    p._conn.execute("DELETE FROM hand_participants")
    p._conn.execute("DELETE FROM hand_index")
    p._conn.commit()

    report1 = p.rebuild_index_from_records()
    assert report1.errors == 0
    assert report1.inserted == 1

    # Rebuild again — should be an update (INSERT OR REPLACE on same PK)
    report2 = p.rebuild_index_from_records()
    assert report2.errors == 0
    assert report2.inserted == 0  # row already existed → updated
    assert report2.updated == 1

    # Data is still intact
    assert p.get_hand("hand-11") is not None


# ---------------------------------------------------------------------------
# Fixture 12: Session CRUD round-trip
# ---------------------------------------------------------------------------


def test_session_crud_roundtrip(p: Persistence) -> None:
    """Fixture 12: insert → get → renew → revoke → delete_expired."""
    account_id = p.insert_account(
        username="sessuser",
        display_name="Session User",
        kind="human",
        role="user",
        password_hash="hash",
    )

    now_ms = int(time.time() * 1000)
    token = "s_" + "a" * 32

    p.insert_session(
        session_id=token,
        account_id=account_id,
        issued_at_ms=now_ms,
        expires_at_ms=now_ms + 86_400_000,
        user_agent="test-agent",
    )

    row = p.get_session(token)
    assert row is not None
    assert row.session_id == token
    assert row.account_id == account_id
    assert row.revoked is False

    # Renew
    new_expiry = now_ms + 2 * 86_400_000
    p.renew_session(token, expires_at_ms=new_expiry, last_seen_ms=now_ms + 1)
    renewed = p.get_session(token)
    assert renewed is not None
    assert renewed.expires_at_ms == new_expiry

    # Revoke
    p.revoke_session(token)
    revoked = p.get_session(token)
    assert revoked is not None
    assert revoked.revoked is True

    # Expire the session then delete_expired_sessions
    p.renew_session(token, expires_at_ms=now_ms - 1, last_seen_ms=now_ms)
    deleted = p.delete_expired_sessions(before_ms=now_ms)
    assert deleted >= 1
    assert p.get_session(token) is None


# ---------------------------------------------------------------------------
# Fixture 13: Account CRUD round-trip
# ---------------------------------------------------------------------------


def test_account_crud_roundtrip(p: Persistence) -> None:
    """Fixture 13: insert → get (case-insensitive) → update login → disable → re-enable."""
    account_id = p.insert_account(
        username="TestUser",
        display_name="Test User",
        kind="human",
        role="user",
        password_hash="initial_hash",
    )

    # Case-insensitive lookup
    row = p.get_account_by_username("testuser")
    assert row is not None
    assert row.account_id == account_id
    assert row.username == "TestUser"  # preserved original casing
    assert row.password_hash == "initial_hash"

    row2 = p.get_account_by_username("TESTUSER")
    assert row2 is not None and row2.account_id == account_id

    # Lookup by id
    row3 = p.get_account_by_id(account_id)
    assert row3 is not None and row3.username == "TestUser"

    # Update login
    now_ms = int(time.time() * 1000)
    p.update_account_login(account_id, password_hash="updated_hash", last_login_ms=now_ms)
    updated = p.get_account_by_id(account_id)
    assert updated is not None
    assert updated.password_hash == "updated_hash"
    assert updated.last_login_ms == now_ms

    # Disable
    assert updated.disabled is False
    p.set_account_disabled(account_id, True)
    disabled_row = p.get_account_by_id(account_id)
    assert disabled_row is not None and disabled_row.disabled is True

    # Re-enable
    p.set_account_disabled(account_id, False)
    reenabled = p.get_account_by_id(account_id)
    assert reenabled is not None and reenabled.disabled is False
