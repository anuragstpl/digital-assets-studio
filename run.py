#!/usr/bin/env python3
"""Start Digital Assets Studio.

    python run.py              launch the app
    python run.py --selftest   check the install without opening a window
    python run.py --version    print the version
"""
from __future__ import annotations

import sys


def selftest() -> int:
    """Prove a packaged build can find everything it needs.

    Frozen builds fail in one specific way: the bundled fonts do not make it into
    the bundle, and the first cover render dies. This checks that before a user
    ever sees it.
    """
    from digital_assets_studio.config import APP_NAME, APP_VERSION, ASSETS_DIR, WORKSPACE

    print(f"{APP_NAME} {APP_VERSION}")
    print(f"  python     {sys.version.split()[0]}")
    print(f"  frozen     {getattr(sys, 'frozen', False)}")
    print(f"  assets     {ASSETS_DIR}")
    print(f"  workspace  {WORKSPACE}")

    problems: list[str] = []
    fonts = ASSETS_DIR / "fonts"
    required = ["Poppins-Regular.ttf", "Poppins-Medium.ttf", "Poppins-Bold.ttf",
                "Lora-Regular.ttf"]
    missing = [f for f in required if not (fonts / f).exists()]
    if missing:
        problems.append(f"bundled fonts missing: {', '.join(missing)}")
    else:
        print(f"  fonts      {len(required)} found")

    try:
        from digital_assets_studio.pipelines import PIPELINES
        steps = sum(len(p.steps) for p in PIPELINES)
        print(f"  pipelines  {len(PIPELINES)} loaded, {steps} steps")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"pipelines failed to load: {exc}")

    try:
        from PIL import ImageFont
        ImageFont.truetype(str(fonts / "Poppins-Bold.ttf"), 32)
        print("  rendering  fonts load")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"fonts present but unusable: {exc}")

    try:
        import flet  # noqa: F401
        print("  ui         flet importable")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"flet not importable: {exc}")

    if problems:
        print("\nFAILED")
        for p in problems:
            print("  -", p)
        return 1
    print("\nOK - this build is complete")
    return 0


def main() -> int:
    args = set(sys.argv[1:])
    if {"--version", "-V"} & args:
        from digital_assets_studio.config import APP_NAME, APP_VERSION
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    if "--selftest" in args:
        return selftest()
    from digital_assets_studio.app import run
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
