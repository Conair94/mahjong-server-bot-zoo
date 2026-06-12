"""TunnelSupervisor — owns an optional ``cloudflared`` quick tunnel.

Spec: docs/specs/admin-console.md § 2 (TunnelSupervisor), § Non-goals (tunnel
control), fixture ``tunnel_url_parse``.

A *quick tunnel* (``cloudflared tunnel --url http://127.0.0.1:<port>``) gives the
server a public ``*.trycloudflare.com`` URL with no Cloudflare account — handy for
ad-hoc remote play.  ``cloudflared`` is an **external binary**, not a pip dep, so
the supervisor degrades gracefully: if it's not installed, ``start`` returns a
``cloudflared_not_found`` error rather than raising, and the GUI shows that.

This is a smaller cousin of ``ServerSupervisor``: spawn, scrape the URL from the
child's output, keep draining the pipe so it can't block, and SIGTERM on stop.
The drain loop also keeps the URL **current** — a quick tunnel re-registers with
a fresh ``*.trycloudflare.com`` hostname on an edge reconnect, so pinning only
the first-scraped URL would leave the console advertising a dead address.
The URL parser (``parse_tunnel_url``) is a pure function so the contract is
unit-testable against a recorded cloudflared line.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Sequence
from typing import Any

_logger = logging.getLogger(__name__)

# cloudflared prints the quick-tunnel URL inside an ASCII box; we just grab the
# first https://<sub>.trycloudflare.com token on any line.
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


def parse_tunnel_url(line: str) -> str | None:
    """Return the ``trycloudflare.com`` URL in *line*, or None if absent."""
    m = _URL_RE.search(line)
    return m.group(0) if m else None


def cloudflared_argv(target_url: str, *, binary: str = "cloudflared") -> list[str]:
    """The quick-tunnel command pointing at the local server."""
    return [binary, "tunnel", "--url", target_url]


class TunnelSupervisor:
    def __init__(
        self,
        *,
        argv: Sequence[str],
        url_timeout_s: float = 20.0,
        shutdown_timeout_s: float = 5.0,
    ) -> None:
        self._argv = list(argv)
        self._url_timeout_s = url_timeout_s
        self._shutdown_timeout_s = shutdown_timeout_s

        self._proc: asyncio.subprocess.Process | None = None
        self._url: str | None = None
        self._error: str | None = None
        self._drain: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # --- read-only state for the plane's STATUS frame ---

    def to_wire(self) -> dict[str, Any]:
        return {
            "running": self._proc is not None and self._proc.returncode is None,
            "url": self._url,
            "error": self._error,
        }

    # --- lifecycle ---

    async def start(self) -> dict[str, Any]:
        """Spawn cloudflared and scrape its public URL.  Returns a wire dict.

        Never raises: a missing binary or a timeout becomes an ``error`` field so
        the console can surface it inline."""
        async with self._lock:
            if self._proc is not None and self._proc.returncode is None:
                return self.to_wire()
            self._url = None
            self._error = None
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *self._argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,  # cloudflared logs to stderr
                )
            except FileNotFoundError:
                self._error = "cloudflared_not_found"
                self._proc = None
                return self.to_wire()

            url = await self._scrape_url()
            if url is None:
                await self._terminate()
                if self._error is None:
                    self._error = "tunnel_url_timeout"
                return self.to_wire()
            self._url = url
            # Keep draining the pipe so the OS buffer can't fill and stall the
            # child; without this, cloudflared blocks on its next log write.
            self._drain = asyncio.create_task(self._drain_output())
            return self.to_wire()

    async def stop(self) -> None:
        async with self._lock:
            if self._drain is not None:
                self._drain.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._drain
                self._drain = None
            await self._terminate()
            self._url = None

    # --- internals ---

    async def _scrape_url(self) -> str | None:
        """Read child output lines until a URL appears or the deadline passes."""
        assert self._proc is not None and self._proc.stdout is not None
        try:
            return await asyncio.wait_for(self._read_until_url(), timeout=self._url_timeout_s)
        except TimeoutError:
            _logger.warning("tunnel.url_timeout argv=%s", self._argv[:2])
            return None

    async def _read_until_url(self) -> str | None:
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        while True:
            raw = await stream.readline()
            if not raw:  # child exited before printing a URL
                if self._proc.returncode is not None:
                    self._error = "cloudflared_exited"
                return None
            url = parse_tunnel_url(raw.decode("utf-8", errors="replace"))
            if url is not None:
                return url

    async def _drain_output(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        with contextlib.suppress(Exception):
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    return
                # Keep watching for URL changes. A quick tunnel re-registers
                # with a NEW *.trycloudflare.com hostname when its edge
                # connection drops; if we kept only the first-scraped URL the
                # console would advertise a dead (NXDOMAIN) address after a
                # reconnect. Draining is still mandatory (an unread pipe stalls
                # the child) — we just no longer throw the lines away blind.
                url = parse_tunnel_url(raw.decode("utf-8", errors="replace"))
                if url is not None and url != self._url:
                    _logger.info("tunnel.url_changed url=%s", url)
                    self._url = url

    async def _terminate(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=self._shutdown_timeout_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()


__all__ = ["TunnelSupervisor", "cloudflared_argv", "parse_tunnel_url"]
