"""Check that a built wheel can serve every bundled static asset and ruleset.

Run from the repository root: python scripts/check_distribution.py dist/*.whl
Editable installs hide missing package-data declarations; inspect the artifact
that will actually be installed on another machine.
"""

from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile


def main() -> None:
    roots = ("mahjong/web/static", "mahjong/control/static", "mahjong/engine/rulesets")
    expected = {
        str(path)
        for root in roots
        for path in Path(root).iterdir()
        if path.suffix in {".html", ".js", ".css", ".json"}
    }
    if not expected:
        raise SystemExit("run this check from the repository root")
    if len(sys.argv) < 2:
        raise SystemExit("usage: python scripts/check_distribution.py dist/*.whl")
    for filename in sys.argv[1:]:
        with ZipFile(filename) as wheel:
            missing = expected - set(wheel.namelist())
        if missing:
            raise SystemExit(f"{filename}: missing packaged assets: {sorted(missing)}")
        print(f"{filename}: all {len(expected)} static assets and rulesets included")


if __name__ == "__main__":
    main()
