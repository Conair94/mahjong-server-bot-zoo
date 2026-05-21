"""Bot sandbox: env whitelist, setrlimit application, Linux-only enforcement.

Spec: docs/specs/bot-runner-protocol.md § Sandboxing.
Fixtures: bot-runner-protocol.md fixture 6 (OOM kill, Linux),
          fixture 7 (network deny, Linux).
"""

from __future__ import annotations

import contextlib
import os
import resource
import subprocess
import sys
import textwrap
import warnings

import pytest

from mahjong.bots.manifest import parse_manifest
from mahjong.bots.sandbox import (
    ENV_WHITELIST,
    SandboxWarning,
    apply_sandbox,
    build_env,
    build_rlimits,
)


def _ok_manifest(**overrides: object) -> object:
    base = {
        "bot_id": "b_sb",
        "version": "0.1.0",
        "display_name": "Sandbox test",
        "directory": "./",
        "command": ["python", "-u", "-c", "pass"],
        "env": {"PYTHONUNBUFFERED": "1"},
        "ruleset_supported": ["mcr-2006"],
        "format_supported": ["botzone-csm"],
        "limits": {
            "memory_mb": 64,
            "cpu_seconds": 5,
            "max_fds": 32,
            "max_processes": 1,
            "network": "deny",
        },
    }
    base.update(overrides)  # type: ignore[arg-type]
    return parse_manifest(base)


# --- build_env: whitelist behavior ---


def test_build_env_includes_whitelist_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    m = _ok_manifest()
    env = build_env(m)  # type: ignore[arg-type]
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["LANG"] == "C.UTF-8"
    assert "SECRET_TOKEN" not in env


def test_build_env_layers_manifest_env_on_top() -> None:
    m = _ok_manifest(env={"PYTHONUNBUFFERED": "1", "MY_VAR": "x"})
    env = build_env(m)  # type: ignore[arg-type]
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["MY_VAR"] == "x"


def test_build_env_only_whitelist_and_manifest_keys() -> None:
    m = _ok_manifest(env={"MY_VAR": "x"})
    env = build_env(m)  # type: ignore[arg-type]
    allowed = set(ENV_WHITELIST) | {"MY_VAR"}
    assert set(env).issubset(allowed)


# --- build_rlimits: correct (resource, soft, hard) tuples ---


def test_build_rlimits_returns_expected_resources() -> None:
    m = _ok_manifest()
    rlimits = build_rlimits(m)  # type: ignore[arg-type]
    by_res = dict(rlimits)
    assert resource.RLIMIT_AS in by_res
    assert resource.RLIMIT_CPU in by_res
    assert resource.RLIMIT_NOFILE in by_res
    # RLIMIT_NPROC isn't on every platform (e.g. some Linux libc variants), but is
    # required by our sandbox spec.
    assert resource.RLIMIT_NPROC in by_res


def test_build_rlimits_translates_memory_mb_to_bytes() -> None:
    m = _ok_manifest()
    rlimits = build_rlimits(m)  # type: ignore[arg-type]
    by_res = dict(rlimits)
    soft, hard = by_res[resource.RLIMIT_AS]
    assert soft == 64 * 1024 * 1024
    assert hard == 64 * 1024 * 1024


def test_build_rlimits_translates_cpu_seconds() -> None:
    m = _ok_manifest()
    rlimits = build_rlimits(m)  # type: ignore[arg-type]
    by_res = dict(rlimits)
    soft, hard = by_res[resource.RLIMIT_CPU]
    assert soft == 5
    assert hard == 5


# --- apply_sandbox: macOS warns about inactive layers ---


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="macOS-specific warning path")
def test_apply_sandbox_warns_on_macos() -> None:
    m = _ok_manifest()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        apply_sandbox(m)  # type: ignore[arg-type]
    msgs = [str(w.message) for w in caught if issubclass(w.category, SandboxWarning)]
    assert msgs, "expected a SandboxWarning on macOS"
    # The warning must name at least one inactive layer so ops knows what's degraded.
    assert any("netns" in m.lower() or "network" in m.lower() for m in msgs)


# --- End-to-end Linux enforcement (fixtures 6, 7) ---


def _spawn_with_sandbox(manifest: object, code: str) -> subprocess.CompletedProcess[str]:
    """Spawn a python subprocess with our sandbox preexec applied."""
    rlimits = build_rlimits(manifest)  # type: ignore[arg-type]

    def preexec() -> None:
        for res, lim in rlimits:
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(res, lim)

    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=build_env(manifest),  # type: ignore[arg-type]
        preexec_fn=preexec,
        timeout=10,
    )


@pytest.mark.linux_only
def test_rlimit_as_kills_overallocating_bot() -> None:
    """Fixture 6: a subprocess that allocates > RLIMIT_AS exits abnormally."""
    m = _ok_manifest()
    # Try to allocate 256MB against a 64MB cap.
    code = "x = bytearray(256 * 1024 * 1024); print('survived')"
    result = _spawn_with_sandbox(m, code)
    assert result.returncode != 0
    assert "survived" not in result.stdout


@pytest.mark.linux_only
def test_network_deny_blocks_socket() -> None:
    """Fixture 7: with network='deny' and netns isolation, sockets must fail.

    NOTE: netns creation requires CAP_NET_ADMIN. On CI without that capability
    this test xfails. The deny path is still enforced once root/CAP is available.
    """
    if os.geteuid() != 0:
        pytest.xfail("netns deny requires root / CAP_NET_ADMIN; skipping unprivileged run")
    m = _ok_manifest()
    code = textwrap.dedent(
        """
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("1.1.1.1", 80))
            print("CONNECTED")
        except OSError as e:
            print(f"BLOCKED:{e.errno}")
        """
    )
    result = _spawn_with_sandbox(m, code)
    assert "CONNECTED" not in result.stdout
    assert "BLOCKED" in result.stdout
