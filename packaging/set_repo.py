#!/usr/bin/env python3
"""Point the repo at your own GitHub account.

If you forked this, or your username is not `aidiginext`, run:

    python packaging/set_repo.py your-username [repo-name]

It rewrites the badges, install commands, issue links and CI references.
"""
from __future__ import annotations

import sys
from pathlib import Path

OLD_OWNER, OLD_REPO = "aidiginext", "digital-assets-studio"
FILES = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "pyproject.toml",
         "packaging/installer.iss", ".github/workflows/release.yml"]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    owner = sys.argv[1].strip().strip("/")
    repo = (sys.argv[2] if len(sys.argv) > 2 else OLD_REPO).strip().strip("/")
    root = Path(__file__).resolve().parent.parent

    changed = 0
    for name in FILES:
        path = root / name
        if not path.exists():
            continue
        text = original = path.read_text(encoding="utf-8")
        text = text.replace(f"{OLD_OWNER}/{OLD_REPO}", f"{owner}/{repo}")
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed += 1
            print(f"  updated {name}")
    print(f"\n{changed} file(s) now point at github.com/{owner}/{repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
