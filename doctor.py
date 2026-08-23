#!/usr/bin/env python3
"""Check that this machine can run everything: python doctor.py"""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REQUIRED = [("flet", "the desktop UI"), ("httpx", "talking to model APIs"),
            ("PIL", "covers, thumbnails, screenshots"), ("reportlab", "print-ready PDFs"),
            ("markdown", "EPUB conversion"), ("jwt", "Google Play and App Store auth")]
OPTIONAL = [("keyring", "storing keys in the OS keychain (falls back to an encoded file)"),
            ("edge_tts", "free voiceovers (pip install edge-tts)"),
            ("playwright", "assisted KDP publishing (pip install playwright)")]
TOOLS = [("ffmpeg", "rendering video (winget install Gyan.FFmpeg / brew install ffmpeg)"),
         ("ffprobe", "measuring audio length — ships with ffmpeg")]


def main() -> int:
    print(f"Python {sys.version.split()[0]} on {sys.platform}\n")
    bad = 0
    print("Required")
    for mod, why in REQUIRED:
        try:
            importlib.import_module(mod)
            print(f"  ok       {mod:12} {why}")
        except Exception:  # noqa: BLE001
            bad += 1
            print(f"  MISSING  {mod:12} {why}")
    print("\nOptional")
    for mod, why in OPTIONAL:
        try:
            importlib.import_module(mod)
            print(f"  ok       {mod:12} {why}")
        except Exception:  # noqa: BLE001
            print(f"  absent   {mod:12} {why}")
    print("\nCommand-line tools")
    for exe, why in TOOLS:
        print(f"  {'ok      ' if shutil.which(exe) else 'absent  '} {exe:12} {why}")

    try:
        from digital_assets_studio.config import WORKSPACE
        from digital_assets_studio.core import keyvault
        from digital_assets_studio.core.publishing import mpt, stockvideo
        print(f"\nWorkspace: {WORKSPACE}")
        print(f"Key store: {keyvault.backend_name()}")
        print("\nVideo engines")
        print(f"  {'ok      ' if shutil.which('ffmpeg') else 'MISSING '} designed slides "
              f"(needs ffmpeg)")
        stock = stockvideo.has_key("pexels") or stockvideo.has_key("pixabay")
        print(f"  {'ok      ' if stock else 'absent  '} stock footage "
              f"(needs a free Pexels or Pixabay key)")
        print(f"  {'ok      ' if mpt.configured() else 'absent  '} MoneyPrinterTurbo "
              f"({mpt.base_url()})")
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not load the app package: {exc}")
        bad += 1

    print("\n" + ("Everything required is present." if not bad
                  else f"{bad} required item(s) missing — run: pip install -r requirements.txt"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
