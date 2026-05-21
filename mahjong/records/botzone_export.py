"""Export a native record to the Botzone CHINESEOFFICIAL judge log shape.

Spec: docs/specs/record-format.md § Botzone export.

The byte format the live judge expects is locked down during S1 (bot-runner);
the gate here is the spec's *mapping rules* — which native events become
which Botzone messages, in what order, with what tokens. The intermediate
shape returned by `export_to_botzone` is a list of structured messages that
S1 (or any external tool) can render to whatever exact bytes the judge needs.

Mapping (from spec):
    HEADER             -> per-seat `init` requests ("0 <round> <seat>")
    DEAL               -> per-seat `deal` requests carrying initial concealed
    DRAW               -> `draw` request to drawing seat ("2 <tile>")
    DISCARD            -> broadcast `discard` ("3 <seat> PLAY <tile>")
    CLAIM_WINDOW       -> dropped (no Botzone equivalent)
    CLAIM_DECISION     -> per-seat `claim_response`
    CLAIM_RESOLUTION   -> dropped
    HAND_END           -> single `hand_end` message
    FOOTER             -> dropped (integrity is a native-record concern)

Tile tokens are pass-through: our spec's `W*/B*/T*/F*/J*/H*` already match
Botzone CHINESEOFFICIAL's encoding, so no conversion layer is needed.
"""

from __future__ import annotations

from typing import Any

# Round-wind tile -> Botzone round index (0=East, 1=South, 2=West, 3=North).
_WIND_INDEX = {"F1": 0, "F2": 1, "F3": 2, "F4": 3}


def export_to_botzone(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map a list of record events to Botzone-shaped messages.

    Each message is `{"kind": str, "tokens": list[str], "source_seq": int,
    ... optional seat/target fields}`. Order matches the source event order
    after dropping un-mapped event types.
    """
    if not events or events[0].get("event") != "HEADER":
        raise ValueError("events must start with a HEADER")

    header = events[0]
    round_idx = _round_index_from_header(header)

    log: list[dict[str, Any]] = []
    for seat in range(4):
        log.append(
            {
                "kind": "init",
                "seat": seat,
                "tokens": ["0", str(round_idx), str(seat)],
                "source_seq": header["seq"],
            }
        )

    for event in events[1:]:
        kind = event["event"]
        if kind == "DEAL":
            for seat_idx, concealed in enumerate(event["concealed"]):
                log.append(
                    {
                        "kind": "deal",
                        "seat": seat_idx,
                        "tokens": ["1", *concealed],
                        "source_seq": event["seq"],
                    }
                )
        elif kind == "DRAW":
            log.append(
                {
                    "kind": "draw",
                    "seat": event["seat"],
                    "tokens": ["2", event["tile"]],
                    "source_seq": event["seq"],
                }
            )
        elif kind == "DISCARD":
            log.append(
                {
                    "kind": "discard",
                    "tokens": ["3", str(event["seat"]), "PLAY", event["tile"]],
                    "source_seq": event["seq"],
                }
            )
        elif kind == "CLAIM_DECISION":
            log.append(
                {
                    "kind": "claim_response",
                    "seat": event["seat"],
                    "tokens": _claim_response_tokens(event),
                    "source_seq": event["seq"],
                }
            )
        elif kind == "HAND_END":
            log.append(
                {
                    "kind": "hand_end",
                    "tokens": _hand_end_tokens(event),
                    "source_seq": event["seq"],
                }
            )
        elif kind in {"CLAIM_WINDOW", "CLAIM_RESOLUTION", "FOOTER"}:
            continue
        else:
            raise ValueError(f"unknown event in record: {kind!r}")

    return log


def _round_index_from_header(header: dict[str, Any]) -> int:
    """Derive Botzone's round index from the round wind. Defaults to East if
    the header doesn't carry an explicit field (the record schema doesn't
    require one yet; the canonical engine always opens with F1 = East)."""
    # Look at seats[0].wind as a proxy for round wind in the absence of an
    # explicit field; dealer is always seat 0 in our engine.
    seats = header.get("seats", [])
    if seats:
        first_wind = seats[0].get("wind", "F1")
        return _WIND_INDEX.get(first_wind, 0)
    return 0


def _claim_response_tokens(event: dict[str, Any]) -> list[str]:
    decision = event["decision"]
    if decision == "PASS":
        return ["PASS"]
    if decision == "PENG":
        return ["PENG", event["tile"]]
    if decision == "CHI":
        # Botzone CHI: middle tile + the claimed tile. Our chi_tiles list is
        # the three tiles in run order; the middle is tiles[1].
        chi_tiles = event["chi_tiles"]
        return ["CHI", chi_tiles[1], chi_tiles[1]]
    if decision == "GANG":
        kind = event.get("kind", "EXPOSED")
        if kind == "ADDED":
            return ["BUGANG", event["tile"]]
        return ["GANG", event["tile"]]
    if decision == "HU":
        return ["HU"]
    raise ValueError(f"unknown decision: {decision!r}")


def _hand_end_tokens(event: dict[str, Any]) -> list[str]:
    if event["kind"] == "DRAW":
        return ["DRAW"]
    winners = event["winner"]
    winner_tok = str(winners[0]) if winners else "-1"
    return ["HU", winner_tok, str(event["fan_total"])]


__all__ = ["export_to_botzone"]
