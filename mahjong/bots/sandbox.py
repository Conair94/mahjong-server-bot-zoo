"""Subprocess sandbox preparation.

Spec: docs/specs/bot-runner-protocol.md § Sandboxing.

The sandbox is layered (env whitelist + setrlimit + Linux netns); each layer
catches a different failure mode. macOS dev hosts run with reduced enforcement
and a recorded SandboxWarning so degraded layers are visible to ops.

This module is preparation-only: it builds the env dict and the rlimit tuples
the runner will apply, and exposes `apply_sandbox` for the BotRunnerAdapter
(Step 5.2) to call from a `preexec_fn`. No subprocess is spawned here.
"""

from __future__ import annotations

import resource
import sys
import warnings
from collections.abc import Sequence

from mahjong.bots.manifest import BotManifest

# Hardcoded env keys allowed through to bot subprocesses, in addition to
# whatever the manifest's `env` block declares. The spec pins these two.
ENV_WHITELIST: tuple[str, ...] = ("PATH", "LANG")


class SandboxWarning(UserWarning):
    """Emitted when one or more sandbox layers are not enforceable on the
    current host (macOS dev workflow). Production hosts (Linux) must not
    trigger this."""


# (resource, (soft, hard)) tuples, ready to feed into resource.setrlimit.
RlimitSpec = tuple[int, tuple[int, int]]


def build_env(manifest: BotManifest) -> dict[str, str]:
    """Construct the child process's environment.

    Only ENV_WHITELIST keys from the parent + the manifest's declared env.
    Nothing else leaks (no inherited secrets, no LD_PRELOAD etc.).
    """
    import os

    env: dict[str, str] = {}
    for key in ENV_WHITELIST:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env.update(manifest.env)
    return env


def build_rlimits(manifest: BotManifest) -> Sequence[RlimitSpec]:
    """Translate manifest limits to setrlimit arguments.

    RLIMIT_AS caps virtual memory (bytes), RLIMIT_CPU caps total CPU seconds,
    RLIMIT_NOFILE caps file descriptors, RLIMIT_NPROC caps fork count.
    Each is set with soft == hard to prevent the bot from raising its own
    limits at runtime.
    """
    bytes_per_mb = 1024 * 1024
    memory_bytes = manifest.limits.memory_mb * bytes_per_mb
    return (
        (resource.RLIMIT_AS, (memory_bytes, memory_bytes)),
        (resource.RLIMIT_CPU, (manifest.limits.cpu_seconds, manifest.limits.cpu_seconds)),
        (resource.RLIMIT_NOFILE, (manifest.limits.max_fds, manifest.limits.max_fds)),
        (resource.RLIMIT_NPROC, (manifest.limits.max_processes, manifest.limits.max_processes)),
    )


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def apply_sandbox(manifest: BotManifest) -> None:
    """Apply sandbox layers to the current process.

    Intended to be called from `subprocess.Popen(preexec_fn=...)` so it runs
    in the child between fork and exec. Layers that are not enforceable on
    the host are skipped with a SandboxWarning.

    Layers applied here (in order):
      1. setrlimit for AS / CPU / NOFILE / NPROC.
      2. (Linux) network-namespace isolation when `limits.network == "deny"`.
         Currently a hook point: the actual netns entry is done by the
         BotRunnerAdapter using `unshare`/clone flags rather than here, since
         it requires CAP_NET_ADMIN that a preexec_fn can't acquire.
      3. (Future) drop privileges to an unprivileged uid.

    Reasoning for the deferred netns: `os.unshare(CLONE_NEWNET)` would need
    privilege. The runner instead launches the child via `unshare --net`
    when `network == "deny"` on Linux. This function records that the layer
    is the runner's responsibility, not the preexec's.
    """
    inactive: list[str] = []

    for res, lim in build_rlimits(manifest):
        try:
            resource.setrlimit(res, lim)
        except (ValueError, OSError) as e:
            inactive.append(f"setrlimit({res})={e!r}")

    if not _is_linux():
        if manifest.limits.network == "deny":
            inactive.append("netns network-deny (Linux-only)")
        inactive.append("RLIMIT_NPROC is per-uid on macOS, not per-process")

    if inactive:
        warnings.warn(
            "Sandbox running with degraded layers: " + "; ".join(inactive),
            SandboxWarning,
            stacklevel=2,
        )


__all__ = [
    "ENV_WHITELIST",
    "RlimitSpec",
    "SandboxWarning",
    "apply_sandbox",
    "build_env",
    "build_rlimits",
]
