"""Botzone CSM request serializer for `BotRunnerAdapter`.

Spec: docs/specs/bot-runner-protocol.md § Per-turn request (defers wire
format to the Botzone CSM wiki) + upstream sample bot
[ailab-pku/Chinese-Standard-Mahjong/sample-bot-Botzone/sample.cpp].

Per-seat state machine: convert observe events into typed request lines,
track this seat's own responses, and on each `decide` emit a JSON envelope
matching the format the upstream reference bot reads:

    {"requests": ["0 0 0", "1 0 0 0 0 W1 W2 ...", "2 W5"],
     "responses": ["PASS", "PASS"]}

Each request[i] is a single space-separated typed line. Type codes mirror the
judge's `roundStage`:

    "0 <my_seat> <round>"      = init
    "1 0 0 0 0 <13 tiles>"     = deal (13 concealed for me)
    "2 <tile>"                 = my own draw -> I must discard
    "3 <seat> PLAY <tile>"     = a seat discarded -> I may claim
    "3 <seat> <claim>"         = a seat claimed (PENG / GANG / CHI / HU / BUGANG)

`len(responses) == len(requests) - 1` at the moment `decide` is called: the
current pending request is the one the bot is being asked to answer. Events
that don't trigger this seat's `decide` (e.g. another seat's claim that I
just observe) are recorded as `(request, "PASS")` pairs so the indices stay
aligned per Botzone convention.

This is the first iteration. **Step 5.3b** (deferred; gated on the C++
judge binary being built) is where we verify byte-fidelity against the
official judge log; until then this serializer is structurally correct
but not byte-compared. See [bots/README.md](../../bots/README.md).
"""

from __future__ import annotations

import json
from typing import Any, cast

from mahjong.adapters.base import Prompt
from mahjong.engine.types import Action


class BotzoneCsmSerializer:
    """Per-seat history -> Botzone-shaped JSON envelope.

    Lifecycle: one instance per (seat, hand). The runner calls `on_observe`
    for every fanned-out event and `on_decide` when this seat must act;
    after the bot replies, the runner calls `record_response` so the next
    envelope includes the just-made response in `responses[]`.
    """

    def __init__(self, seat: int, *, round_index: int = 0) -> None:
        self._seat = seat
        self._round_index = round_index
        self._requests: list[str] = []
        self._responses: list[str] = []
        self._dealt_initial = False
        self._header_seen = False

    # --- Public API ---

    def on_observe(self, event: dict[str, Any], view: dict[str, Any]) -> None:
        kind = event.get("event")
        if kind == "HEADER":
            self._on_header()
        elif kind == "DEAL":
            self._on_deal(event)
        elif kind == "DRAW":
            self._on_draw(event)
        elif kind == "DISCARD":
            self._on_discard(event)
        elif kind == "CLAIM_DECISION":
            self._on_claim_decision(event)
        # CLAIM_WINDOW, CLAIM_RESOLUTION, HAND_END, FOOTER -> nothing to push
        # to the bot. The decide that resolves a claim window already carries
        # the relevant discard typed line; HAND_END / FOOTER terminate the
        # subprocess from `BotRunnerAdapter.left`.

    def on_decide(self, prompt: Prompt) -> str:
        """Build the JSON envelope for the current decide.

        Length invariant: `len(self._requests) == len(self._responses) + 1`
        on return. Auto-pads PASSes for any requests this seat saw but never
        had to act on (the buffer was populated by `on_observe`).
        """
        self._pad_passive_responses()
        envelope = {
            "requests": list(self._requests),
            "responses": list(self._responses),
        }
        return json.dumps(envelope)

    def record_response(self, action: Action) -> None:
        self._responses.append(action_to_botzone_string(action))

    def history_length(self) -> int:
        """Number of typed lines emitted to this seat so far."""
        return len(self._requests)

    # --- Event handlers ---

    def _on_header(self) -> None:
        if self._header_seen:
            return
        self._header_seen = True
        # "0 <my_seat> <round>" per sample.cpp's first read.
        self._requests.append(f"0 {self._seat} {self._round_index}")
        # Init request is purely informational; the bot would reply PASS.
        self._responses.append("PASS")

    def _on_deal(self, event: dict[str, Any]) -> None:
        if self._dealt_initial:
            return
        self._dealt_initial = True
        concealed = event["concealed"][self._seat]
        # "1 0 0 0 0 <13 tiles>" — five leading ints per sample.cpp's read
        # loop (`for(int j = 0; j < 5; j++) sin >> itmp;`). The first int is
        # the type code; the remaining four are placeholders the upstream
        # bot ignores. The next 13 tokens are this seat's concealed tiles.
        line = "1 0 0 0 0 " + " ".join(concealed)
        self._requests.append(line)
        self._responses.append("PASS")

    def _on_draw(self, event: dict[str, Any]) -> None:
        # Per judge.cpp roundStage 0-3: a draw is only revealed to the
        # drawing seat. Other seats see nothing.
        if event["seat"] != self._seat:
            return
        self._requests.append(f"2 {event['tile']}")
        # Don't pad a response here — this draw is what the next decide
        # answers (PLAY <tile>).

    def _on_discard(self, event: dict[str, Any]) -> None:
        # Per judge.cpp roundStage 4-7: a discard is broadcast to all seats.
        # The discarder gets the line too — it's how `responses[]` align
        # with the bot's own PLAY action they just emitted. We append the
        # request for everyone; the discarder's response was already recorded
        # in record_response.
        self._requests.append(f"3 {event['seat']} PLAY {event['tile']}")
        # If this is my own discard, the response is already in self._responses.
        # If it's someone else's, the claim window may or may not trigger a
        # decide from me. Default to PASS — on_decide will re-evaluate when
        # the next decide fires (it pads responses up to len-1).

    def _on_claim_decision(self, event: dict[str, Any]) -> None:
        seat = event["seat"]
        if seat == self._seat:
            # My own claim — the response was recorded by the runner.
            return
        decision = event["decision"]
        tokens = _claim_decision_tokens(event, decision)
        self._requests.append(f"3 {seat} {' '.join(tokens)}")
        # Passive observation from this seat's POV; pad PASS.
        self._responses.append("PASS")

    def _pad_passive_responses(self) -> None:
        # Goal: len(responses) == len(requests) - 1 when the bot is about to
        # answer the most recent request. Pad PASSes for any gap.
        gap = len(self._requests) - len(self._responses) - 1
        for _ in range(gap):
            self._responses.append("PASS")


def _claim_decision_tokens(event: dict[str, Any], decision: str) -> list[str]:
    if decision == "PASS":
        return ["PASS"]
    if decision == "PENG":
        return ["PENG", event["tile"]]
    if decision == "CHI":
        chi_tiles = event["chi_tiles"]
        # Botzone CHI: <claimed tile> <middle tile>. Match botzone_export.py.
        return ["CHI", chi_tiles[1], chi_tiles[1]]
    if decision == "GANG":
        kind = event.get("kind", "EXPOSED")
        if kind == "ADDED":
            return ["BUGANG", event["tile"]]
        return ["GANG", event["tile"]]
    if decision == "HU":
        return ["HU"]
    raise ValueError(f"unknown claim decision: {decision!r}")


def action_to_botzone_string(action: Action) -> str:
    """Convert an `Action` dict to the Botzone single-line action format.

    Inverse of `parse_action_string` in `mahjong.adapters.bot_runner`.
    """
    typ = action["type"]
    if typ == "PASS":
        return "PASS"
    if typ == "PLAY":
        return f"PLAY {action['tile']}"  # type: ignore[typeddict-item]
    if typ == "PENG":
        return f"PENG {action['tile']}"  # type: ignore[typeddict-item]
    if typ == "CHI":
        tiles = action["tiles"]  # type: ignore[typeddict-item]
        return f"CHI {tiles[0]} {tiles[1]}"
    if typ == "GANG":
        kind = cast(dict[str, Any], action).get("kind", "EXPOSED")
        if kind == "ADDED":
            return f"BUGANG {action['tile']}"  # type: ignore[typeddict-item]
        return f"GANG {action['tile']}"  # type: ignore[typeddict-item]
    if typ == "HU":
        return "HU"
    raise ValueError(f"cannot serialize action {action!r}")


__all__ = [
    "BotzoneCsmSerializer",
    "action_to_botzone_string",
]
