"""Deduplication by Reddit identifiers, URLs, and normalized text hashes."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse, urlunparse

from storage.json_store import read_json, write_json
from storage.redis_queue import RedisQueue

LOGGER = logging.getLogger(__name__)


class DedupeStore:
    """Keep dedupe state in Redis when available and in a JSON file otherwise."""

    def __init__(
        self,
        redis_queue: RedisQueue | None = None,
        local_path: str | Path = "data/processed/dedupe_seen.json",
        set_name: str = "dedupe",
    ) -> None:
        self.redis_queue = redis_queue
        self.local_path = Path(local_path)
        self.set_name = set_name
        self.local_seen: set[str] = set(read_json(self.local_path, default=[]))

    def keys_for_item(self, item: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        item_id = str(item.get("id") or "").strip()
        if item_id:
            keys.append(f"id:{item_id}")

        url = canonical_url(str(item.get("url") or ""))
        # Visible comments may only expose their parent post URL; their stable
        # ID and text hash distinguish them without collapsing the thread.
        if url and item.get("item_type") != "comment":
            keys.append(f"url:{url}")

        normalized_blob = normalize_blob(
            f"{item.get('title') or ''}\n{item.get('text') or ''}"
        )
        if normalized_blob:
            keys.append(f"text:{hash_text(normalized_blob)}")
        return keys

    def is_seen(self, item: dict[str, Any]) -> bool:
        return any(self._contains(key) for key in self.keys_for_item(item))

    def mark(self, item: dict[str, Any]) -> None:
        for key in self.keys_for_item(item):
            self._add(key)

    def filter_new(self, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        new_items: list[dict[str, Any]] = []
        duplicates = 0
        for item in items:
            if self.is_seen(item):
                duplicates += 1
                continue
            self.mark(item)
            new_items.append(item)
        self.flush()
        LOGGER.info("Deduplication kept %s new items and removed %s duplicates", len(new_items), duplicates)
        return new_items, duplicates

    def flush(self) -> None:
        write_json(self.local_path, sorted(self.local_seen))

    def _contains(self, key: str) -> bool:
        if self.redis_queue is not None and self.redis_queue.client is not None:
            return self.redis_queue.set_contains(self.set_name, key)
        return key in self.local_seen

    def _add(self, key: str) -> None:
        if self.redis_queue is not None and self.redis_queue.client is not None:
            self.redis_queue.set_add(self.set_name, key)
        self.local_seen.add(key)


def canonical_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    url, _fragment = urldefrag(url)
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+$", "", parsed.path)
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def normalize_blob(value: str) -> str:
    value = value.lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:24]
