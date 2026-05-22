"""In-tree Python `b_random` reference bot.

Speaks Botzone CSM via `mahjong.bots.sdk`. For Step 6.1a, behavior is
deterministic tsumogiri-plus-pass — identical play to `py_reference_v1`.
The distinct `bot_id` is what 6.1a needs: rotation tests assert that
`HEADER.seats[i].identity.bot_id` changes per hand under `round-robin`,
which requires two registered bot ids. A truly randomized policy is
deferred until self-play needs varied trajectories for training data.
"""

from __future__ import annotations

from typing import Any

from mahjong.bots.sdk import latest_botzone_request, run_bot

BOT_ID = "b_random"
VERSION = "0.1.0"


def decide(request: dict[str, Any]) -> str:
    code, args = latest_botzone_request(request)
    if code == "2" and args:
        return f"PLAY {args[0]}"
    return "PASS"


if __name__ == "__main__":
    run_bot(decide, bot_id=BOT_ID, version=VERSION)
