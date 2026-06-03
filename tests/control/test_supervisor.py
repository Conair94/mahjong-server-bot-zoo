"""ServerSupervisor — the subprocess lifecycle for `serve`.

Spec: docs/specs/admin-console.md § 2 (ServerSupervisor), fixture
``supervisor_lifecycle``.

These unit tests drive the supervisor with *fake* children (a sleeping/exiting
``python -c`` one-liner) and an injected readiness probe, so they're fast and
don't bind a port.  A real-`serve` integration test lives in
``test_supervisor_integration.py`` (marked slow).
"""

from __future__ import annotations

import os
import sys

import pytest

from mahjong.control.supervisor import ServerState, ServerSupervisor

pytestmark = pytest.mark.asyncio

# Fake children.
_SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]
_CRASHER = [sys.executable, "-c", "import sys; sys.exit(3)"]


async def _always_ready() -> bool:
    return True


async def _never_ready() -> bool:
    return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_start_reaches_running() -> None:
    sup = ServerSupervisor(argv=_SLEEPER, readiness_probe=_always_ready)
    ok = await sup.start()
    try:
        assert ok is True
        assert sup.state is ServerState.RUNNING
        assert sup.pid is not None and _pid_alive(sup.pid)
        assert sup.started_at_monotonic is not None
    finally:
        await sup.stop()


async def test_stop_terminates_child() -> None:
    sup = ServerSupervisor(argv=_SLEEPER, readiness_probe=_always_ready)
    await sup.start()
    pid = sup.pid
    assert pid is not None
    await sup.stop()
    assert sup.state is ServerState.STOPPED
    assert not _pid_alive(pid)


async def test_restart_cycles_to_running() -> None:
    sup = ServerSupervisor(argv=_SLEEPER, readiness_probe=_always_ready)
    await sup.start()
    first_pid = sup.pid
    try:
        ok = await sup.restart()
        assert ok is True
        assert sup.state is ServerState.RUNNING
        assert sup.pid is not None
        assert sup.pid != first_pid  # genuinely a new process
        assert first_pid is not None and not _pid_alive(first_pid)
    finally:
        await sup.stop()


async def test_child_exit_while_running_flips_to_crashed() -> None:
    """A child that dies unexpectedly after reaching RUNNING → CRASHED."""
    # Child runs ~0.3s then exits non-zero; probe reports ready immediately so we
    # reach RUNNING first, then the watcher observes the unexpected exit.
    argv = [sys.executable, "-c", "import time,sys; time.sleep(0.3); sys.exit(2)"]
    sup = ServerSupervisor(argv=argv, readiness_probe=_always_ready)
    await sup.start()
    assert sup.state is ServerState.RUNNING
    # Wait for the child to die and the watcher to react.
    await sup.wait_for_exit(timeout=5.0)
    assert sup.state is ServerState.CRASHED


async def test_crash_during_startup_returns_false() -> None:
    """A child that exits before becoming ready → start() fails, state CRASHED."""
    sup = ServerSupervisor(argv=_CRASHER, readiness_probe=_never_ready)
    ok = await sup.start()
    assert ok is False
    assert sup.state is ServerState.CRASHED


async def test_startup_timeout_kills_child_and_reports_failure() -> None:
    """Child stays alive but never signals ready → timeout, child killed, STOPPED."""
    sup = ServerSupervisor(
        argv=_SLEEPER, readiness_probe=_never_ready, startup_timeout_s=0.5
    )
    ok = await sup.start()
    pid = sup.pid
    assert ok is False
    assert sup.state is ServerState.STOPPED
    assert pid is not None and not _pid_alive(pid)


async def test_log_lines_forwarded_to_callback() -> None:
    """Child stdout lines reach the on_log callback (feeds the ring buffer)."""
    lines: list[tuple[str, str]] = []
    argv = [sys.executable, "-c", "print('hello-from-child', flush=True)\nimport time; time.sleep(30)"]
    sup = ServerSupervisor(
        argv=argv,
        readiness_probe=_always_ready,
        on_log=lambda text, stream: lines.append((text, stream)),
    )
    await sup.start()
    try:
        await sup.wait_for_log("hello-from-child", timeout=5.0)
        assert any("hello-from-child" in t for t, _ in lines)
    finally:
        await sup.stop()
