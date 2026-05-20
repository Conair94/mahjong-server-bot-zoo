"""Canonical hash: SHA-256 over sorted-key JSON.

Spec: docs/specs/determinism.md § The canonical hash.

One function (`canonical_hash`), three uses: state_hash, config_hash, record
checksum. Floats are forbidden as hash inputs (state schema bans them; this
contract is what enforces the consequence).
"""
