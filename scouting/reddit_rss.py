"""Reddit RSS collection.

RSS is the preferred fetch path. This module intentionally avoids login-only
or private endpoints and only reads public Reddit feeds.
"""

from __future__ import annotations

import calendar
import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from xml.etree import ElementTree

LOGGER = logging.getLogger(__name__)


class RedditAccessError(RuntimeError):
    """Raised when Reddit returns an access-related HTTP status."""

    def __init__(self, url: str, status_code: int, message: str = "") -> None:
        self.url = url
        self.status_code = status_code
        super().__init__(f"Reddit returned HTTP {status_code} for {url}. {message}".strip())


class RedditRSSFetcher:
    """Fetch and normalize public Reddit RSS feed entries."""

    def __init__(
        self,
        user_agent: str = "market-research-tool/0.1",
        timeout: int = 30,
        retries: int = 3,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries

    def build_search_url(self, subreddit: str, query: str) -> str:
        return (
            f"https://www.reddit.com/r/{subreddit}/search.rss?"
            f"q={quote_plus(query)}&restrict_sr=1&sort=new"
        )

    def build_feed_url(self, subreddit: str, feed: str = "new") -> str:
        feed = feed.strip("/")
        if feed == "top":
            return f"https://www.reddit.com/r/{subreddit}/top/.rss?t=month"
        return f"https://www.reddit.com/r/{subreddit}/{feed}/.rss"

    def fetch_search(
        self,
        subreddit: str,
        query: str,
        topic: str,
        limit: int = 50,
        days_back: int = 90,
    ) -> list[dict[str, Any]]:
        url = self.build_search_url(subreddit, query)
        LOGGER.info("Fetching Reddit RSS: %s", url)
        feed_text = self._fetch_text(url)
        entries = self._parse_feed(feed_text)
        cutoff = datetime.now(UTC) - timedelta(days=days_back)

        items: list[dict[str, Any]] = []
        for entry in entries:
            item = self._entry_to_item(entry, subreddit=subreddit, topic=topic, query=query)
            published = parse_datetime(item.get("published_at"))
            if published and published < cutoff:
                continue
            items.append(item)
            if len(items) >= limit:
                break
        return items

    def fetch_feed(
        self,
        subreddit: str,
        topic: str,
        feed: str = "new",
        limit: int = 50,
        days_back: int = 90,
    ) -> list[dict[str, Any]]:
        url = self.build_feed_url(subreddit, feed)
        LOGGER.info("Fetching Reddit RSS feed: %s", url)
        feed_text = self._fetch_text(url)
        entries = self._parse_feed(feed_text)
        cutoff = datetime.now(UTC) - timedelta(days=days_back)

        items: list[dict[str, Any]] = []
        for entry in entries:
            item = self._entry_to_item(entry, subreddit=subreddit, topic=topic, query=f"{feed} feed")
            published = parse_datetime(item.get("published_at"))
            if published and published < cutoff:
                continue
            items.append(item)
            if len(items) >= limit:
                break
        return items

    def _fetch_text(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                return self._fetch_with_curl_cffi(url)
            except RedditAccessError as exc:
                if exc.status_code == 403:
                    LOGGER.warning("Reddit blocked public RSS fetch for %s: %s", url, exc)
                    raise
                last_error = exc
                LOGGER.warning("RSS access error on attempt %s for %s: %s", attempt, url, exc)
                time.sleep(min(2 ** attempt, 8))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                LOGGER.warning("RSS fetch failed on attempt %s for %s: %s", attempt, url, exc)
                time.sleep(min(2 ** attempt, 8))
        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to fetch {url}")

    def _fetch_with_curl_cffi(self, url: str) -> str:
        headers = {"User-Agent": self.user_agent, "Accept": "application/rss+xml,application/xml"}
        try:
            from curl_cffi import requests

            response = requests.get(
                url,
                headers=headers,
                timeout=self.timeout,
                impersonate="chrome",
            )
            if response.status_code in {403, 429}:
                raise RedditAccessError(url, response.status_code, response.reason)
            response.raise_for_status()
            return response.text
        except ModuleNotFoundError:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return response.read().decode("utf-8", errors="replace")

    def _parse_feed(self, feed_text: str) -> list[dict[str, Any]]:
        try:
            import feedparser

            parsed = feedparser.parse(feed_text)
            if getattr(parsed, "bozo", False):
                LOGGER.debug("Feedparser bozo warning: %s", getattr(parsed, "bozo_exception", ""))
            return [dict(entry) for entry in parsed.entries]
        except ModuleNotFoundError:
            return parse_atom_fallback(feed_text)

    def _entry_to_item(
        self,
        entry: dict[str, Any],
        subreddit: str,
        topic: str,
        query: str,
    ) -> dict[str, Any]:
        title = safe_text(entry.get("title"))
        link = safe_link(entry)
        text = safe_entry_text(entry)
        published_at = safe_published_at(entry)
        reddit_id = safe_text(entry.get("id")) or link or f"{title}:{published_at}"
        stable_id = stable_hash(reddit_id)

        return {
            "id": stable_id,
            "source": "reddit",
            "subreddit": subreddit,
            "topic": topic,
            "query": query,
            "item_type": "post",
            "title": title,
            "text": text,
            "url": link,
            "author": safe_author(entry),
            "published_at": published_at,
            "score": int_or_zero(entry.get("score")),
            "num_comments": int_or_zero(entry.get("num_comments")),
            "fetched_at": datetime.now(UTC).isoformat(),
            "raw_html_or_json_path": None,
        }


def parse_atom_fallback(feed_text: str) -> list[dict[str, Any]]:
    """Parse Reddit's Atom feed without feedparser."""

    root = ElementTree.fromstring(feed_text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries: list[dict[str, Any]] = []

    for node in root.findall("atom:entry", ns):
        link_node = node.find("atom:link", ns)
        author_node = node.find("atom:author/atom:name", ns)
        entry = {
            "id": node.findtext("atom:id", default="", namespaces=ns),
            "title": node.findtext("atom:title", default="", namespaces=ns),
            "link": link_node.attrib.get("href", "") if link_node is not None else "",
            "summary": node.findtext("atom:summary", default="", namespaces=ns),
            "content": [{"value": node.findtext("atom:content", default="", namespaces=ns)}],
            "author": author_node.text if author_node is not None else "",
            "published": node.findtext("atom:published", default="", namespaces=ns),
            "updated": node.findtext("atom:updated", default="", namespaces=ns),
        }
        entries.append(entry)
    return entries


def safe_link(entry: dict[str, Any]) -> str:
    link = entry.get("link")
    if isinstance(link, str):
        return link
    links = entry.get("links")
    if isinstance(links, list):
        for item in links:
            if isinstance(item, dict) and item.get("href"):
                return str(item["href"])
    return ""


def safe_entry_text(entry: dict[str, Any]) -> str:
    content = entry.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("value"):
            return str(first["value"])
    summary = entry.get("summary")
    if isinstance(summary, str):
        return summary
    return ""


def safe_author(entry: dict[str, Any]) -> str | None:
    author = entry.get("author")
    if isinstance(author, str) and author.strip():
        return author.strip()
    author_detail = entry.get("author_detail")
    if isinstance(author_detail, dict) and author_detail.get("name"):
        return str(author_detail["name"])
    return None


def safe_published_at(entry: dict[str, Any]) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            try:
                return datetime.fromtimestamp(calendar.timegm(value), UTC).isoformat()
            except (TypeError, ValueError, OverflowError):
                pass
    for key in ("published", "updated"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            parsed = parse_datetime(value)
            if parsed:
                return parsed.isoformat()
    return None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:24]


def safe_text(value: Any) -> str:
    return str(value or "").strip()


def int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
