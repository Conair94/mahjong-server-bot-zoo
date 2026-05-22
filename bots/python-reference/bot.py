"""In-tree Python rule-based reference bot.

Speaks Botzone CSM via `mahjong.bots.sdk`. Plays tsumogiri-like rules:

  - own draw (`2 <tile>`) -> `PLAY <tile>` (discard what was just drawn).
  - any claim opportunity -> PASS (no PENG / CHI / GANG / HU).
  - init / deal -> PASS.

Used by the four-bot integration test in `tests/adapters/test_layer5_e2e.py`
to exercise the BotzoneCsmSerializer end-to-end without requiring the
upstream C++ sample bot (which is deferred to Step 5.3b).
"""

from __future__ import annotations

from typing import Any

from mahjong.bots.sdk import latest_botzone_request, run_bot

BOT_ID = "py_reference_v1"
VERSION = "0.1.0"


def decide(request: dict[str, Any]) -> str:
    code, args = latest_botzone_request(request)
    if code == "2" and args:
        return f"PLAY {args[0]}"
    return "PASS"


if __name__ == "__main__":
    run_bot(decide, bot_id=BOT_ID, version=VERSION)
