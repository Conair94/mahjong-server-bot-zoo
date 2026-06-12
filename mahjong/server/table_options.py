"""Parse + validate the optional ``CREATE_TABLE.options`` object.

Spec: docs/specs/layer8-closeout-r2.md § 22.6 Part A.

A table creator may tune three knobs at creation time; they are frozen for
the life of the table (no mid-hand changes):

- ``bot_pacing`` — a named preset (``fast`` / ``normal`` / ``slow``) or a
  custom ``{"min_s", "max_s"}`` object, controlling the uniform-random delay
  before each bot ``decide``.
- ``decide_timeout_seconds`` — overrides the human DISCARD deadline only;
  human CLAIM and bot deadlines stay at the server default.
- ``timeouts_enabled`` — when ``false``, human seats get an effectively
  unlimited decide deadline (the table waits indefinitely for a human; the
  strike / AutoPass takeover never fires on them). Bots keep their deadline.
- ``stats_enabled`` — when ``false``, the Spec 37 decision-time analysis
  (shanten / waits / fan) is disabled for the whole table: the composition
  root binds no stats provider, so no ``PROMPT.stats`` is ever attached.
  Default ``true``.

``parse_table_options`` resolves the raw wire object against the server
defaults and returns concrete values to hand to ``create_table``. Invalid
input raises :class:`TableOptionsError`, which the orchestrator surfaces as
``ERROR { code: "framing" }``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from mahjong.table.manager import DecideTimeouts

# Named bot-pacing presets → (min_s, max_s).
PACING_PRESETS: dict[str, tuple[float, float]] = {
    "fast": (0.5, 1.5),
    "normal": (5.0, 10.0),
    "slow": (15.0, 30.0),
}

# Validation bounds (spec § 22.6 Part A).
_PACING_MAX_S = 60.0
_DECIDE_MIN_S = 5.0
_DECIDE_MAX_S = 600.0
# "Effectively unlimited" human deadline when timeouts are disabled (~115
# days). Large enough that the strike/AutoPass path never fires in practice;
# avoids threading a true "no deadline" sentinel through the manager.
_NO_TIMEOUT_S = 10_000_000.0


class TableOptionsError(ValueError):
    """Raised when ``CREATE_TABLE.options`` is malformed or out of range."""


@dataclasses.dataclass(frozen=True)
class ResolvedTableOptions:
    """Concrete values to pass to ``create_table`` after resolving against
    the server defaults."""

    bot_pacing_enabled: bool
    bot_min_delay_s: float
    bot_max_delay_s: float
    decide_timeouts: DecideTimeouts
    stats_enabled: bool = True


def _resolve_pacing(raw: Any) -> tuple[float, float]:
    """Resolve a ``bot_pacing`` value (preset string or custom object) to a
    (min_s, max_s) pair."""
    if isinstance(raw, str):
        preset = PACING_PRESETS.get(raw)
        if preset is None:
            raise TableOptionsError(
                f"bot_pacing must be one of {sorted(PACING_PRESETS)} or a "
                f"{{min_s, max_s}} object; got {raw!r}"
            )
        return preset
    if isinstance(raw, dict):
        try:
            min_s = float(raw["min_s"])
            max_s = float(raw["max_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TableOptionsError(
                f"custom bot_pacing requires numeric min_s and max_s; got {raw!r}"
            ) from exc
        if not (0.0 <= min_s <= max_s <= _PACING_MAX_S):
            raise TableOptionsError(
                f"bot_pacing requires 0 <= min_s <= max_s <= {_PACING_MAX_S}; "
                f"got min_s={min_s}, max_s={max_s}"
            )
        return min_s, max_s
    raise TableOptionsError(
        f"bot_pacing must be a preset string or {{min_s, max_s}} object; got {type(raw).__name__}"
    )


def parse_table_options(
    raw: Any,
    *,
    default_pacing_enabled: bool,
    default_min_delay_s: float,
    default_max_delay_s: float,
    default_decide_timeouts: DecideTimeouts,
) -> ResolvedTableOptions:
    """Resolve ``CREATE_TABLE.options`` against the server defaults.

    ``raw`` is ``msg.get("options")`` — ``None`` (or absent) yields the
    server defaults unchanged.
    """
    pacing_enabled = default_pacing_enabled
    min_s = default_min_delay_s
    max_s = default_max_delay_s
    decide_timeouts = default_decide_timeouts

    if raw is None:
        return ResolvedTableOptions(pacing_enabled, min_s, max_s, decide_timeouts)
    if not isinstance(raw, dict):
        raise TableOptionsError(f"options must be an object; got {type(raw).__name__}")

    # Stats are on unless the creator explicitly disables them (lenient, like
    # timeouts_enabled — only a literal `false` opts out).
    stats_enabled = raw.get("stats_enabled") is not False

    if "bot_pacing" in raw:
        min_s, max_s = _resolve_pacing(raw["bot_pacing"])
        pacing_enabled = True

    if raw.get("timeouts_enabled") is False:
        # Disable human deadlines entirely; bots keep theirs.
        decide_timeouts = dataclasses.replace(
            decide_timeouts,
            human_discard_s=_NO_TIMEOUT_S,
            human_claim_s=_NO_TIMEOUT_S,
        )
    elif "decide_timeout_seconds" in raw:
        try:
            secs = float(raw["decide_timeout_seconds"])
        except (TypeError, ValueError) as exc:
            raise TableOptionsError(
                f"decide_timeout_seconds must be numeric; got {raw['decide_timeout_seconds']!r}"
            ) from exc
        if not (_DECIDE_MIN_S <= secs <= _DECIDE_MAX_S):
            raise TableOptionsError(
                f"decide_timeout_seconds must be {_DECIDE_MIN_S}..{_DECIDE_MAX_S}; got {secs}"
            )
        # Per spec: overrides the human DISCARD deadline only.
        decide_timeouts = dataclasses.replace(decide_timeouts, human_discard_s=secs)

    return ResolvedTableOptions(pacing_enabled, min_s, max_s, decide_timeouts, stats_enabled)


__all__ = [
    "PACING_PRESETS",
    "ResolvedTableOptions",
    "TableOptionsError",
    "parse_table_options",
]
