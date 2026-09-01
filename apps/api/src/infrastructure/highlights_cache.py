"""Fail-open SQLite cache for validated public highlights projections."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from apps.api.src.api.schemas import (
    HighlightDetailResponse,
    HighlightGame,
    HighlightsRangeResponse,
    HighlightsResponse,
)

# Version the serialized projection contract, not only the SQLite table shape.
# v4 preserves origin on every game row and derives the envelope from actual
# returned rows; accepting v3 could retain an early aggregate-only projection.
CACHE_SCHEMA_VERSION = 4
_MODEL_BY_KIND: dict[str, type[BaseModel]] = {
    "date": HighlightsResponse,
    "range": HighlightsRangeResponse,
    "recent": HighlightsRangeResponse,
    "detail": HighlightDetailResponse,
}
_T = TypeVar("_T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class PersistentCacheHit:
    value: Any
    stale: bool
    stored_at_utc: datetime
    fresh_until_utc: datetime


def stable_cache_key(kind: str, *parts: object) -> str:
    """Build a bounded key only from already-normalized route parameters."""

    if kind not in _MODEL_BY_KIND:
        raise ValueError("invalid cache key kind")
    values = [kind, f"v{CACHE_SCHEMA_VERSION}"]
    for part in parts:
        text = str(part)
        if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ValueError("cache key contains invalid text")
        values.append(text)
    key = ":".join(values)
    if len(key) > 512:
        raise ValueError("cache key is too long")
    return key


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("cache timestamps must be timezone-aware")
    return current.astimezone(UTC)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored cache timestamp is naive")
    return parsed.astimezone(UTC)


def _canonical_payload(value: BaseModel) -> tuple[str, bytes, str]:
    payload = value.model_dump(mode="json", by_alias=True)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encoded = text.encode("utf-8")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    return text, encoded, fingerprint


def _game_score(game: HighlightGame) -> int:
    score = 10
    score += int(bool(game.home_name)) + int(bool(game.away_name))
    score += int(game.home_score is not None) * 4
    score += int(game.away_score is not None) * 4
    score += int(game.status == "final") * 4
    score += int(game.series_game_number is not None)
    score += int(game.venue_name is not None) * 2
    score += int(game.venue_city is not None)
    return score


def completeness_score(value: BaseModel) -> int:
    """Return a deterministic score used only to prevent data downgrades."""

    if isinstance(value, HighlightDetailResponse):
        leader_fields = sum(
            1
            for leader in value.leaders
            for item in (leader.player_name, leader.points, leader.rebounds, leader.assists)
            if item is not None
        )
        play_fields = sum(
            1
            for play in value.plays
            for item in (
                play.period,
                play.clock,
                play.team,
                play.player_name,
                play.action,
                play.detail,
                play.home_score,
                play.away_score,
            )
            if item is not None
        )
        return _game_score(value.game) * 10_000 + leader_fields * 100 + play_fields
    if isinstance(value, (HighlightsRangeResponse, HighlightsResponse)):
        return len(value.games) * 10_000 + sum(_game_score(game) for game in value.games)
    return 0


def _game_status(value: BaseModel) -> str | None:
    if isinstance(value, HighlightDetailResponse):
        return value.game.status
    if isinstance(value, (HighlightsRangeResponse, HighlightsResponse)):
        if not value.games:
            return "empty"
        statuses = {game.status for game in value.games}
        return statuses.pop() if len(statuses) == 1 else "mixed"
    return None


def _games(value: BaseModel) -> dict[str, HighlightGame]:
    if isinstance(value, HighlightDetailResponse):
        return {value.game.game_id: value.game}
    if isinstance(value, (HighlightsRangeResponse, HighlightsResponse)):
        return {game.game_id: game for game in value.games}
    return {}


def _has_final_score_conflict(old: BaseModel, new: BaseModel) -> bool:
    old_games = _games(old)
    for game_id, new_game in _games(new).items():
        old_game = old_games.get(game_id)
        if old_game is None or old_game.status != "final" or new_game.status != "final":
            continue
        old_score = (old_game.home_score, old_game.away_score)
        new_score = (new_game.home_score, new_game.away_score)
        if None not in old_score and None not in new_score and old_score != new_score:
            return True
    return False


class SQLiteHighlightsCache:
    """Small persistent cache with typed reads and fail-open storage errors."""

    def __init__(
        self,
        database: str | Path,
        *,
        max_entries: int = 5_000,
        max_payload_bytes: int = 2_097_152,
        lease_seconds: int = 30,
        busy_timeout_ms: int = 1_500,
    ) -> None:
        self.database = str(database)
        self.max_entries = max(1, int(max_entries))
        self.max_payload_bytes = max(1, int(max_payload_bytes))
        self.lease_seconds = max(1, int(lease_seconds))
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self.available = False
        self._counters = {
            "persistent_cache_read_count": 0,
            "persistent_cache_hit_count": 0,
            "persistent_cache_stale_hit_count": 0,
            "persistent_cache_write_count": 0,
            "persistent_cache_rejected_write_count": 0,
            "persistent_cache_error_count": 0,
            "persistent_cache_refresh_coalesced_count": 0,
        }
        try:
            connection = sqlite3.connect(
                self.database,
                timeout=self.busy_timeout_ms / 1000,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS highlights_cache (
                    cache_key TEXT PRIMARY KEY,
                    response_kind TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    game_status TEXT,
                    completeness_score INTEGER NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    source_as_of TEXT,
                    stored_at_utc TEXT NOT NULL,
                    fresh_until_utc TEXT NOT NULL,
                    last_accessed_utc TEXT NOT NULL,
                    payload_bytes INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_highlights_cache_accessed
                    ON highlights_cache(last_accessed_utc);
                CREATE TABLE IF NOT EXISTS highlights_refresh_lease (
                    cache_key TEXT PRIMARY KEY,
                    owner_token TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL
                );
                """
            )
            connection.commit()
            self._connection = connection
            self.available = True
        except (OSError, sqlite3.Error, ValueError):
            self._counters["persistent_cache_error_count"] += 1
            self._connection = None
            self.available = False

    @property
    def status(self) -> str:
        return "ok" if self.available else "degraded"

    def _delete_invalid(self, key: str) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            connection.execute("DELETE FROM highlights_cache WHERE cache_key = ?", (key,))
            connection.commit()
        except sqlite3.Error:
            pass

    def get(
        self,
        key: str,
        model: type[_T],
        *,
        now: datetime | None = None,
        allow_stale: bool = False,
        count_metrics: bool = True,
    ) -> PersistentCacheHit | None:
        if count_metrics:
            self._counters["persistent_cache_read_count"] += 1
        connection = self._connection
        if connection is None:
            return None
        current = _utc(now)
        with self._lock:
            try:
                row = connection.execute(
                    "SELECT * FROM highlights_cache WHERE cache_key = ?", (key,)
                ).fetchone()
                if row is None:
                    return None
                expected = _MODEL_BY_KIND.get(str(row["response_kind"]))
                if (
                    expected is not model
                    or int(row["schema_version"]) != CACHE_SCHEMA_VERSION
                    or int(row["payload_bytes"]) > self.max_payload_bytes
                ):
                    raise ValueError("cache schema mismatch")
                encoded = str(row["payload_json"]).encode("utf-8")
                if len(encoded) != int(row["payload_bytes"]):
                    raise ValueError("cache payload size mismatch")
                if hashlib.sha256(encoded).hexdigest() != row["content_fingerprint"]:
                    raise ValueError("cache fingerprint mismatch")
                value = model.model_validate(json.loads(encoded))
                stored_at = _parse_utc(str(row["stored_at_utc"]))
                fresh_until = _parse_utc(str(row["fresh_until_utc"]))
                stale = fresh_until <= current
                if stale and not allow_stale:
                    return None
                connection.execute(
                    "UPDATE highlights_cache SET last_accessed_utc = ? WHERE cache_key = ?",
                    (current.isoformat(), key),
                )
                connection.commit()
                if count_metrics:
                    self._counters["persistent_cache_hit_count"] += 1
                    if stale:
                        self._counters["persistent_cache_stale_hit_count"] += 1
                return PersistentCacheHit(value, stale, stored_at, fresh_until)
            except (json.JSONDecodeError, TypeError, ValueError, sqlite3.Error):
                self._counters["persistent_cache_error_count"] += 1
                self._delete_invalid(key)
                return None

    def set(
        self,
        key: str,
        kind: str,
        value: BaseModel,
        *,
        ttl_seconds: int | float,
        now: datetime | None = None,
    ) -> bool:
        connection = self._connection
        expected = _MODEL_BY_KIND.get(kind)
        if connection is None or expected is None or not isinstance(value, expected):
            return False
        try:
            stable_cache_key(kind, *key.split(":")[2:])
            current = _utc(now)
            canonical = expected.model_validate(value.model_dump(mode="json", by_alias=True))
            payload_json, encoded, fingerprint = _canonical_payload(canonical)
            if len(encoded) > self.max_payload_bytes:
                self._counters["persistent_cache_rejected_write_count"] += 1
                return False
            ttl = float(ttl_seconds)
            if ttl <= 0:
                raise ValueError("cache ttl must be positive")
            score = completeness_score(canonical)
            status = _game_status(canonical)
            source_as_of = getattr(canonical, "as_of_beijing", None)
            fresh_until = current + timedelta(seconds=ttl)
        except (TypeError, ValueError):
            self._counters["persistent_cache_rejected_write_count"] += 1
            return False

        with self._lock:
            try:
                old_row = connection.execute(
                    "SELECT * FROM highlights_cache WHERE cache_key = ?", (key,)
                ).fetchone()
                if old_row is not None:
                    old_kind = str(old_row["response_kind"])
                    old_model = _MODEL_BY_KIND.get(old_kind)
                    old_value = (
                        old_model.model_validate(json.loads(str(old_row["payload_json"])))
                        if old_model is not None
                        else None
                    )
                    if (
                        old_kind != kind
                        or old_value is None
                        or score < int(old_row["completeness_score"])
                        or _has_final_score_conflict(old_value, canonical)
                    ):
                        self._counters["persistent_cache_rejected_write_count"] += 1
                        return False
                connection.execute(
                    """
                    INSERT INTO highlights_cache (
                        cache_key, response_kind, schema_version, payload_json,
                        game_status, completeness_score, content_fingerprint,
                        source_as_of, stored_at_utc, fresh_until_utc,
                        last_accessed_utc, payload_bytes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        response_kind=excluded.response_kind,
                        schema_version=excluded.schema_version,
                        payload_json=excluded.payload_json,
                        game_status=excluded.game_status,
                        completeness_score=excluded.completeness_score,
                        content_fingerprint=excluded.content_fingerprint,
                        source_as_of=excluded.source_as_of,
                        stored_at_utc=excluded.stored_at_utc,
                        fresh_until_utc=excluded.fresh_until_utc,
                        last_accessed_utc=excluded.last_accessed_utc,
                        payload_bytes=excluded.payload_bytes
                    """,
                    (
                        key,
                        kind,
                        CACHE_SCHEMA_VERSION,
                        payload_json,
                        status,
                        score,
                        fingerprint,
                        source_as_of,
                        current.isoformat(),
                        fresh_until.isoformat(),
                        current.isoformat(),
                        len(encoded),
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM highlights_cache
                    WHERE fresh_until_utc <= ?
                      AND game_status IN ('scheduled', 'live', 'postponed', 'unknown', 'mixed')
                      AND cache_key <> ?
                    """,
                    (current.isoformat(), key),
                )
                row_count = int(
                    connection.execute("SELECT COUNT(*) FROM highlights_cache").fetchone()[0]
                )
                overflow = row_count - self.max_entries
                if overflow > 0:
                    connection.execute(
                        """
                        DELETE FROM highlights_cache WHERE cache_key IN (
                            SELECT cache_key FROM highlights_cache
                            ORDER BY last_accessed_utc ASC, stored_at_utc ASC
                            LIMIT ?
                        )
                        """,
                        (overflow,),
                    )
                connection.commit()
                self._counters["persistent_cache_write_count"] += 1
                return True
            except (json.JSONDecodeError, TypeError, ValueError, sqlite3.Error):
                self._counters["persistent_cache_error_count"] += 1
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                return False

    def acquire_refresh(self, key: str, *, now: datetime | None = None) -> str | None:
        connection = self._connection
        if connection is None:
            return None
        current = _utc(now)
        token = uuid4().hex
        expires = current + timedelta(seconds=self.lease_seconds)
        with self._lock:
            try:
                connection.execute(
                    "DELETE FROM highlights_refresh_lease WHERE expires_at_utc <= ?",
                    (current.isoformat(),),
                )
                connection.execute(
                    "INSERT INTO highlights_refresh_lease VALUES (?, ?, ?)",
                    (key, token, expires.isoformat()),
                )
                connection.commit()
                return token
            except sqlite3.IntegrityError:
                connection.rollback()
                self._counters["persistent_cache_refresh_coalesced_count"] += 1
                return None
            except sqlite3.Error:
                connection.rollback()
                self._counters["persistent_cache_error_count"] += 1
                return None

    def release_refresh(self, key: str, owner_token: str) -> bool:
        connection = self._connection
        if connection is None:
            return False
        with self._lock:
            try:
                cursor = connection.execute(
                    "DELETE FROM highlights_refresh_lease WHERE cache_key = ? AND owner_token = ?",
                    (key, owner_token),
                )
                connection.commit()
                return cursor.rowcount == 1
            except sqlite3.Error:
                self._counters["persistent_cache_error_count"] += 1
                return False

    def count(self) -> int:
        connection = self._connection
        if connection is None:
            return 0
        with self._lock:
            try:
                row = connection.execute("SELECT COUNT(*) FROM highlights_cache").fetchone()
                return int(row[0])
            except sqlite3.Error:
                self._counters["persistent_cache_error_count"] += 1
                return 0

    def counters(self) -> dict[str, int]:
        return dict(self._counters)

    def health_snapshot(self) -> dict[str, str | int]:
        """Return bounded operational state without storage paths or cache keys."""

        return {
            "status": self.status,
            "entries": self.count(),
            **self.counters(),
        }

    def close(self) -> None:
        with self._lock:
            if self._connection is None:
                return
            try:
                self._connection.close()
            except sqlite3.Error:
                self._counters["persistent_cache_error_count"] += 1
            finally:
                self._connection = None
                self.available = False


__all__ = [
    "PersistentCacheHit",
    "SQLiteHighlightsCache",
    "completeness_score",
    "stable_cache_key",
]
