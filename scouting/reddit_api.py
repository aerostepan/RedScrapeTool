"""Official Reddit API fallback.

This is a read-only fallback for cases where public Reddit RSS returns 403.
RSS remains the primary collection method.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from scouting.reddit_rss import stable_hash

LOGGER = logging.getLogger(__name__)


class RedditAPIError(RuntimeError):
    """Raised when the official Reddit API fallback cannot fetch data."""


class RedditOAuthFetcher:
    """Read-only Reddit OAuth client using application credentials."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        user_agent: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.client_id = client_id or os.getenv("REDDIT_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("REDDIT_CLIENT_SECRET", "")
        self.user_agent = (
            user_agent
            or os.getenv("REDDIT_USER_AGENT", "")
            or "market-research-tool/0.1 by local-user"
        )
        self.timeout = timeout
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id.strip() and self.client_secret.strip())

    def fetch_search(
        self,
        subreddit: str,
        query: str,
        topic: str,
        limit: int = 50,
        days_back: int = 90,
    ) -> list[dict[str, Any]]:
        if not self.is_configured:
            raise RedditAPIError("REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are not configured")

        payload = self._get_json(
            f"https://oauth.reddit.com/r/{subreddit}/search",
            params={
                "q": query,
                "restrict_sr": "1",
                "sort": "new",
                "limit": str(min(max(limit, 1), 100)),
                "raw_json": "1",
            },
        )
        children = (((payload or {}).get("data") or {}).get("children") or [])
        cutoff = datetime.now(UTC) - timedelta(days=days_back)
        items: list[dict[str, Any]] = []
        for child in children:
            data = child.get("data") if isinstance(child, dict) else {}
            item = self._post_to_item(data or {}, subreddit=subreddit, topic=topic, query=query)
            published = parse_epoch(item.get("published_at"))
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
        if not self.is_configured:
            raise RedditAPIError("REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are not configured")

        path = "top" if feed == "top" else "new"
        params = {"limit": str(min(max(limit, 1), 100)), "raw_json": "1"}
        if path == "top":
            params["t"] = "month"
        payload = self._get_json(f"https://oauth.reddit.com/r/{subreddit}/{path}", params=params)
        children = (((payload or {}).get("data") or {}).get("children") or [])
        cutoff = datetime.now(UTC) - timedelta(days=days_back)
        items: list[dict[str, Any]] = []
        for child in children:
            data = child.get("data") if isinstance(child, dict) else {}
            item = self._post_to_item(data or {}, subreddit=subreddit, topic=topic, query=f"{feed} feed")
            published = parse_epoch(item.get("published_at"))
            if published and published < cutoff:
                continue
            items.append(item)
            if len(items) >= limit:
                break
        return items

    def _get_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        try:
            from curl_cffi import requests
        except ModuleNotFoundError as exc:
            raise RedditAPIError("curl_cffi is required for Reddit OAuth fallback") from exc

        response = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RedditAPIError(f"Reddit token request failed: HTTP {response.status_code}")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RedditAPIError("Reddit token response did not include access_token")
        self._access_token = str(token)
        self._token_expires_at = now + int(payload.get("expires_in") or 3600)
        return self._access_token

    def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            from curl_cffi import requests
        except ModuleNotFoundError as exc:
            raise RedditAPIError("curl_cffi is required for Reddit OAuth fallback") from exc

        token = self._get_token()
        response = requests.get(
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RedditAPIError(f"Reddit API request failed: HTTP {response.status_code} for {url}")
        return response.json()

    def _post_to_item(
        self,
        data: dict[str, Any],
        subreddit: str,
        topic: str,
        query: str,
    ) -> dict[str, Any]:
        reddit_name = str(data.get("name") or data.get("id") or "")
        permalink = str(data.get("permalink") or "")
        url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else str(data.get("url") or "")
        text = str(data.get("selftext") or data.get("body") or "")
        created = data.get("created_utc")
        published_at = (
            datetime.fromtimestamp(float(created), UTC).isoformat()
            if created is not None
            else None
        )
        return {
            "id": reddit_name or stable_hash(url or f"{data.get('title', '')}:{created}"),
            "source": "reddit",
            "subreddit": str(data.get("subreddit") or subreddit),
            "topic": topic,
            "query": query,
            "item_type": "post",
            "title": str(data.get("title") or ""),
            "text": text,
            "url": url,
            "author": data.get("author"),
            "published_at": published_at,
            "score": int_or_zero(data.get("score")),
            "num_comments": int_or_zero(data.get("num_comments")),
            "fetched_at": datetime.now(UTC).isoformat(),
            "raw_html_or_json_path": None,
        }


def parse_epoch(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

