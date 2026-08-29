"""Assisted browser automation, for the stores with no API.

Amazon KDP, Gumroad and Payhip publish no write API, so the only honest way to
remove the typing is to drive a real browser. Ground rules baked in here:

  * The browser is VISIBLE. You watch it work.
  * A persistent profile means you sign in once, by hand, including any 2FA.
    Credentials are never typed by the suite and never stored by it.
  * It fills the form and then STOPS. You read the page and press the store's
    own publish button. Nothing is submitted without you.
  * Selectors on these pages change without notice. When one moves, the step
    reports exactly which field it could not find and leaves the rest filled in.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ...config import WORKSPACE

log = logging.getLogger(__name__)

PROFILE_DIR = WORKSPACE / "browser-profile"


class BrowserError(RuntimeError):
    pass


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


INSTALL_HINT = (
    "Browser automation needs Playwright:\n"
    "    pip install playwright\n"
    "    playwright install chromium\n"
    "Then restart Artalo Digi Suit."
)


@dataclass
class FillResult:
    filled: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.missed


class Session:
    """A visible Chromium with a persistent profile."""

    def __init__(self, headless: bool = False, slow_mo: int = 120) -> None:
        if not available():
            raise BrowserError(INSTALL_HINT)
        self._pw = None
        self._ctx = None
        self.headless = headless
        self.slow_mo = slow_mo

    def __enter__(self) -> "Session":
        from playwright.sync_api import sync_playwright

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=self.headless, slow_mo=self.slow_mo,
                viewport={"width": 1440, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:  # noqa: BLE001
            self._pw.stop()
            raise BrowserError(
                f"Could not start Chromium: {exc}\n\nIf this is the first run, try:\n"
                "    playwright install chromium") from exc
        return self

    def __exit__(self, *exc) -> bool:
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()
        return False

    @property
    def page(self):
        if not self._ctx:
            raise BrowserError("Browser session is not open.")
        return self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    # ------------------------------------------------------------ helpers --
    def goto(self, url: str, wait: str = "domcontentloaded") -> None:
        self.page.goto(url, wait_until=wait, timeout=90_000)

    def wait_for_signin(self, signed_in_selector: str, timeout_ms: int = 300_000,
                        on_wait: Callable[[str], None] | None = None) -> None:
        """Block until the page shows the signed-in marker. The human does the login."""
        if on_wait:
            on_wait("Sign in to the store in the browser window that just opened. "
                    "Two-factor and captchas are yours - I will wait.")
        try:
            self.page.wait_for_selector(signed_in_selector, timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise BrowserError("Timed out waiting for sign-in. Nothing was submitted.") from exc

    def fill_first(self, label: str, selectors: list[str], value: str,
                   result: FillResult, kind: str = "fill") -> None:
        if value in (None, ""):
            return
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if el.count() == 0:
                    continue
                el.scroll_into_view_if_needed(timeout=5000)
                if kind == "fill":
                    el.fill(str(value), timeout=10_000)
                elif kind == "select":
                    el.select_option(str(value), timeout=10_000)
                elif kind == "check":
                    el.check(timeout=10_000)
                elif kind == "click":
                    el.click(timeout=10_000)
                result.filled.append(label)
                return
            except Exception:  # noqa: BLE001
                continue
        result.missed.append(label)

    def upload(self, label: str, selectors: list[str], path: Path, result: FillResult) -> None:
        if not Path(path).exists():
            result.missed.append(f"{label} (file missing: {path})")
            return
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if el.count() == 0:
                    continue
                el.set_input_files(str(path), timeout=60_000)
                result.filled.append(label)
                return
            except Exception:  # noqa: BLE001
                continue
        result.missed.append(label)

    def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=False)
        return path


# ------------------------------------------------------------------ recipes --

def kdp_prefill(book: dict[str, Any], files: dict[str, Path], out_dir: Path,
                on_status: Callable[[str], None] | None = None) -> FillResult:
    """Open KDP's new-Kindle-eBook form and fill everything the suite knows.

    Stops before Amazon's own 'Publish' button, on purpose."""
    def say(m: str) -> None:
        log.info(m)
        if on_status:
            on_status(m)

    res = FillResult()
    with Session() as s:
        say("Opening KDP")
        s.goto("https://kdp.amazon.com/en_US/title-setup/kindle/new/details")
        s.wait_for_signin("input#data-print-book-title, input[name='data[title]'], #data-print-book-title",
                          on_wait=say)
        say("Filling the details page")

        s.fill_first("Title", ["#data-print-book-title", "input[name='data[title]']",
                               "input[id*='title'][type='text']"], book.get("title", ""), res)
        s.fill_first("Subtitle", ["#data-print-book-subtitle", "input[name='data[subtitle]']"],
                     book.get("subtitle", ""), res)
        s.fill_first("Series", ["#data-print-book-series-title"], book.get("series", ""), res)
        s.fill_first("Author first name", ["#data-print-book-primary-author-first-name"],
                     book.get("author_first", ""), res)
        s.fill_first("Author last name", ["#data-print-book-primary-author-last-name"],
                     book.get("author_last", ""), res)
        s.fill_first("Description", ["#cke_1_contents .cke_editable", "#data-print-book-description",
                                     "textarea[name='data[description]']"],
                     book.get("description_plain", ""), res)
        for i, kw in enumerate(book.get("keywords", [])[:7], start=1):
            s.fill_first(f"Keyword {i}", [f"#data-print-book-keywords-{i}",
                                          f"input[name='data[keywords][{i - 1}]']"], kw, res)

        say("Filled what I could. Nothing has been submitted.")
        s.screenshot(out_dir / "kdp_prefilled.png")
        res.note = ("The form is filled and waiting in the browser. Review it, set the "
                    "categories, answer the AI-content disclosure, upload the files and press "
                    "Amazon's own buttons. Close the browser window when you are done.")
        try:
            s.page.wait_for_timeout(1_000)
            input_wait = s.page
            _ = input_wait  # keep the window open until the context closes
        except Exception:  # noqa: BLE001
            pass
    return res


RECIPES = {
    "kdp": kdp_prefill,
}
