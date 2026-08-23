"""Static checks that catch what the other suites cannot.

An automated step that no test happens to execute can sit in the codebase with a
NameError in it and every test will still pass — which is exactly how a missing
import shipped and only failed when a user pressed the button. pyflakes reads
every line whether it runs or not, so this closes that gap.

    python tests/lint.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Findings that mean something is genuinely broken or a real hazard.
# Unused imports are noise and are deliberately not fatal.
FATAL = (
    "undefined name",
    "local variable",              # referenced before assignment, or dead
    "redefinition of unused",      # a duplicated def silently replacing another
    "f-string is missing placeholders",
    "syntax error",
    "invalid syntax",
    "'return' outside function",
    "two starred expressions",
    "assertion is always true",    # assert (a, b) - always truthy, never checks
)

TARGETS = ["digital_assets_studio", "tests"]


def main() -> int:
    try:
        from pyflakes.api import checkPath
        from pyflakes.reporter import Reporter
    except ImportError:
        print("pyflakes is not installed - install it with:  pip install pyflakes")
        print("(skipping; CI installs it, so this will still be enforced there)")
        return 0

    import io

    out, err = io.StringIO(), io.StringIO()
    reporter = Reporter(out, err)
    files = sorted(
        f for target in TARGETS
        for f in (ROOT / target).rglob("*.py")
        if "__pycache__" not in f.parts
    )
    for f in files:
        checkPath(str(f), reporter)

    findings = [line for line in (out.getvalue() + err.getvalue()).splitlines() if line.strip()]
    fatal = [f for f in findings
             if any(marker in f.lower() for marker in FATAL)]
    other = [f for f in findings if f not in fatal]

    print(f"checked {len(files)} files")
    if other:
        print(f"\n{len(other)} tidiness note(s), not failures:")
        for line in other[:5]:
            print("   ", line.replace(str(ROOT) + "/", ""))
        if len(other) > 5:
            print(f"    … and {len(other) - 5} more")

    if fatal:
        print(f"\n{len(fatal)} FAILURE(S):")
        for line in fatal:
            print("  -", line.replace(str(ROOT) + "/", ""))
        return 1

    print("\nno undefined names, no shadowed definitions, no dead locals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
