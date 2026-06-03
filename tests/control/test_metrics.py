"""MetricsSampler — CPU% and RSS of the supervised process via psutil.

Spec: docs/specs/admin-console.md § 2 (metrics sampler).  Tested against *this*
test process's own PID — real psutil reads, no mocks.
"""

from __future__ import annotations

import asyncio
import os

import psutil
import pytest

from mahjong.control.metrics import MetricsSampler

pytestmark = pytest.mark.asyncio


def _dead_pid() -> int:
    """A PID that does not currently exist."""
    candidate = 999_999
    while psutil.pid_exists(candidate):
        candidate -= 1
    return candidate


async def test_sample_once_reads_real_process() -> None:
    sampler = MetricsSampler(pid_provider=os.getpid)
    sampler.sample_once()  # primes cpu_percent (first call returns 0.0)
    metrics = sampler.sample_once()
    assert metrics is not None
    assert metrics.mem_rss_bytes > 0
    assert metrics.cpu_pct >= 0.0
    assert sampler.latest is metrics


async def test_sample_none_when_no_pid() -> None:
    sampler = MetricsSampler(pid_provider=lambda: None)
    assert sampler.sample_once() is None
    assert sampler.latest is None


async def test_sample_handles_dead_pid_gracefully() -> None:
    dead = _dead_pid()
    sampler = MetricsSampler(pid_provider=lambda: dead)
    assert sampler.sample_once() is None  # no psutil.NoSuchProcess leak


async def test_background_loop_updates_latest() -> None:
    sampler = MetricsSampler(pid_provider=os.getpid, interval_s=0.05)
    await sampler.start()
    try:
        # Let the loop tick a couple of times (cpu_percent needs two samples).
        await asyncio.sleep(0.2)
        assert sampler.latest is not None
        assert sampler.latest.mem_rss_bytes > 0
    finally:
        await sampler.stop()
