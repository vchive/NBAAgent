"""Small TTL cache used only behind the provider gateway."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class CacheItem:
    value: Any
    expires_at: float


class InMemoryTTLCache:
    def __init__(self, *, max_entries: int = 10_000, clock: Any | None = None) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self.clock = clock
        self._items: dict[str, CacheItem] = {}
        self.read_count = 0
        self.write_count = 0
        self.hit_count = 0

    def _now(self) -> float:
        """Read a monotonic-ish testable clock.

        Production uses ``time.monotonic`` so wall-clock adjustments cannot
        extend a TTL.  Tests may inject a callable or a domain clock exposing
        ``now_utc``/``now``; datetimes are converted to POSIX seconds.
        """

        if self.clock is None:
            return time.monotonic()
        value = (
            self.clock()
            if callable(self.clock)
            else (self.clock.now_utc() if hasattr(self.clock, "now_utc") else self.clock.now())
        )
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("cache clock must return an aware datetime")
            return value.timestamp()
        return float(value)

    def get(self, key: str) -> Any | None:
        self.read_count += 1
        item = self._items.get(key)
        if item is None:
            return None
        if item.expires_at <= self._now():
            self._items.pop(key, None)
            return None
        self.hit_count += 1
        return copy.deepcopy(item.value)

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        self.write_count += 1
        if len(self._items) >= self.max_entries and key not in self._items:
            oldest_key = min(self._items, key=lambda candidate: self._items[candidate].expires_at)
            self._items.pop(oldest_key, None)
        self._items[key] = CacheItem(copy.deepcopy(value), self._now() + max(ttl_seconds, 0))

    def clear(self) -> None:
        self._items.clear()

    def counters(self) -> dict[str, int]:
        return {
            "cache_read_count": self.read_count,
            "cache_write_count": self.write_count,
            "cache_hit_count": self.hit_count,
        }
