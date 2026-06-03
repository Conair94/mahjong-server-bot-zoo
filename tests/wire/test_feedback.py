"""Tests for `mahjong.wire.feedback.sanitise_report_text`.

Spec: docs/specs/feedback-reporting.md § 23.1 Sanitisation contract,
      § 23.4 Unit: sanitisation fixtures.

TDD: these tests were written before the implementation.
"""

from __future__ import annotations

import pytest

from mahjong.wire.feedback import sanitise_report_text, SanitiseError


class TestSanitiseReportText:
    def test_clean_text_passes_through(self):
        assert sanitise_report_text("Hello world!") == "Hello world!"

    def test_strips_html_tags(self):
        result = sanitise_report_text("bug <script>alert(1)</script>")
        assert result == "bug script alert 1 script"

    def test_strips_shell_metacharacters(self):
        result = sanitise_report_text("rm -rf /; drop table users")
        assert "<" not in result
        assert ";" not in result
        assert "/" not in result
        assert "rm -rf" in result
        assert "drop table users" in result

    def test_collapses_whitespace(self):
        result = sanitise_report_text("too   many    spaces")
        assert "  " not in result
        assert "too many spaces" == result

    def test_trims_leading_trailing_whitespace(self):
        assert sanitise_report_text("  hello world  ") == "hello world"

    def test_truncates_to_800_chars(self):
        long_text = "a" * 2000
        result = sanitise_report_text(long_text)
        assert len(result) == 800

    def test_raises_on_text_too_short(self):
        with pytest.raises(SanitiseError, match="too short"):
            sanitise_report_text("   ")

    def test_raises_on_text_too_short_after_stripping(self):
        # 9 safe chars — below the 10-char minimum
        with pytest.raises(SanitiseError, match="too short"):
            sanitise_report_text("abc def g")

    def test_exactly_ten_chars_accepted(self):
        result = sanitise_report_text("abcde fghi")
        assert result == "abcde fghi"

    def test_newlines_treated_as_whitespace(self):
        result = sanitise_report_text("line one\nline two")
        assert result == "line one line two"

    def test_allowed_punctuation_preserved(self):
        text = "It's broken, really! Why? No idea."
        result = sanitise_report_text(text)
        assert result == text

    def test_special_chars_replaced_with_space(self):
        result = sanitise_report_text("hello @ world # test!!")
        assert "@" not in result
        assert "#" not in result
        # surrounding words still present
        assert "hello" in result
        assert "world" in result
        assert "test" in result
