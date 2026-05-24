"""A small browser abstraction for public pages.

The first version uses HTTP fetching first and only calls Selenium when the
caller explicitly enables it. This keeps browser automation as fallback
infrastructure instead of the main collection strategy.
"""

from __future__ import annotations

import logging

from scouting.reddit_public import RedditPublicFetcher
from scouting.selenium_fallback import SeleniumFallback

LOGGER = logging.getLogger(__name__)


class CloakBrowser:
    """Fetch a public page through the lightest available approved layer."""

    def __init__(
        self,
        user_agent: str = "market-research-tool/0.1",
        use_selenium_fallback: bool = False,
    ) -> None:
        self.public_fetcher = RedditPublicFetcher(user_agent=user_agent)
        self.selenium = SeleniumFallback(enabled=use_selenium_fallback)

    def get_html(self, url: str) -> str:
        try:
            return self.public_fetcher.fetch_page(url)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Public fetch failed for %s: %s", url, exc)
            if not self.selenium.enabled:
                raise
            return self.selenium.render_page(url)

