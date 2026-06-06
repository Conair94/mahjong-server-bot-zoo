"""FeedbackStatusStore — the triage-status sidecar over the report files.

Spec: docs/specs/feedback-tracking.md § 30.1–30.2.  The store keys status by report
filename, validates against the fixed vocabulary, and writes `status.json` atomically so a
crash mid-write can't corrupt the map.  Pure synchronous disk IO (the async layer wraps it
in run_in_executor), so these tests are plain sync tests.
"""

from __future__ import annotations

import json

import pytest

from mahjong.control.feedback import FeedbackStatusStore


def test_missing_file_loads_empty(tmp_path):
    store = FeedbackStatusStore(tmp_path / "status.json")
    assert store.load() == {}


def test_set_persists_and_reloads(tmp_path):
    path = tmp_path / "status.json"
    entry = FeedbackStatusStore(path).set(
        "20260606_000643_bug.txt", "triaged", backlog_id="FB-01", note="repro-first"
    )
    assert entry["status"] == "triaged"
    assert entry["backlog_id"] == "FB-01"
    assert entry["note"] == "repro-first"
    assert entry["updated"]  # server-set ISO timestamp, non-empty

    # A fresh store over the same path sees it (persisted to disk).
    loaded = FeedbackStatusStore(path).load()
    assert loaded["20260606_000643_bug.txt"]["status"] == "triaged"
    assert loaded["20260606_000643_bug.txt"]["backlog_id"] == "FB-01"


def test_invalid_status_rejected_and_nothing_written(tmp_path):
    path = tmp_path / "status.json"
    store = FeedbackStatusStore(path)
    with pytest.raises(ValueError):
        store.set("x_bug.txt", "banana")
    assert not path.exists()  # rejected before any write


def test_invalid_backlog_id_rejected(tmp_path):
    store = FeedbackStatusStore(tmp_path / "status.json")
    with pytest.raises(ValueError):
        store.set("x_bug.txt", "open", backlog_id="nope")
    # the canonical FB-NN form is accepted
    assert store.set("x_bug.txt", "open", backlog_id="FB-03")["backlog_id"] == "FB-03"


def test_note_is_sanitised_and_truncated(tmp_path):
    store = FeedbackStatusStore(tmp_path / "status.json")
    entry = store.set("x_bug.txt", "open", note="drop; <script>alert(1)</script> " * 40)
    assert "<" not in entry["note"] and ";" not in entry["note"]
    assert len(entry["note"]) <= 200


def test_empty_note_allowed(tmp_path):
    # Unlike a report body (Spec 23 min length 10), a status note may be blank.
    entry = FeedbackStatusStore(tmp_path / "status.json").set("x_bug.txt", "open", note="")
    assert entry["note"] == ""


def test_overwrite_last_write_wins_and_file_stays_valid_json(tmp_path):
    path = tmp_path / "status.json"
    store = FeedbackStatusStore(path)
    store.set("a.txt", "open")
    store.set("b.txt", "triaged")
    store.set("a.txt", "implemented")  # overwrite a's status, keep b

    loaded = store.load()
    assert loaded["a.txt"]["status"] == "implemented"
    assert loaded["b.txt"]["status"] == "triaged"
    # never left half-written: the file parses as JSON.
    json.loads(path.read_text(encoding="utf-8"))


def test_corrupt_sidecar_loads_empty_not_fatal(tmp_path):
    path = tmp_path / "status.json"
    path.write_text("{ this is not json", encoding="utf-8")
    # A garbled sidecar must not crash the console; treat as empty.
    assert FeedbackStatusStore(path).load() == {}
