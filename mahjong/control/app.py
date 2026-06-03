"""ControlApp — assembles and runs the control plane.

Spec: docs/specs/admin-console.md § 2, § Bootstrapping, § Configuration.

Wires together the supervisor, metrics sampler, log buffer, admin-status fetch,
and the admin web server, then runs until a signal.  The control plane generates
a fresh ``MAHJONG_ADMIN_TOKEN`` and injects it into the ``serve`` child's
environment, so it can read the token-gated ``/admin/status`` while the operator
never handles the secret.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import signal
import sys
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mahjong.control import static_root
from mahjong.control.health import HealthMonitor
from mahjong.control.logbuffer import LogRingBuffer
from mahjong.control.metrics import MetricsSampler
from mahjong.control.plane import AdminStatusFetch, ControlPlane
from mahjong.control.server import AdminWebServer
from mahjong.control.services import AdminDataService
from mahjong.control.supervisor import ServerSupervisor, make_http_readiness_probe
from mahjong.persistence import Persistence
from mahjong.server.config import load_config_from_env

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControlConfig:
    """Control-plane knobs (``MAHJONG_CTL_*``)."""

    ctl_host: str = "127.0.0.1"
    ctl_port: int = 8500
    metrics_interval_s: float = 2.0
    log_buffer_lines: int = 2000
    startup_timeout_s: float = 15.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ControlConfig:
        addr = env.get("MAHJONG_CTL_LISTEN_ADDR", "127.0.0.1:8500")
        host, _, port_s = addr.rpartition(":")
        return cls(
            ctl_host=host or "127.0.0.1",
            ctl_port=int(port_s or "8500"),
            metrics_interval_s=float(env.get("MAHJONG_CTL_METRICS_INTERVAL_S", "2.0")),
            log_buffer_lines=int(env.get("MAHJONG_CTL_LOG_BUFFER_LINES", "2000")),
            startup_timeout_s=float(env.get("MAHJONG_CTL_STARTUP_TIMEOUT_S", "15")),
        )


def _loopback_status_url(listen_addr: str) -> str:
    """Build the loopback /admin/status URL from the server's listen addr.

    The server may bind 0.0.0.0, but the control plane is co-located, so it always
    probes via 127.0.0.1."""
    _, _, port = listen_addr.rpartition(":")
    return f"http://127.0.0.1:{port}/admin/status"


def make_admin_status_fetch(url: str, token: str) -> AdminStatusFetch:
    """Async fetch of the server's /admin/status JSON (None if unreachable)."""

    def _blocking() -> dict[str, Any] | None:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status != 200:
                    return None
                data: dict[str, Any] = json.loads(resp.read())
                return data
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return None

    async def fetch() -> dict[str, Any] | None:
        return await asyncio.get_running_loop().run_in_executor(None, _blocking)

    return fetch


class ControlApp:
    """Owns the assembled control plane and its lifecycle."""

    def __init__(
        self,
        *,
        config: ControlConfig,
        server_env: Mapping[str, str],
        server_listen_addr: str,
        static_dir: Path | None = None,
    ) -> None:
        self._config = config
        token = secrets.token_urlsafe(32)
        status_url = _loopback_status_url(server_listen_addr)

        # Open the server's DB for invite/account ops (WAL → safe alongside the
        # running serve child). Long-lived; closed on shutdown.
        server_cfg, _unknown = load_config_from_env(server_env)
        server_cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self._persistence = Persistence(server_cfg.db_path, server_cfg.data_dir)
        data = AdminDataService(self._persistence)
        health = HealthMonitor(persistence=self._persistence, db_path=server_cfg.db_path)

        # The child is a `serve`, not a `control`; strip our MAHJONG_CTL_* vars so
        # it doesn't log them as unknown-config warnings (which would then show up
        # in the console's own Logs pane).
        child_env = {
            k: v for k, v in server_env.items() if not k.startswith("MAHJONG_CTL_")
        }
        self._log_buffer = LogRingBuffer(maxlen=config.log_buffer_lines)
        self._supervisor = ServerSupervisor(
            argv=[sys.executable, "-m", "mahjong", "serve"],
            env={**child_env, "MAHJONG_ADMIN_TOKEN": token},
            readiness_probe=make_http_readiness_probe(status_url, token),
            startup_timeout_s=config.startup_timeout_s,
            on_log=self._on_log,
        )
        self._metrics = MetricsSampler(
            pid_provider=lambda: self._supervisor.pid,
            interval_s=config.metrics_interval_s,
        )
        self._plane = ControlPlane(
            supervisor=self._supervisor,
            metrics=self._metrics,
            admin_status_fetch=make_admin_status_fetch(status_url, token),
            server_listen_url=f"ws://{server_listen_addr}",
            data=data,
            log_buffer=self._log_buffer,
            health_monitor=health,
        )
        self._web = AdminWebServer(
            plane=self._plane,
            host=config.ctl_host,
            port=config.ctl_port,
            static_dir=static_dir if static_dir is not None else static_root(),
            status_interval_s=config.metrics_interval_s,
        )

    def _on_log(self, text: str, stream: str) -> None:
        self._log_buffer.append(text, stream)

    @property
    def url(self) -> str:
        return f"http://{self._config.ctl_host}:{self._web.port}/"

    async def start(self) -> None:
        """Bring up the metrics sampler + admin web server (no server child yet)."""
        await self._metrics.start()
        await self._web.start()
        _logger.info("control.ready url=%s", self.url)

    async def aclose(self) -> None:
        await self._shutdown()

    async def run(self, *, autostart_server: bool = False, open_browser: bool = False) -> int:
        await self.start()
        print(f"mahjong control console: {self.url}", file=sys.stderr)

        if autostart_server:
            print("starting server…", file=sys.stderr)
            ok = await self._supervisor.start()
            print(f"server {'started' if ok else 'FAILED to start'}", file=sys.stderr)
        if open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(self.url)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        try:
            await stop.wait()
        finally:
            await self._shutdown()
        return 0

    async def _shutdown(self) -> None:
        _logger.info("control.draining")
        await self._supervisor.stop()
        await self._metrics.stop()
        await self._web.close()
        with contextlib.suppress(Exception):
            self._persistence.close()


__all__ = ["ControlApp", "ControlConfig", "make_admin_status_fetch"]
