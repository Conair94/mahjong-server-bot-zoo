"""In-process unit test for `bots/python-reference/bot.py`'s decide().

The full subprocess-driven integration lives in
`tests/adapters/test_layer5_e2e.py`. This file just confirms the decide
function's branches without the spawn cost.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

REFERENCE_BOT = Path(__file__).resolve().parents[2] / "bots" / "python-reference" / "bot.py"


def _load_decide() -> Any:
    spec = importlib.util.spec_from_file_location("python_reference_bot", REFERENCE_BOT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REFERENCE_BOT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module.decide


def test_plays_drawn_tile() -> None:
    decide = _load_decide()
    assert decide({"requests": ["2 W5"], "responses": []}) == "PLAY W5"


def test_passes_on_others_discard() -> None:
    decide = _load_decide()
    assert decide({"requests": ["3 1 PLAY W5"], "responses": []}) == "PASS"


def test_passes_on_init_request() -> None:
    decide = _load_decide()
    assert decide({"requests": ["0 0 0"], "responses": []}) == "PASS"


def test_passes_on_empty_request() -> None:
    decide = _load_decide()
    assert decide({}) == "PASS"
