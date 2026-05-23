"""Wire-protocol error hierarchy.

Spec: docs/specs/wire-protocol.md § Error codes.

- `WireFramingError`: the frame is not a well-formed wire payload at the
  envelope layer — invalid JSON, not a JSON object, or missing/non-string
  `kind`. Maps to WebSocket close code 1003 ("unsupported data") or 1002
  ("protocol error") at the transport layer.
- `WireDecodeError`: the frame parses but its `kind` is not in
  `codec.KNOWN_KINDS`, or required structural fields are missing. Maps to
  `ERROR { code: "unknown_kind" }` plus WS close 1003.
- `WireVersionError`: HELLO carried a `protocol_version` this implementation
  refuses. Maps to `ERROR { code: "protocol_version" }` plus WS close 1002.
"""

from __future__ import annotations


class WireError(Exception):
    """Base class for wire-protocol failures."""


class WireFramingError(WireError):
    """Envelope-level failure: invalid JSON, non-object, missing `kind`."""


class WireDecodeError(WireError):
    """Unknown `kind` or missing required field after envelope validation."""


class WireVersionError(WireError):
    """`protocol_version` mismatch on `HELLO`."""


__all__ = [
    "WireDecodeError",
    "WireError",
    "WireFramingError",
    "WireVersionError",
]
