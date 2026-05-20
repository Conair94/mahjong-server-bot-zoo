"""Bundled ruleset configs, versioned by config_hash.

Spec: docs/specs/determinism.md § Ruleset config_hash,
      docs/specs/engine-api.md § Internal submodule layout.

Layout: mcr-2006.json + MANIFEST.json mapping human-readable IDs to current
config_hash values. Per-config JSON files are immutable once shipped.
"""
