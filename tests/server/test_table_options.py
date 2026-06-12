"""parse_table_options — CREATE_TABLE.options resolution (§22.6 Part A)."""

from __future__ import annotations

import pytest

from mahjong.server.table_options import (
    ResolvedTableOptions,
    TableOptionsError,
    parse_table_options,
)
from mahjong.table.manager import DecideTimeouts

_DEFAULTS = dict(
    default_pacing_enabled=True,
    default_min_delay_s=5.0,
    default_max_delay_s=10.0,
    default_decide_timeouts=DecideTimeouts(human_discard_s=60.0, human_claim_s=20.0, bot_s=30.0),
)


def test_none_options_yields_server_defaults() -> None:
    r = parse_table_options(None, **_DEFAULTS)
    assert r == ResolvedTableOptions(
        bot_pacing_enabled=True,
        bot_min_delay_s=5.0,
        bot_max_delay_s=10.0,
        decide_timeouts=DecideTimeouts(60.0, 20.0, 30.0),
        stats_enabled=True,
    )


def test_stats_enabled_defaults_true() -> None:
    """Absent stats_enabled, or any non-false value, keeps the analyzer on."""
    assert parse_table_options(None, **_DEFAULTS).stats_enabled is True
    assert parse_table_options({}, **_DEFAULTS).stats_enabled is True
    assert parse_table_options({"stats_enabled": True}, **_DEFAULTS).stats_enabled is True


def test_stats_enabled_false_disables() -> None:
    """Only an explicit false opts the table out of decision-time stats."""
    r = parse_table_options({"stats_enabled": False}, **_DEFAULTS)
    assert r.stats_enabled is False
    # Independent of the other knobs.
    r2 = parse_table_options({"stats_enabled": False, "bot_pacing": "slow"}, **_DEFAULTS)
    assert r2.stats_enabled is False
    assert (r2.bot_min_delay_s, r2.bot_max_delay_s) == (15.0, 30.0)


@pytest.mark.parametrize(
    "preset,expected",
    [("fast", (0.5, 1.5)), ("normal", (5.0, 10.0)), ("slow", (15.0, 30.0))],
)
def test_pacing_presets(preset: str, expected: tuple[float, float]) -> None:
    r = parse_table_options({"bot_pacing": preset}, **_DEFAULTS)
    assert (r.bot_min_delay_s, r.bot_max_delay_s) == expected
    assert r.bot_pacing_enabled is True


def test_custom_pacing() -> None:
    r = parse_table_options({"bot_pacing": {"min_s": 1.0, "max_s": 2.0}}, **_DEFAULTS)
    assert (r.bot_min_delay_s, r.bot_max_delay_s) == (1.0, 2.0)


def test_decide_timeout_override_affects_human_discard_only() -> None:
    r = parse_table_options({"decide_timeout_seconds": 90}, **_DEFAULTS)
    assert r.decide_timeouts.human_discard_s == 90.0
    # CLAIM and bot deadlines stay at server defaults.
    assert r.decide_timeouts.human_claim_s == 20.0
    assert r.decide_timeouts.bot_s == 30.0


def test_timeouts_disabled_makes_human_deadlines_effectively_unlimited() -> None:
    r = parse_table_options({"timeouts_enabled": False}, **_DEFAULTS)
    assert r.decide_timeouts.human_discard_s > 1_000_000
    assert r.decide_timeouts.human_claim_s > 1_000_000
    # Bots still have their deadline (they always respond promptly).
    assert r.decide_timeouts.bot_s == 30.0


def test_timeouts_disabled_overrides_explicit_decide_timeout() -> None:
    """timeouts_enabled=false wins over a decide_timeout_seconds value."""
    r = parse_table_options({"timeouts_enabled": False, "decide_timeout_seconds": 90}, **_DEFAULTS)
    assert r.decide_timeouts.human_discard_s > 1_000_000


@pytest.mark.parametrize(
    "bad",
    [
        {"bot_pacing": "lightning"},
        {"bot_pacing": {"min_s": -1.0, "max_s": 2.0}},
        {"bot_pacing": {"min_s": 5.0, "max_s": 1.0}},
        {"bot_pacing": {"min_s": 0.0, "max_s": 100.0}},
        {"bot_pacing": {"min_s": "x", "max_s": 2.0}},
        {"decide_timeout_seconds": 1.0},
        {"decide_timeout_seconds": 9999.0},
        {"decide_timeout_seconds": "soon"},
        ["not", "an", "object"],
    ],
)
def test_invalid_options_rejected(bad: object) -> None:
    with pytest.raises(TableOptionsError):
        parse_table_options(bad, **_DEFAULTS)
