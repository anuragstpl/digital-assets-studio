"""Entry point."""
from __future__ import annotations

import logging
import sys

import flet as ft

from .config import APP_NAME, ASSETS_DIR, LOG_FILE, ensure_dirs
from .core.jobs import RUNNER
from .ui.studio import Studio


def _setup_logging() -> None:
    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main(page: ft.Page) -> None:
    page.title = APP_NAME
    page.window.width = 1360
    page.window.height = 880
    page.window.min_width = 1040
    page.window.min_height = 700
    page.padding = 0
    page.spacing = 0
    page.fonts = {
        "Poppins": "fonts/Poppins-Regular.ttf",
        "Poppins Medium": "fonts/Poppins-Medium.ttf",
        "Poppins Bold": "fonts/Poppins-Bold.ttf",
    }
    page.scroll = None

    studio = Studio(page)

    def on_close(e) -> None:
        if e.data == "close":
            RUNNER.shutdown()
            page.window.destroy()

    page.window.prevent_close = False
    page.on_window_event = on_close
    studio.mount()


def run() -> None:
    _setup_logging()
    ft.app(target=main, assets_dir=str(ASSETS_DIR))


if __name__ == "__main__":
    run()
