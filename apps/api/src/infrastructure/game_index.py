"""Queryable SQLite index for public NBA games and narrative evidence.

This is deliberately separate from ``SQLiteHighlightsCache``.  The latter is
an exact HTTP projection cache; this module provides canonical relational
filters plus FTS5/BM25 retrieval for chat and Agent tools.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from apps.api.src.domain.models import (
    EntityRef,
    Evidence,
    Game,
    GameBundle,
    GameFilters,
    GameStatus,
    NewsItem,
    NewsQuery,
    PlayByPlayBundle,
    StatLine,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_:-]{2,64}|[\u3400-\u9fff]{2,24}")
_SEASON_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-/](\d{2}|(?:19|20)\d{2})(?!\d)")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_TOPIC_GROUPS = (
    (
        "阵容",
        "调整",
        "交易",
        "签约",
        "续约",
        "补强",
        "引援",
        "加盟",
        "离队",
        "裁员",
        "轮换",
        "自由市场",
    ),
    ("伤病", "受伤", "缺阵", "复出"),
    ("教练", "主帅", "执教"),
    ("战术", "复盘", "防守", "进攻", "挡拆", "联防"),
    ("得分",),
    ("篮板",),
    ("助攻",),
    ("场馆", "球馆", "地点", "举办"),
    ("时长", "耗时", "多久"),
)
_TOPIC_TERMS = tuple(dict.fromkeys(term for group in _TOPIC_GROUPS for term in group))
_CLAUSE_RE = re.compile(r"[，,。！？!?；;：:\n]+")


@dataclass(frozen=True, slots=True)
class IndexHit[T]:
    data: T
    evidence: list[Evidence]
    retrieved_at_utc: datetime
    partial: bool = False


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("index timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _json(value: object) -> str:
    def convert(item: object) -> object:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, Enum):
            return item.value
        raise TypeError(f"unsupported index value: {type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=convert,
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _truncate_utf8(value: str, maximum: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value
    return encoded[:maximum].decode("utf-8", errors="ignore").rstrip()


def _season_from_text(text: str) -> str | None:
    match = _SEASON_RE.search(text)
    if match:
        start = int(match.group(1))
        raw_end = match.group(2)
        end = int(raw_end) if len(raw_end) == 4 else (start // 100) * 100 + int(raw_end)
        if end == start + 1:
            return f"{start:04d}-{end % 100:02d}"
    year = _YEAR_RE.search(text)
    if year:
        end = int(year.group(1))
        return f"{end - 1:04d}-{end % 100:02d}"
    return None


def _canonical_terms(text: str, refs: list[EntityRef], season: str | None) -> str:
    values: list[str] = []
    for ref in refs:
        values.append(ref.canonical_id.casefold())
        values.append(ref.display_name.casefold())
    if season:
        values.append("season_" + season.replace("-", "_"))
        values.append(season)
    values.extend(term for term in _TOPIC_TERMS if term in text)
    values.extend(token.casefold() for token in _TOKEN_RE.findall(text))
    return " ".join(dict.fromkeys(value for value in values if value))[:4_000]


def _expanded_topic_terms(text: str) -> list[str]:
    """Expand a query topic within a small, auditable basketball vocabulary."""

    values: list[str] = []
    for group in _TOPIC_GROUPS:
        if any(term in text for term in group):
            values.extend(group)
    return list(dict.fromkeys(values))


def _subject_terms(refs: list[EntityRef]) -> list[str]:
    values: list[str] = []
    for ref in refs:
        values.extend((ref.display_name, *ref.aliases, ref.canonical_id))
    return list(
        dict.fromkeys(value.casefold() for value in values if len(value.strip()) >= 2)
    )


def _topic_rank_key(
    title: str,
    content: str,
    subject_terms: list[str],
    topic_terms: list[str],
) -> tuple[int, int, int, int, int]:
    """Return a subject-aware lexical score before the stable BM25 position.

    Search summaries often mention the requested team only as background and
    then discuss another club.  Co-occurrence inside the same bounded clause
    is therefore stronger evidence than global term frequency.
    """

    folded_title = title.casefold()
    folded_content = content.casefold()

    def has_subject(value: str) -> bool:
        return any(term in value for term in subject_terms)

    def topic_count(value: str) -> int:
        return sum(term in value for term in topic_terms)

    title_clauses = [part for part in _CLAUSE_RE.split(folded_title) if part]
    content_clauses = [part for part in _CLAUSE_RE.split(folded_content) if part]
    title_coherent = sum(
        has_subject(part) and topic_count(part) > 0 for part in title_clauses
    )
    content_coherent = sum(
        has_subject(part) and topic_count(part) > 0 for part in content_clauses
    )
    return (
        content_coherent,
        title_coherent,
        topic_count(folded_title),
        min(topic_count(folded_content), 8),
        int(has_subject(folded_title)),
    )


def _game_completeness(bundle: GameBundle) -> int:
    game = bundle.game
    fields = (
        game.home_score,
        game.away_score,
        game.series_id,
        game.series_game_number,
        game.venue,
        game.home_coach,
        game.away_coach,
        game.duration_seconds,
        game.attendance,
    )
    score = 100 + sum(value is not None for value in fields) * 10
    score += len(bundle.stat_lines) * 4 + len(bundle.leaders) * 2
    if bundle.plays is not None:
        score += len(bundle.plays.events)
    if game.status is GameStatus.FINAL:
        score += 50
    return score


class GameIndex:
    """Thread-safe, fail-open SQLite store with relational and BM25 search."""

    def __init__(
        self,
        database: str | Path,
        *,
        max_documents: int = 10_000,
        max_document_bytes: int = 8_192,
        busy_timeout_ms: int = 1_500,
    ) -> None:
        self.database = str(database)
        self.max_documents = max(1, int(max_documents))
        self.max_document_bytes = max(1_024, int(max_document_bytes))
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self.available = False
        self._counters = {
            "index_read_count": 0,
            "index_hit_count": 0,
            "index_write_count": 0,
            "index_rejected_write_count": 0,
            "index_error_count": 0,
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
                CREATE TABLE IF NOT EXISTS indexed_games (
                    game_id TEXT PRIMARY KEY,
                    source_ref TEXT NOT NULL,
                    source_game_id TEXT NOT NULL,
                    season_label TEXT NOT NULL,
                    start_utc TEXT NOT NULL,
                    home_id TEXT NOT NULL,
                    away_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    home_score INTEGER,
                    away_score INTEGER,
                    series_id TEXT,
                    series_game_number INTEGER,
                    game_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    evidence_state TEXT NOT NULL,
                    retrieved_at_utc TEXT NOT NULL,
                    completeness_score INTEGER NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    UNIQUE(source_ref, source_game_id)
                );
                CREATE INDEX IF NOT EXISTS idx_indexed_games_season_start
                    ON indexed_games(season_label, start_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_indexed_games_home_start
                    ON indexed_games(home_id, start_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_indexed_games_away_start
                    ON indexed_games(away_id, start_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_indexed_games_series_game
                    ON indexed_games(series_id, series_game_number);

                CREATE TABLE IF NOT EXISTS indexed_player_stats (
                    game_id TEXT NOT NULL REFERENCES indexed_games(game_id) ON DELETE CASCADE,
                    player_id TEXT NOT NULL,
                    team_id TEXT,
                    stat_json TEXT NOT NULL,
                    completeness_score INTEGER NOT NULL,
                    PRIMARY KEY(game_id, player_id)
                );

                CREATE TABLE IF NOT EXISTS indexed_plays (
                    game_id TEXT NOT NULL REFERENCES indexed_games(game_id) ON DELETE CASCADE,
                    provider_index INTEGER NOT NULL,
                    play_json TEXT NOT NULL,
                    PRIMARY KEY(game_id, provider_index)
                );

                CREATE TABLE IF NOT EXISTS source_documents (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    canonical_terms TEXT NOT NULL,
                    season_label TEXT,
                    game_id TEXT REFERENCES indexed_games(game_id) ON DELETE SET NULL,
                    source_ref TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    published_utc TEXT,
                    retrieved_at_utc TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_document_entities (
                    document_id TEXT NOT NULL REFERENCES source_documents(document_id)
                        ON DELETE CASCADE,
                    entity_id TEXT NOT NULL,
                    PRIMARY KEY(document_id, entity_id)
                );
                CREATE INDEX IF NOT EXISTS idx_source_document_entities_entity
                    ON source_document_entities(entity_id, document_id);
                CREATE INDEX IF NOT EXISTS idx_source_documents_season
                    ON source_documents(season_label, retrieved_at_utc DESC);

                CREATE VIRTUAL TABLE IF NOT EXISTS source_documents_fts USING fts5(
                    title,
                    content,
                    canonical_terms,
                    content='source_documents',
                    content_rowid='rowid',
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TRIGGER IF NOT EXISTS source_documents_ai AFTER INSERT ON source_documents
                BEGIN
                    INSERT INTO source_documents_fts(rowid, title, content, canonical_terms)
                    VALUES (new.rowid, new.title, new.content, new.canonical_terms);
                END;
                CREATE TRIGGER IF NOT EXISTS source_documents_ad AFTER DELETE ON source_documents
                BEGIN
                    INSERT INTO source_documents_fts(
                        source_documents_fts, rowid, title, content, canonical_terms
                    ) VALUES ('delete', old.rowid, old.title, old.content, old.canonical_terms);
                END;
                CREATE TRIGGER IF NOT EXISTS source_documents_au AFTER UPDATE ON source_documents
                BEGIN
                    INSERT INTO source_documents_fts(
                        source_documents_fts, rowid, title, content, canonical_terms
                    ) VALUES ('delete', old.rowid, old.title, old.content, old.canonical_terms);
                    INSERT INTO source_documents_fts(rowid, title, content, canonical_terms)
                    VALUES (new.rowid, new.title, new.content, new.canonical_terms);
                END;
                """
            )
            connection.commit()
            self._connection = connection
            self.available = True
        except (OSError, sqlite3.Error, ValueError):
            self._counters["index_error_count"] += 1

    @property
    def status(self) -> str:
        return "ok" if self.available else "degraded"

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                except sqlite3.Error:
                    pass
            self._connection = None
            self.available = False

    def counters(self) -> dict[str, int]:
        return dict(self._counters)

    def counts(self) -> dict[str, int]:
        connection = self._connection
        if connection is None:
            return {"games": 0, "player_stats": 0, "plays": 0, "documents": 0}
        names = {
            "games": "indexed_games",
            "player_stats": "indexed_player_stats",
            "plays": "indexed_plays",
            "documents": "source_documents",
        }
        try:
            with self._lock:
                return {
                    key: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    for key, table in names.items()
                }
        except sqlite3.Error:
            self._counters["index_error_count"] += 1
            return {key: 0 for key in names}

    def upsert_bundle(
        self,
        bundle: GameBundle,
        evidence: list[Evidence],
        *,
        origin: str,
        retrieved_at_utc: datetime | None = None,
    ) -> str:
        connection = self._connection
        if origin != "public" or not evidence or any(
            item.source_class.value in {"FIXTURE", "SEARCH"} for item in evidence
        ):
            self._counters["index_rejected_write_count"] += 1
            return "rejected_origin"
        if connection is None:
            return "unavailable"
        retrieved = _aware_utc(retrieved_at_utc or max(item.fetched_at_utc for item in evidence))
        incoming = bundle
        game = incoming.game
        try:
            with self._lock:
                row = connection.execute(
                    "SELECT * FROM indexed_games WHERE game_id = ?", (game.game_id,)
                ).fetchone()
                previous: GameBundle | None = None
                if row is not None:
                    old_game = Game.model_validate_json(row["game_json"])
                    old_stats = self._load_stat_lines_locked(game.game_id)
                    old_plays = self._load_plays_locked(game.game_id)
                    previous = GameBundle(
                        game=old_game,
                        stat_lines=old_stats,
                        leaders=old_stats,
                        plays=old_plays,
                    )
                    if (
                        old_game.status is GameStatus.FINAL
                        and game.status is GameStatus.FINAL
                        and None not in (old_game.home_score, old_game.away_score)
                        and None not in (game.home_score, game.away_score)
                        and (old_game.home_score, old_game.away_score)
                        != (game.home_score, game.away_score)
                    ):
                        self._counters["index_rejected_write_count"] += 1
                        return "rejected_conflict"
                    game = self._merge_game(old_game, game)
                    existing_stats = {line.subject.canonical_id: line for line in old_stats}
                    for line in incoming.stat_lines:
                        existing_stats[line.subject.canonical_id] = line
                    plays = incoming.plays or old_plays
                    incoming = incoming.model_copy(
                        update={
                            "game": game,
                            "stat_lines": list(existing_stats.values()),
                            "leaders": list(existing_stats.values()),
                            "plays": plays,
                        }
                    )
                score = _game_completeness(incoming)
                fingerprint = _fingerprint(incoming)
                if row is not None and row["content_fingerprint"] == fingerprint:
                    return "unchanged"
                if previous is not None and score < int(row["completeness_score"]):
                    return "unchanged"
                primary = evidence[0]
                source_game_id = game.game_id.split(":", 1)[-1]
                connection.execute(
                    """
                    INSERT INTO indexed_games (
                        game_id, source_ref, source_game_id, season_label, start_utc,
                        home_id, away_id, status, home_score, away_score, series_id,
                        series_game_number, game_json, evidence_json, evidence_state,
                        retrieved_at_utc, completeness_score, content_fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(game_id) DO UPDATE SET
                        source_ref=excluded.source_ref,
                        source_game_id=excluded.source_game_id,
                        season_label=excluded.season_label,
                        start_utc=excluded.start_utc,
                        home_id=excluded.home_id,
                        away_id=excluded.away_id,
                        status=excluded.status,
                        home_score=excluded.home_score,
                        away_score=excluded.away_score,
                        series_id=excluded.series_id,
                        series_game_number=excluded.series_game_number,
                        game_json=excluded.game_json,
                        evidence_json=excluded.evidence_json,
                        evidence_state=excluded.evidence_state,
                        retrieved_at_utc=excluded.retrieved_at_utc,
                        completeness_score=excluded.completeness_score,
                        content_fingerprint=excluded.content_fingerprint
                    """,
                    (
                        game.game_id,
                        primary.source_ref,
                        source_game_id,
                        game.season.label,
                        game.start_utc.astimezone(UTC).isoformat(),
                        game.home.canonical_id,
                        game.away.canonical_id,
                        game.status.value,
                        game.home_score,
                        game.away_score,
                        game.series_id,
                        game.series_game_number,
                        incoming.game.model_dump_json(),
                        _json(evidence),
                        (
                            "partial"
                            if any(item.source_class.value == "SEARCH" for item in evidence)
                            else "verified"
                        ),
                        retrieved.isoformat(),
                        score,
                        fingerprint,
                    ),
                )
                for line in incoming.stat_lines:
                    connection.execute(
                        """
                        INSERT INTO indexed_player_stats(
                            game_id, player_id, team_id, stat_json, completeness_score
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(game_id, player_id) DO UPDATE SET
                            team_id=excluded.team_id,
                            stat_json=excluded.stat_json,
                            completeness_score=excluded.completeness_score
                        WHERE excluded.completeness_score >= indexed_player_stats.completeness_score
                        """,
                        (
                            game.game_id,
                            line.subject.canonical_id,
                            str(line.metrics.get("team_id") or "") or None,
                            line.model_dump_json(),
                            sum(value is not None for value in line.metrics.values()),
                        ),
                    )
                if incoming.plays is not None:
                    for event in incoming.plays.events:
                        connection.execute(
                            """
                            INSERT INTO indexed_plays(game_id, provider_index, play_json)
                            VALUES (?, ?, ?)
                            ON CONFLICT(game_id, provider_index) DO UPDATE SET
                                play_json=excluded.play_json
                            """,
                            (game.game_id, event.provider_index, event.model_dump_json()),
                        )
                connection.commit()
                self._counters["index_write_count"] += 1
                return "updated" if row is not None else "inserted"
        except (sqlite3.Error, ValueError, TypeError):
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            self._counters["index_error_count"] += 1
            return "unavailable"

    @staticmethod
    def _merge_game(old: Game, new: Game) -> Game:
        values = new.model_dump()
        for field in (
            "series_id",
            "series_game_number",
            "venue",
            "home_coach",
            "away_coach",
            "duration_seconds",
            "attendance",
        ):
            if values.get(field) is None:
                values[field] = getattr(old, field)
        if new.home_score is None and old.home_score is not None:
            values["home_score"] = old.home_score
        if new.away_score is None and old.away_score is not None:
            values["away_score"] = old.away_score
        if old.status is GameStatus.FINAL and new.status is not GameStatus.FINAL:
            values["status"] = old.status
        return Game.model_validate(values)

    def _load_stat_lines_locked(self, game_id: str) -> list[StatLine]:
        assert self._connection is not None
        rows = self._connection.execute(
            "SELECT stat_json FROM indexed_player_stats WHERE game_id = ? ORDER BY rowid",
            (game_id,),
        ).fetchall()
        return [StatLine.model_validate_json(row[0]) for row in rows]

    def _load_plays_locked(self, game_id: str) -> PlayByPlayBundle | None:
        assert self._connection is not None
        rows = self._connection.execute(
            "SELECT play_json FROM indexed_plays WHERE game_id = ? ORDER BY provider_index",
            (game_id,),
        ).fetchall()
        if not rows:
            return None
        from apps.api.src.domain.models import PlayEvent

        return PlayByPlayBundle(
            game_id=game_id,
            events=[PlayEvent.model_validate_json(row[0]) for row in rows],
            sequence_valid=False,
        )

    @staticmethod
    def _evidence(row: sqlite3.Row) -> list[Evidence]:
        values = json.loads(row["evidence_json"])
        return [Evidence.model_validate(item) for item in values]

    def search_games(self, filters: GameFilters, *, limit: int = 200) -> IndexHit[list[Game]]:
        self._counters["index_read_count"] += 1
        connection = self._connection
        if connection is None:
            return IndexHit([], [], datetime.now(UTC))
        clauses: list[str] = []
        params: list[object] = []
        if filters.season is not None:
            clauses.append("season_label = ?")
            params.append(filters.season.label)
        if filters.date_range is not None:
            clauses.extend(("start_utc >= ?", "start_utc < ?"))
            params.extend(
                (
                    filters.date_range.start_inclusive.astimezone(UTC).isoformat(),
                    filters.date_range.end_exclusive.astimezone(UTC).isoformat(),
                )
            )
        if filters.status is not None:
            clauses.append("status = ?")
            params.append(filters.status.value)
        if filters.series_game_number is not None:
            clauses.append("series_game_number = ?")
            params.append(filters.series_game_number)
        for team_id in filters.team_ids:
            clauses.append("(home_id = ? OR away_id = ?)")
            params.extend((team_id, team_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        try:
            with self._lock:
                rows = connection.execute(
                    "SELECT * FROM indexed_games"
                    + where
                    + " ORDER BY start_utc DESC LIMIT ?",
                    (*params, max(1, min(int(limit), 500))),
                ).fetchall()
            games = [Game.model_validate_json(row["game_json"]) for row in rows]
            evidence = [item for row in rows for item in self._evidence(row)]
            if games:
                self._counters["index_hit_count"] += 1
            retrieved = max(
                (datetime.fromisoformat(row["retrieved_at_utc"]) for row in rows),
                default=datetime.now(UTC),
            )
            partial = any(row["evidence_state"] != "verified" for row in rows)
            return IndexHit(games, evidence, retrieved, partial)
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            self._counters["index_error_count"] += 1
            return IndexHit([], [], datetime.now(UTC))

    def get_game_summary(self, game_id: str) -> IndexHit[GameBundle] | None:
        self._counters["index_read_count"] += 1
        connection = self._connection
        if connection is None:
            return None
        try:
            with self._lock:
                row = connection.execute(
                    "SELECT * FROM indexed_games WHERE game_id = ?", (game_id,)
                ).fetchone()
                if row is None:
                    return None
                stats = self._load_stat_lines_locked(game_id)
                plays = self._load_plays_locked(game_id)
            self._counters["index_hit_count"] += 1
            return IndexHit(
                GameBundle(
                    game=Game.model_validate_json(row["game_json"]),
                    stat_lines=stats,
                    leaders=stats,
                    plays=plays,
                ),
                self._evidence(row),
                datetime.fromisoformat(row["retrieved_at_utc"]),
                row["evidence_state"] != "verified",
            )
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            self._counters["index_error_count"] += 1
            return None

    def get_play_by_play(self, game_id: str) -> IndexHit[PlayByPlayBundle] | None:
        summary = self.get_game_summary(game_id)
        if summary is None or summary.data.plays is None:
            return None
        return IndexHit(
            summary.data.plays,
            summary.evidence,
            summary.retrieved_at_utc,
            summary.partial,
        )

    def get_player_stats(self, player_id: str) -> IndexHit[list[StatLine]]:
        self._counters["index_read_count"] += 1
        connection = self._connection
        if connection is None:
            return IndexHit([], [], datetime.now(UTC))
        try:
            with self._lock:
                rows = connection.execute(
                    """
                    SELECT s.stat_json, g.evidence_json, g.retrieved_at_utc,
                           g.evidence_state
                    FROM indexed_player_stats s
                    JOIN indexed_games g ON g.game_id = s.game_id
                    WHERE s.player_id = ?
                    ORDER BY g.start_utc DESC
                    LIMIT 500
                    """,
                    (player_id,),
                ).fetchall()
            lines = [StatLine.model_validate_json(row["stat_json"]) for row in rows]
            evidence = [item for row in rows for item in self._evidence(row)]
            if lines:
                self._counters["index_hit_count"] += 1
            return IndexHit(
                lines,
                evidence,
                max(
                    (datetime.fromisoformat(row["retrieved_at_utc"]) for row in rows),
                    default=datetime.now(UTC),
                ),
                any(row["evidence_state"] != "verified" for row in rows),
            )
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            self._counters["index_error_count"] += 1
            return IndexHit([], [], datetime.now(UTC))

    def upsert_document(
        self,
        item: NewsItem,
        evidence: Evidence,
        *,
        origin: str,
        game_id: str | None = None,
    ) -> bool:
        connection = self._connection
        if origin != "public" or evidence.source_class.value == "FIXTURE":
            self._counters["index_rejected_write_count"] += 1
            return False
        if connection is None:
            return False
        content = _truncate_utf8(item.summary or "", self.max_document_bytes)
        title = _truncate_utf8(item.title, min(self.max_document_bytes, 2_000))
        combined = f"{title} {content}"
        season = _season_from_text(combined)
        entity_ids = list(dict.fromkeys(ref.canonical_id for ref in item.subject_refs))
        terms = _canonical_terms(combined, item.subject_refs, season)
        fingerprint = _fingerprint((title, content, terms, entity_ids, season, game_id))
        try:
            with self._lock:
                connection.execute(
                    """
                    INSERT INTO source_documents(
                        document_id, title, content, canonical_terms, season_label,
                        game_id, source_ref, evidence_json, published_utc,
                        retrieved_at_utc, content_fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(document_id) DO UPDATE SET
                        title=excluded.title,
                        content=excluded.content,
                        canonical_terms=excluded.canonical_terms,
                        season_label=excluded.season_label,
                        game_id=COALESCE(excluded.game_id, source_documents.game_id),
                        source_ref=excluded.source_ref,
                        evidence_json=excluded.evidence_json,
                        published_utc=excluded.published_utc,
                        retrieved_at_utc=excluded.retrieved_at_utc,
                        content_fingerprint=excluded.content_fingerprint
                    """,
                    (
                        item.news_id,
                        title,
                        content,
                        terms,
                        season,
                        game_id,
                        evidence.source_ref,
                        _json([evidence]),
                        item.published_utc.astimezone(UTC).isoformat()
                        if item.published_utc
                        else None,
                        evidence.fetched_at_utc.astimezone(UTC).isoformat(),
                        fingerprint,
                    ),
                )
                connection.execute(
                    "DELETE FROM source_document_entities WHERE document_id = ?",
                    (item.news_id,),
                )
                connection.executemany(
                    "INSERT INTO source_document_entities(document_id, entity_id) VALUES (?, ?)",
                    [(item.news_id, entity_id) for entity_id in entity_ids],
                )
                connection.execute(
                    """
                    DELETE FROM source_documents WHERE rowid IN (
                        SELECT rowid FROM source_documents
                        ORDER BY retrieved_at_utc DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (self.max_documents,),
                )
                connection.commit()
                self._counters["index_write_count"] += 1
                return True
        except (sqlite3.Error, ValueError, TypeError):
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            self._counters["index_error_count"] += 1
            return False

    def search_documents(self, query: NewsQuery) -> IndexHit[list[NewsItem]]:
        self._counters["index_read_count"] += 1
        connection = self._connection
        if connection is None:
            return IndexHit([], [], datetime.now(UTC), partial=True)
        text = " ".join(query.keywords)
        season = _season_from_text(text)
        topic_terms = _expanded_topic_terms(text)
        subject_terms = _subject_terms(list(query.subject_refs))
        terms = _canonical_terms(text, query.subject_refs, season)
        # Quote only tokens produced by the bounded tokenizer/canonical refs;
        # never pass raw user syntax into FTS MATCH.
        tokens = list(dict.fromkeys(_TOKEN_RE.findall(terms)))[:24]
        if not tokens:
            return IndexHit([], [], datetime.now(UTC), partial=True)
        match = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
        clauses = ["source_documents_fts MATCH ?"]
        params: list[object] = [match]
        if season:
            clauses.append("d.season_label = ?")
            params.append(season)
        for ref in query.subject_refs:
            clauses.append(
                "EXISTS (SELECT 1 FROM source_document_entities e "
                "WHERE e.document_id = d.document_id AND e.entity_id = ?)"
            )
            params.append(ref.canonical_id)
        try:
            with self._lock:
                rows = connection.execute(
                    """
                    SELECT d.*, bm25(source_documents_fts, 3.0, 1.0, 6.0) AS rank
                    FROM source_documents_fts
                    JOIN source_documents d ON d.rowid = source_documents_fts.rowid
                    WHERE """
                    + " AND ".join(clauses)
                    + " ORDER BY rank ASC, d.retrieved_at_utc DESC LIMIT ?",
                    (*params, min(max(query.limit * 8, 20), 100)),
                ).fetchall()
            if topic_terms:
                ranked_rows: list[
                    tuple[tuple[int, int, int, int, int], int, sqlite3.Row]
                ] = []
                for position, row in enumerate(rows):
                    title = str(row["title"] or "")
                    content = str(row["content"] or "")
                    topic_score = _topic_rank_key(
                        title,
                        content,
                        subject_terms,
                        topic_terms,
                    )
                    if any(topic_score[:4]):
                        ranked_rows.append((topic_score, position, row))
                if ranked_rows:
                    title_centered = [
                        item
                        for item in ranked_rows
                        if item[0][2] > 0 and item[0][4] > 0
                    ]
                    if title_centered:
                        ranked_rows = title_centered
                    ranked_rows.sort(
                        key=lambda item: tuple(-value for value in item[0]) + (item[1],)
                    )
                    rows = [item[2] for item in ranked_rows]
            rows = rows[: query.limit]
            items: list[NewsItem] = []
            evidence: list[Evidence] = []
            refs = list(query.subject_refs)
            for row in rows:
                row_evidence = self._evidence(row)
                if not row_evidence:
                    continue
                evidence.extend(row_evidence)
                items.append(
                    NewsItem(
                        news_id=row["document_id"],
                        title=row["title"],
                        summary=row["content"] or None,
                        published_utc=(
                            datetime.fromisoformat(row["published_utc"])
                            if row["published_utc"]
                            else None
                        ),
                        subject_refs=refs,
                        evidence_id=row_evidence[0].evidence_id,
                    )
                )
            if items:
                self._counters["index_hit_count"] += 1
            return IndexHit(
                items,
                evidence,
                max(
                    (datetime.fromisoformat(row["retrieved_at_utc"]) for row in rows),
                    default=datetime.now(UTC),
                ),
                partial=True,
            )
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            self._counters["index_error_count"] += 1
            return IndexHit([], [], datetime.now(UTC), partial=True)


__all__ = ["GameIndex", "IndexHit"]
