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
    # Default data dir is the absolute XDG path, not a CWD-relative one, so the
    # launch directory can never silently swap which SQLite DB is opened.
    assert cfg.data_dir == Path.home() / ".local" / "share" / "mahjong-server"
    assert cfg.data_dir.is_absolute()
    assert cfg.seat_hold_seconds == 180  # raised from 60 for FB-03 rejoin (reconnect-rejoin.md)
    assert cfg.session_lifetime_hours == 336
    assert cfg.default_ruleset == "mcr-2006"
    assert cfg.log_format == "json"
    assert unknown == []


def test_trust_proxy_defaults_off() -> None:
    cfg, _ = load_config_from_env(env={})
    assert cfg.trust_proxy is False


def test_trust_proxy_parses_truthy() -> None:
    cfg, _ = load_config_from_env(env={"MAHJONG_TRUST_PROXY": "1"})
    assert cfg.trust_proxy is True
    cfg, _ = load_config_from_env(env={"MAHJONG_TRUST_PROXY": "true"})
    assert cfg.trust_proxy is True


def test_listen_addr_split() -> None:
    cfg, _ = load_config_from_env(env={"MAHJONG_LISTEN_ADDR": "0.0.0.0:9000"})
    assert cfg.listen_host == "0.0.0.0"
    assert cfg.listen_port == 9000
    assert cfg.listen_addr == "0.0.0.0:9000"


def test_derived_paths() -> None:
    cfg, _ = load_config_from_env(env={"MAHJONG_DATA_DIR": "/tmp/mj"})
    assert cfg.db_path == Path("/tmp/mj/mahjong.db")
    assert cfg.records_dir == Path("/tmp/mj/records")


def test_default_data_dir_honours_xdg_data_home() -> None:
    cfg, _ = load_config_from_env(env={"XDG_DATA_HOME": "/srv/state"})
    assert cfg.data_dir == Path("/srv/state/mahjong-server")
    assert cfg.data_dir.is_absolute()


def test_explicit_data_dir_expands_tilde() -> None:
    cfg, _ = load_config_from_env(env={"MAHJONG_DATA_DIR": "~/mjdata"})
    assert cfg.data_dir == Path.home() / "mjdata"
    assert cfg.data_dir.is_absolute()


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
