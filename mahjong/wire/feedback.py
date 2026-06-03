"""Sanitisation utilities for the feedback reporting endpoint.

Spec: docs/specs/feedback-reporting.md § 23.1 Sanitisation contract.
"""

from __future__ import annotations

import re

_ALLOWED = re.compile(r"[^A-Za-z0-9 .,!?'\-\n]")
_WHITESPACE = re.compile(r"[ \n]+")

MIN_LENGTH = 10
MAX_LENGTH = 800


class SanitiseError(ValueError):
    pass


def sanitise_report_text(text: str) -> str:
    """Strip unsafe characters from user-supplied report text.

    Raises SanitiseError if the sanitised result is too short.
    Silently truncates to MAX_LENGTH.
    """
    cleaned = _ALLOWED.sub(" ", text)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    if len(cleaned) < MIN_LENGTH:
        raise SanitiseError(f"text too short (got {len(cleaned)}, minimum {MIN_LENGTH})")
    return cleaned[:MAX_LENGTH]
