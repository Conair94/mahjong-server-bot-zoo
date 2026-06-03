"""ServerSupervisor — owns the `serve` child process.

Spec: docs/specs/admin-console.md § 2 (ServerSupervisor).

A process can't start itself, so the control plane (this process) supervises the
game server as a *child*.  This is the classic **supervisor** pattern (cf.
systemd / pm2): spawn, watch, restart.  The supervisor is deliberately generic —
it takes an ``argv``, an ``env``, and an async ``readiness_probe`` — so it's
unit-testable with a fake child and the real wiring (real `serve`, HTTP probe)
is injected by the control-plane layer.

State machine:

    STOPPED ──start()──▶ STARTING ──ready──▶ RUNNING
       ▲                    │                  │
       │                    │ exit/timeout     │ stop()
       │                    ▼                  ▼
       └──────────────── STOPPED ◀──────── STOPPING
                            ▲                  │
                            └── (graceful)     │
    RUNNING ── child dies unexpectedly ─▶ CRASHED

``stop()`` cancels the crash watcher *before* terminating, so a deliberate stop
never masquerades as a crash.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping, Sequence

_logger = logging.getLogger(__name__)

# Async predicate: "is the server actually up and serving?"  Returns True once
# ready; the supervisor polls it during STARTING.
ReadinessProbe = Callable[[], Awaitable[bool]]
# (text, stream_name) — stream_name is "stdout" or "stderr".
LogCallback = Callable[[str, str], None]

_POLL_INTERVAL_S = 0.1


class ServerState(enum.Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    CRASHED = "CRASHED"


class ServerSupervisor:
    """Supervise a single ``serve`` child process.

    Parameters
    ----------
    argv:
        The command to spawn (e.g. ``[sys.executable, "-m", "mahjong", "serve"]``).
    env:
        Environment for the child.  ``None`` inherits the parent's environment.
    readiness_probe:
        Async ``() -> bool`` polled during STARTING until it returns True (server
        is up) or the startup timeout elapses.
    startup_timeout_s / shutdown_timeout_s:
        Deadlines for reaching RUNNING and for graceful SIGTERM before SIGKILL.
    on_log:
        Optional callback invoked once per captured child output line.
    """

    def __init__(
        self,
        *,
        argv: Sequence[str],
        env: Mapping[str, str] | None = None,
        readiness_probe: ReadinessProbe,
        startup_timeout_s: float = 15.0,
        shutdown_timeout_s: float = 10.0,
        on_log: LogCallback | None = None,
    ) -> None:
        self._argv = list(argv)
        self._env = dict(env) if env is not None else None
        self._readiness_probe = readiness_probe
        self._startup_timeout_s = startup_timeout_s
        self._shutdown_timeout_s = shutdown_timeout_s
        self._on_log = on_log

        self._state = ServerState.STOPPED
        self._proc: asyncio.subprocess.Process | None = None
        self._started_at_monotonic: float | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._pumps: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()
        self._exited = asyncio.Event()
        # Most-recent captured output lines (for wait_for_log + readiness debug).
        # The bounded ring buffer that feeds the GUI lives in the control plane;
        # this is just a small tail the supervisor keeps for its own use.
        self._recent: list[str] = []

    # --- public read-only state ---

    @property
    def state(self) -> ServerState:
        return self._state

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def started_at_monotonic(self) -> float | None:
        return self._started_at_monotonic

    # --- lifecycle ---

    async def start(self) -> bool:
        """Spawn the child and block until RUNNING.  Returns True on success.

        On failure: CRASHED if the child exited during startup, STOPPED if it
        stayed up but never became ready (killed on timeout).
        """
        async with self._lock:
            if self._state in (ServerState.STARTING, ServerState.RUNNING):
                return True
            self._state = ServerState.STARTING
            self._exited.clear()
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
            self._spawn_pumps()

            deadline = time.monotonic() + self._startup_timeout_s
            while True:
                if self._proc.returncode is not None:
                    # Exited before becoming ready → a startup crash.
                    self._state = ServerState.CRASHED
                    return False
                if await self._safe_probe():
                    break
                if time.monotonic() > deadline:
                    _logger.warning("supervisor.startup_timeout argv=%s", self._argv[:3])
                    await self._terminate()
                    self._state = ServerState.STOPPED
                    return False
                await asyncio.sleep(_POLL_INTERVAL_S)

            self._state = ServerState.RUNNING
            self._started_at_monotonic = time.monotonic()
            self._watcher = asyncio.create_task(self._watch())
            return True

    async def stop(self) -> None:
        """Gracefully stop the child (SIGTERM → wait → SIGKILL).  Idempotent."""
        async with self._lock:
            if self._proc is None or self._state in (
                ServerState.STOPPED,
                ServerState.CRASHED,
            ):
                self._state = ServerState.STOPPED
                return
            self._state = ServerState.STOPPING
            # Cancel the crash watcher first so a deliberate stop isn't seen as a
            # crash when wait() returns.
            if self._watcher is not None:
                self._watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._watcher
                self._watcher = None
            await self._terminate()
            self._state = ServerState.STOPPED
            self._started_at_monotonic = None

    async def restart(self) -> bool:
        await self.stop()
        return await self.start()

    # --- test / caller synchronisation helpers ---

    async def wait_for_exit(self, *, timeout: float | None = None) -> None:
        """Block until the child process has exited (for any reason)."""
        await asyncio.wait_for(self._exited.wait(), timeout=timeout)

    async def wait_for_log(self, needle: str, *, timeout: float = 5.0) -> None:
        """Block until a captured log line contains *needle* (test helper)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in "".join(self._recent):
                return
            await asyncio.sleep(0.02)
        raise TimeoutError(f"log line containing {needle!r} not seen within {timeout}s")

    # --- internals ---

    async def _safe_probe(self) -> bool:
        try:
            return await self._readiness_probe()
        except Exception:  # a probe error is just "not ready yet"
            return False

    def _spawn_pumps(self) -> None:
        assert self._proc is not None
        self._recent.clear()
        self._pumps = []
        if self._proc.stdout is not None:
            self._pumps.append(asyncio.create_task(self._pump(self._proc.stdout, "stdout")))
        if self._proc.stderr is not None:
            self._pumps.append(asyncio.create_task(self._pump(self._proc.stderr, "stderr")))

    async def _pump(self, stream: asyncio.StreamReader, name: str) -> None:
        while True:
            raw = await stream.readline()
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").rstrip("\n")
            self._recent.append(text)
            if self._on_log is not None:
                with contextlib.suppress(Exception):
                    self._on_log(text, name)

    async def _watch(self) -> None:
        """Background: an exit observed here (watcher not cancelled) is a crash."""
        assert self._proc is not None
        try:
            await self._proc.wait()
        except asyncio.CancelledError:
            return
        self._exited.set()
        if self._state is ServerState.RUNNING:
            _logger.error("supervisor.child_crashed returncode=%s", self._proc.returncode)
            self._state = ServerState.CRASHED

    async def _terminate(self) -> None:
        """SIGTERM then SIGKILL the child; wait for it to reap."""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            self._exited.set()
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=self._shutdown_timeout_s)
        except TimeoutError:
            _logger.warning("supervisor.sigkill_escalation pid=%s", proc.pid)
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        self._exited.set()


def make_http_readiness_probe(url: str, token: str) -> ReadinessProbe:
    """A readiness probe that succeeds once ``GET url`` returns HTTP 200 with the
    admin Bearer token.  Used against the server's ``/admin/status`` route."""

    def _blocking() -> bool:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return bool(resp.status == 200)
        except (urllib.error.URLError, OSError):
            return False

    async def probe() -> bool:
        return await asyncio.get_running_loop().run_in_executor(None, _blocking)

    return probe


__all__ = [
    "LogCallback",
    "ReadinessProbe",
    "ServerState",
    "ServerSupervisor",
    "make_http_readiness_probe",
]
