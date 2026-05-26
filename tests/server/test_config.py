"""Tests for ServerConfig env-var loader (spec server-lifecycle.md fixtures 1-3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mahjong.server.config import ConfigError, ServerConfig, load_config_from_env


def test_defaults_load_cleanly() -> None:
    cfg, unknown = load_config_from_env(env={})
    assert isinstance(cfg, ServerConfig)
    assert cfg.listen_host == "127.0.0.1"
    assert cfg.listen_port == 8400
    assert cfg.data_dir == Path("./var/mahjong")
    assert cfg.seat_hold_seconds == 60
    assert cfg.session_lifetime_hours == 336
    assert cfg.default_ruleset == "mcr-2006"
    assert cfg.log_format == "json"
    assert unknown == []


def test_listen_addr_split() -> None:
    cfg, _ = load_config_from_env(env={"MAHJONG_LISTEN_ADDR": "0.0.0.0:9000"})
    assert cfg.listen_host == "0.0.0.0"
    assert cfg.listen_port == 9000
    assert cfg.listen_addr == "0.0.0.0:9000"


def test_derived_paths() -> None:
    cfg, _ = load_config_from_env(env={"MAHJONG_DATA_DIR": "/tmp/mj"})
    assert cfg.db_path == Path("/tmp/mj/mahjong.db")
    assert cfg.records_dir == Path("/tmp/mj/records")


def test_bad_int_raises_config_error_with_var_name() -> None:
    with pytest.raises(ConfigError, match="MAHJONG_SEAT_HOLD_SECONDS"):
        load_config_from_env(env={"MAHJONG_SEAT_HOLD_SECONDS": "banana"})


def test_bad_listen_addr_raises() -> None:
    with pytest.raises(ConfigError, match="MAHJONG_LISTEN_ADDR"):
        load_config_from_env(env={"MAHJONG_LISTEN_ADDR": "not-a-host-port"})


def test_unknown_mahjong_var_returned_as_warning() -> None:
    cfg, unknown = load_config_from_env(
        env={"MAHJONG_HARTBEAT_INTERVAL_SECONDS": "30"}
    )
    assert "MAHJONG_HARTBEAT_INTERVAL_SECONDS" in unknown
    assert isinstance(cfg, ServerConfig)
