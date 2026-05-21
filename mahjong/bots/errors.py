"""Bot-side exception taxonomy.

Spec: docs/specs/bot-runner-protocol.md.

Manifest / registration failures are surfaced at registration time per
bot-runner-protocol.md fixture 10: validation is *not* deferred to spawn.
"""

from __future__ import annotations


class BotError(Exception):
    """Base class for all bot-runner failures."""


class BotManifestError(BotError):
    """A manifest failed validation.

    Payload:
        field: dotted path of the offending field (e.g. "limits.memory_mb"),
               or "" for whole-document errors (e.g. bad JSON).
        detail: human-readable explanation.
    """

    def __init__(self, *, field: str, detail: str) -> None:
        super().__init__(f"BotManifestError({field!r}: {detail})")
        self.field = field
        self.detail = detail
