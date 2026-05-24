"""Optional Redis-backed queue and set helpers."""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any

LOGGER = logging.getLogger(__name__)


class RedisQueue:
    """Small Redis wrapper with in-memory fallback."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        prefix: str = "reddit_market_research",
        enabled: bool = True,
    ) -> None:
        self.prefix = prefix
        self.client = None
        self.memory_sets: dict[str, set[str]] = {}
        self.memory_queues: dict[str, deque[str]] = {}

        if not enabled:
            return

        try:
            import redis

            client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
            client.ping()
            self.client = client
            LOGGER.info("Connected to Redis at %s:%s/%s", host, port, db)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Redis unavailable; using in-memory queue/set fallback: %s", exc)

    def _key(self, name: str) -> str:
        return f"{self.prefix}:{name}"

    def set_contains(self, name: str, value: str) -> bool:
        if self.client is not None:
            return bool(self.client.sismember(self._key(name), value))
        return value in self.memory_sets.setdefault(name, set())

    def set_add(self, name: str, value: str) -> None:
        if self.client is not None:
            self.client.sadd(self._key(name), value)
            return
        self.memory_sets.setdefault(name, set()).add(value)

    def push(self, name: str, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        if self.client is not None:
            self.client.rpush(self._key(name), encoded)
            return
        self.memory_queues.setdefault(name, deque()).append(encoded)

    def pop(self, name: str) -> dict[str, Any] | None:
        if self.client is not None:
            encoded = self.client.lpop(self._key(name))
        else:
            queue = self.memory_queues.setdefault(name, deque())
            encoded = queue.popleft() if queue else None
        if not encoded:
            return None
        return json.loads(encoded)

