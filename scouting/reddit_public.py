"""Lightweight public Reddit HTML fetching and parsing."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)


class RedditPublicFetcher:
    """Fetch public Reddit pages as an optional enrichment layer."""

    def __init__(self, user_agent: str = "market-research-tool/0.1", timeout: int = 30) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def fetch_page(self, url: str) -> str:
        LOGGER.info("Fetching public Reddit page: %s", url)
        headers = {"User-Agent": self.user_agent, "Accept": "text/html"}
        try:
            from curl_cffi import requests

            response = requests.get(
                url,
                headers=headers,
                timeout=self.timeout,
                impersonate="chrome",
            )
            response.raise_for_status()
            return response.text
        except ModuleNotFoundError:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return response.read().decode("utf-8", errors="replace")

    def extract_comments(self, url: str, subreddit: str, topic: str, query: str) -> list[dict[str, Any]]:
        """Extract visible comment-like text from a public Reddit HTML page."""

        html = self.fetch_page(url)
        try:
            from bs4 import BeautifulSoup
        except ModuleNotFoundError:
            LOGGER.warning("beautifulsoup4 is not installed; skipping public-page parsing")
            return []

        soup = BeautifulSoup(html, "lxml")
        comments: list[dict[str, Any]] = []
        selectors = [
            'div[data-testid="comment"]',
            "shreddit-comment",
            "div.Comment",
        ]
        seen: set[str] = set()
        for selector in selectors:
            for node in soup.select(selector):
                text = " ".join(node.get_text(" ", strip=True).split())
                if len(text) < 40:
                    continue
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
                if digest in seen:
                    continue
                seen.add(digest)
                comments.append(
                    {
                        "id": digest,
                        "source": "reddit",
                        "subreddit": subreddit,
                        "topic": topic,
                        "query": query,
                        "item_type": "comment",
                        "title": "",
                        "text": text,
                        "url": url,
                        "author": None,
                        "published_at": None,
                        "score": 0,
                        "num_comments": 0,
                        "fetched_at": datetime.now(UTC).isoformat(),
                        "raw_html_or_json_path": None,
                    }
                )
        return comments

