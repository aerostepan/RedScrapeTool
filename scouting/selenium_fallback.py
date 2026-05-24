"""SeleniumBase fallback for public Reddit pages."""

from __future__ import annotations

import hashlib
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

LOGGER = logging.getLogger(__name__)


class BrowserBlockedError(RuntimeError):
    """Raised when Reddit shows a CAPTCHA, login wall, or block page."""


class SeleniumFallback:
    """Render pages only when explicitly enabled by config."""

    def __init__(
        self,
        enabled: bool = False,
        timeout: int = 20,
        headless: bool = False,
        min_delay_seconds: float = 30,
        max_delay_seconds: float = 90,
        scrolls_per_search: int = 3,
        open_post_pages: bool = True,
        max_comments_per_post: int = 5,
        sample_all_subreddits_first: bool = True,
    ) -> None:
        self.enabled = enabled
        self.timeout = timeout
        self.headless = headless
        self.min_delay_seconds = min_delay_seconds
        self.max_delay_seconds = max(max_delay_seconds, min_delay_seconds)
        self.scrolls_per_search = scrolls_per_search
        self.open_post_pages = open_post_pages
        self.max_comments_per_post = max_comments_per_post
        self.sample_all_subreddits_first = sample_all_subreddits_first

    def render_page(self, url: str) -> str:
        if not self.enabled:
            raise RuntimeError("Selenium fallback is disabled")

        try:
            from seleniumbase import SB
        except ModuleNotFoundError as exc:
            raise RuntimeError("seleniumbase is not installed") from exc

        LOGGER.info("Rendering page with SeleniumBase fallback: %s", url)
        with SB(uc=False, headless=self.headless) as browser:
            browser.open(url)
            browser.sleep(2)
            self._raise_if_blocked(browser)
            return browser.get_page_source()

    def visible_text(self, url: str) -> str:
        if not self.enabled:
            raise RuntimeError("Selenium fallback is disabled")

        try:
            from seleniumbase import SB
        except ModuleNotFoundError as exc:
            raise RuntimeError("seleniumbase is not installed") from exc

        with SB(uc=False, headless=self.headless) as browser:
            browser.open(url)
            browser.sleep(2)
            self._raise_if_blocked(browser)
            return browser.get_text("body")

    def fetch_queries(
        self,
        queries: list[Any],
        topic: str,
        total_limit: int,
        limit_per_query: int,
        days_back: int,
        on_items: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Collect public visible Reddit search results through SeleniumBase."""

        if not self.enabled:
            raise RuntimeError("Selenium fallback is disabled")

        try:
            from seleniumbase import SB
        except ModuleNotFoundError as exc:
            raise RuntimeError("seleniumbase is not installed") from exc

        collected: list[dict[str, Any]] = []
        cutoff = datetime.now(UTC) - timedelta(days=days_back)
        subreddit_keys = list(dict.fromkeys(query.subreddit.lower() for query in queries))
        attempted_subreddits: set[str] = set()
        first_pass_budget = max(1, total_limit // max(len(subreddit_keys), 1))
        if self.sample_all_subreddits_first and total_limit < len(subreddit_keys):
            LOGGER.warning(
                "Item limit %s is smaller than subreddit count %s; raise --limit to sample every subreddit",
                total_limit,
                len(subreddit_keys),
            )

        with SB(uc=False, headless=self.headless) as browser:
            for query in queries:
                remaining = total_limit - len(collected)
                if remaining <= 0:
                    break
                subreddit_key = query.subreddit.lower()
                reserving_coverage = (
                    self.sample_all_subreddits_first
                    and len(attempted_subreddits) < len(subreddit_keys)
                )
                query_limit = min(limit_per_query, remaining)
                if reserving_coverage:
                    query_limit = min(query_limit, first_pass_budget)

                search_url = self._search_url(query.subreddit, query.query)
                LOGGER.info("Opening Reddit public browser search: %s", search_url)
                items: list[dict[str, Any]] = []
                stop_after_batch = False
                try:
                    browser.open(search_url)
                    self._polite_sleep(browser)
                    self._raise_if_blocked(browser)
                    self._scroll_results(browser)
                    self._raise_if_blocked(browser)

                    items = self._extract_search_items(
                        browser=browser,
                        subreddit=query.subreddit,
                        topic=topic,
                        query=query.query,
                        limit=query_limit,
                        cutoff=cutoff,
                    )

                    if self.open_post_pages and items:
                        found_posts = items
                        enriched: list[dict[str, Any]] = []
                        for index, item in enumerate(found_posts):
                            if len(enriched) >= query_limit:
                                break
                            try:
                                enriched.append(self._enrich_post_item(browser, item))
                                remaining_comments = min(
                                    query_limit - len(enriched),
                                    total_limit - len(collected) - len(enriched),
                                )
                                if remaining_comments <= 0:
                                    break
                                comments = self._extract_visible_comments(browser, item, remaining_comments)
                                enriched.extend(comments)
                            except BrowserBlockedError as exc:
                                LOGGER.warning(
                                    "Stopping enrichment at r/%s after preserving found posts: %s",
                                    query.subreddit,
                                    exc,
                                )
                                items = enriched + found_posts[index:]
                                stop_after_batch = True
                                break
                        if not stop_after_batch:
                            items = enriched
                except BrowserBlockedError as exc:
                    LOGGER.warning(
                        "Stopping browser collection at r/%s after preserving %s collected items: %s",
                        query.subreddit,
                        len(collected),
                        exc,
                    )
                    break

                attempted_subreddits.add(subreddit_key)
                if items:
                    items = items[: min(query_limit, remaining)]
                    collected.extend(items)
                    if on_items is not None:
                        on_items(items)
                if stop_after_batch:
                    break

        return collected

    def _search_url(self, subreddit: str, query: str) -> str:
        return (
            f"https://www.reddit.com/r/{subreddit}/search/"
            f"?q={quote_plus(query)}&restrict_sr=1&sort=new"
        )

    def _polite_sleep(self, browser: Any) -> None:
        delay = random.uniform(self.min_delay_seconds, self.max_delay_seconds)
        browser.sleep(delay)

    def _scroll_results(self, browser: Any) -> None:
        for _ in range(max(0, self.scrolls_per_search)):
            browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            self._polite_sleep(browser)

    def _raise_if_blocked(self, browser: Any) -> None:
        try:
            body = browser.get_text("body").lower()
        except Exception:  # noqa: BLE001
            body = ""
        block_markers = {
            "i'm not a robot",
            "whoa there",
            "too many requests",
            "login to continue",
            "request has been blocked",
            "you've been blocked by network security",
            "you have been blocked by network security",
            "verify you are human",
            "recaptcha",
        }
        matching_marker = next((marker for marker in block_markers if marker in body), None)
        if matching_marker:
            raise BrowserBlockedError(
                f"Reddit showed a challenge or block page (matched: {matching_marker!r})"
            )

    def _extract_search_items(
        self,
        browser: Any,
        subreddit: str,
        topic: str,
        query: str,
        limit: int,
        cutoff: datetime,
    ) -> list[dict[str, Any]]:
        rows = browser.execute_script(SEARCH_EXTRACTION_SCRIPT) or []
        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = normalize_reddit_url(str(row.get("url") or ""))
            if not url or url in seen_urls:
                continue
            published_at = str(row.get("published_at") or "") or None
            published = parse_datetime(published_at)
            if published and published < cutoff:
                continue
            text = " ".join(str(row.get("text") or "").split())
            title = " ".join(str(row.get("title") or "").split()) or text[:140]
            if not title and not text:
                continue
            seen_urls.add(url)
            items.append(
                {
                    "id": stable_hash(url),
                    "source": "reddit",
                    "subreddit": subreddit,
                    "topic": topic,
                    "query": query,
                    "item_type": "post",
                    "title": title,
                    "text": text,
                    "url": url,
                    "author": clean_author(row.get("author")),
                    "published_at": published_at,
                    "score": 0,
                    "num_comments": 0,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "raw_html_or_json_path": None,
                }
            )
            if len(items) >= limit:
                break
        LOGGER.info("Browser search extracted %s items for r/%s %r", len(items), subreddit, query)
        return items

    def _enrich_post_item(self, browser: Any, item: dict[str, Any]) -> dict[str, Any]:
        url = str(item.get("url") or "")
        if not url:
            return item
        LOGGER.info("Opening Reddit public post page: %s", url)
        browser.open(url)
        self._polite_sleep(browser)
        self._raise_if_blocked(browser)
        rows = browser.execute_script(POST_EXTRACTION_SCRIPT) or {}
        if not isinstance(rows, dict):
            return item
        title = " ".join(str(rows.get("title") or "").split())
        text = " ".join(str(rows.get("text") or "").split())
        enriched = dict(item)
        if title:
            enriched["title"] = title
        if text and len(text) > len(str(enriched.get("text") or "")):
            enriched["text"] = text
        published_at = str(rows.get("published_at") or "") or None
        if published_at:
            enriched["published_at"] = published_at
        return enriched

    def _extract_visible_comments(
        self,
        browser: Any,
        post_item: dict[str, Any],
        remaining_limit: int,
    ) -> list[dict[str, Any]]:
        if remaining_limit <= 0:
            return []
        rows = browser.execute_script(COMMENT_EXTRACTION_SCRIPT) or []
        comments: list[dict[str, Any]] = []
        post_url = str(post_item.get("url") or "")
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = " ".join(str(row.get("text") or "").split())
            if len(text) < 40:
                continue
            comment_url = normalize_reddit_url(str(row.get("url") or "")) or post_url
            comments.append(
                {
                    "id": stable_hash(f"{comment_url}:{text[:120]}"),
                    "source": "reddit",
                    "subreddit": post_item.get("subreddit"),
                    "topic": post_item.get("topic"),
                    "query": post_item.get("query"),
                    "item_type": "comment",
                    "title": post_item.get("title"),
                    "text": text,
                    "url": comment_url,
                    "author": clean_author(row.get("author")),
                    "published_at": str(row.get("published_at") or "") or None,
                    "score": 0,
                    "num_comments": 0,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "raw_html_or_json_path": None,
                }
            )
            if len(comments) >= min(self.max_comments_per_post, remaining_limit):
                break
        LOGGER.info("Browser post page extracted %s visible comments from %s", len(comments), post_url)
        return comments


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:24]


def normalize_reddit_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("/"):
        url = f"https://www.reddit.com{url}"
    return url.split("?")[0].split("#")[0].rstrip("/")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def clean_author(value: Any) -> str | None:
    author = str(value or "").strip()
    if not author:
        return None
    return author.removeprefix("u/").removeprefix("/u/")


SEARCH_EXTRACTION_SCRIPT = r"""
const anchors = Array.from(document.querySelectorAll('a[href*="/comments/"]'));
const rows = [];
const seen = new Set();
for (const anchor of anchors) {
  const href = anchor.href || anchor.getAttribute("href") || "";
  if (!href || seen.has(href) || !href.includes("/comments/")) continue;
  seen.add(href);
  const card = anchor.closest("shreddit-post, article, div[data-testid='post-container'], div") || anchor;
  const timeEl = card.querySelector("time");
  const authorEl = card.querySelector("a[href^='/user/'], a[href*='/user/'], a[href^='/u/'], a[href*='/u/']");
  const rawTitle = anchor.innerText || anchor.getAttribute("aria-label") || "";
  const rawText = card.innerText || rawTitle;
  rows.push({
    url: href,
    title: rawTitle,
    text: rawText,
    author: authorEl ? authorEl.innerText : null,
    published_at: timeEl ? (timeEl.getAttribute("datetime") || timeEl.dateTime || null) : null
  });
}
return rows;
"""


POST_EXTRACTION_SCRIPT = r"""
const post = document.querySelector("shreddit-post, article, div[data-testid='post-container']") || document.body;
const h1 = document.querySelector("h1");
const timeEl = post.querySelector("time");
return {
  title: h1 ? h1.innerText : "",
  text: post ? post.innerText : "",
  published_at: timeEl ? (timeEl.getAttribute("datetime") || timeEl.dateTime || null) : null
};
"""


COMMENT_EXTRACTION_SCRIPT = r"""
const nodes = Array.from(document.querySelectorAll("shreddit-comment, div[data-testid='comment'], div[id^='t1_']"));
const rows = [];
const seen = new Set();
for (const node of nodes) {
  const text = node.innerText || "";
  const key = text.slice(0, 160);
  if (!text || seen.has(key)) continue;
  seen.add(key);
  const authorEl = node.querySelector("a[href^='/user/'], a[href*='/user/'], a[href^='/u/'], a[href*='/u/']");
  const timeEl = node.querySelector("time");
  const linkEl = node.querySelector("a[href*='/comments/']");
  rows.push({
    text,
    author: authorEl ? authorEl.innerText : null,
    published_at: timeEl ? (timeEl.getAttribute("datetime") || timeEl.dateTime || null) : null,
    url: linkEl ? linkEl.href : location.href
  });
}
return rows;
"""
