"""FeedbackInbox — reads the server's data_dir/reports/*.txt feedback files.

Spec: docs/specs/admin-console.md § "Feedback inbox" + fixture `feedback_inbox`.
The server's `_handle_feedback` writes files shaped:

    type: bug
    submitted: 2026-06-03T12:00:00+00:00
    submitter: Alice
    ---
    <body text>

The inbox parses that header back into structured rows for the console pane.
"""

from __future__ import annotations

import pytest

from mahjong.control.feedback import FeedbackInbox

pytestmark = pytest.mark.asyncio


def _write_report(reports_dir, name: str, *, rtype: str, submitted: str, submitter: str, body: str) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    header = f"type: {rtype}\nsubmitted: {submitted}\nsubmitter: {submitter}\n---\n"
    (reports_dir / name).write_text(header + body, encoding="utf-8")


async def test_lists_and_parses_a_report(tmp_path):
    reports = tmp_path / "reports"
    _write_report(
        reports,
        "20260603_120000_bug.txt",
        rtype="bug",
        submitted="2026-06-03T12:00:00+00:00",
        submitter="Alice",
        body="the tiles overlap on narrow screens",
    )
    inbox = FeedbackInbox(reports)

    rows = await inbox.list_reports()

    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "bug"
    assert row["submitter"] == "Alice"
    assert row["submitted"] == "2026-06-03T12:00:00+00:00"
    assert row["text"] == "the tiles overlap on narrow screens"
    assert row["filename"] == "20260603_120000_bug.txt"


async def test_missing_dir_is_empty_not_an_error(tmp_path):
    inbox = FeedbackInbox(tmp_path / "does-not-exist")
    assert await inbox.list_reports() == []


async def test_newest_first_and_body_may_be_multiline(tmp_path):
    reports = tmp_path / "reports"
    _write_report(reports, "20260601_090000_feature.txt", rtype="feature",
                  submitted="2026-06-01T09:00:00+00:00", submitter="Bob", body="add a dark mode")
    _write_report(reports, "20260603_120000_bug.txt", rtype="bug",
                  submitted="2026-06-03T12:00:00+00:00", submitter="Alice",
                  body="line one\nline two")
    inbox = FeedbackInbox(reports)

    rows = await inbox.list_reports()

    # Filenames are timestamp-prefixed, so newest sorts first.
    assert [r["filename"] for r in rows] == [
        "20260603_120000_bug.txt",
        "20260601_090000_feature.txt",
    ]
    assert rows[0]["text"] == "line one\nline two"


async def test_malformed_file_is_skipped_not_fatal(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    (reports / "garbage.txt").write_text("no header here", encoding="utf-8")
    _write_report(reports, "20260603_120000_bug.txt", rtype="bug",
                  submitted="2026-06-03T12:00:00+00:00", submitter="Alice", body="ok")
    inbox = FeedbackInbox(reports)

    rows = await inbox.list_reports()

    # The well-formed report is still listed; the garbage one is tolerated
    # (either skipped or surfaced with empty fields) without raising.
    assert any(r["filename"] == "20260603_120000_bug.txt" for r in rows)
