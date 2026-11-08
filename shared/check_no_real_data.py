"""Fail if real company data or secrets are tracked by git.

CLAUDE.md: never commit or publish real data. .gitignore only protects files
that are not yet tracked, so this check looks at what git actually tracks
(or, in CI, at what the commit contains).

Run from the repo root:  py -m shared.check_no_real_data
Exit code 0 = clean, 1 = a forbidden path is tracked.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Path prefixes (directories end in "/") or exact paths that must never be tracked.
FORBIDDEN = (
    "apps/recon/data/",
    "apps/sales_tax/data/",
    "apps/fifo_inventory/fifo_snapshots/",
    "apps/fifo_inventory/app_settings.json",
    "apps/fifo_inventory/material_aliases.json",
    "shared/fiscal_settings.json",
    ".streamlit/secrets.toml",
)

# Tracked-file names that are never allowed anywhere in the tree.
FORBIDDEN_NAMES = (".env",)
FORBIDDEN_SUFFIXES = (".pem", ".key")


def violations(paths: list[str]) -> list[str]:
    """Return the tracked paths that break the rules, sorted."""
    bad = []
    for p in paths:
        p = p.replace("\\", "/")
        name = p.rsplit("/", 1)[-1]
        if (
            any(p == f or (f.endswith("/") and p.startswith(f)) for f in FORBIDDEN)
            or name in FORBIDDEN_NAMES
            or name.startswith(".env.") and not name.endswith(".example")
            or name.endswith(FORBIDDEN_SUFFIXES)
        ):
            bad.append(p)
    return sorted(bad)


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    return [p for p in out.split("\0") if p]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    bad = violations(tracked_files(root))
    if bad:
        print("Real data or secrets are tracked by git:")
        for p in bad:
            print(f"  {p}")
        print("Remove them with `git rm --cached <path>` and keep them in .gitignore.")
        return 1
    print("OK: no real data or secrets are tracked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
