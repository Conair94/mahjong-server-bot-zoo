"""Shared pytest fixtures and helpers.

Test layout mirrors the mahjong/ package; one tests/ subdirectory per package
subdirectory. Cross-cutting helpers (golden fixture loading, platform markers)
live here so individual test files stay focused.

Conventions:
    - Golden fixtures live in tests/_fixtures/ as JSON files.
    - Tests requiring cross-platform determinism are marked @pytest.mark.determinism.
    - Tests requiring Linux-only features (netns, RLIMIT_AS) are marked @pytest.mark.linux_only.
    - Tests requiring PyMahjongGB are marked @pytest.mark.needs_pymjgb.
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Any

import pytest

FIXTURE_ROOT = Path(__file__).parent / "_fixtures"


# --- Helpers ---


def load_golden(relpath: str) -> Any:
    """Load a JSON golden fixture relative to tests/_fixtures/.

    Use this rather than ad-hoc file reads so the fixture directory layout
    is enforced and golden paths are greppable.
    """
    path = FIXTURE_ROOT / relpath
    if not path.exists():
        raise FileNotFoundError(
            f"Golden fixture not found: {path}. "
            f"Add it to tests/_fixtures/ before this test can run."
        )
    return json.loads(path.read_text(encoding="utf-8"))


# --- Platform-aware skip helpers (consumed by tests as needed) ---


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_macos() -> bool:
    return sys.platform == "darwin"


# --- Auto-applied markers ---


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip linux_only tests on non-Linux platforms."""
    skip_non_linux = pytest.mark.skip(reason=f"linux_only test skipped on {platform.system()}")
    for item in items:
        if "linux_only" in item.keywords and not is_linux():
            item.add_marker(skip_non_linux)
