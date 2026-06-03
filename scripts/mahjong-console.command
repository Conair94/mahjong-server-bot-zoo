#!/usr/bin/env bash
# macOS Finder-double-clickable launcher for the mahjong admin console.
# Double-click in Finder → opens the dashboard in your browser, no terminal.
# (Delegates to ./scripts/mahjong-console so there's one source of truth.)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
exec "$HERE/scripts/mahjong-console" --autostart-server
