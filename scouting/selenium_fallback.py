"""SeleniumBase fallback for public Reddit pages."""

from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)


class SeleniumFallback:
    """Render pages only when explicitly enabled by config."""

    def __init__(self, enabled: bool = False, timeout: int = 20) -> None:
        self.enabled = enabled
        self.timeout = timeout

    def render_page(self, url: str) -> str:
        if not self.enabled:
            raise RuntimeError("Selenium fallback is disabled")

        try:
            from seleniumbase import SB
        except ModuleNotFoundError as exc:
            raise RuntimeError("seleniumbase is not installed") from exc

        LOGGER.info("Rendering page with SeleniumBase fallback: %s", url)
        with SB(uc=False, headless=True) as browser:
            browser.open(url)
            browser.sleep(2)
            return browser.get_page_source()

    def visible_text(self, url: str) -> str:
        if not self.enabled:
            raise RuntimeError("Selenium fallback is disabled")

        try:
            from seleniumbase import SB
        except ModuleNotFoundError as exc:
            raise RuntimeError("seleniumbase is not installed") from exc

        with SB(uc=False, headless=True) as browser:
            browser.open(url)
            browser.sleep(2)
            return browser.get_text("body")

