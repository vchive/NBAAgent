"""Deterministic, offline NBA provider used by the first runnable slice.

The class implements the same typed operations as the public-data gateway.  It
loads versioned JSON snapshots once, validates every record through the
normalizer, and supports a few failure scenarios for contract tests via
``FIXTURE_SCENARIO`` (``timeout``, ``rate_limit``, ``invalid_json``, ``empty``).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.api.src.application.ports import ProviderResult, RequestBudget
from apps.api.src.domain.errors import ProviderError, ProviderErrorKind
from apps.api.src.domain.models import (
    EntityKind,
    Game,
    GameBundle,
    GameFilters,
    HistoryQuery,
    HistoryRecord,
    NewsItem,
    NewsQuery,
    PlayByPlayBundle,
    SeasonLabel,
    Standing,
    StatLine,
    StatScope,
    StatsQuery,
)
from apps.api.src.providers.normalizer import Normalizer

DEFAULT_FIXTURE_DIR = Path(__file__).with_name("fixtures")


class FixtureProvider:
    """A small provider with observable operation counters.

    ``calls`` is intentionally public so tests and telemetry can assert that
    safety/clarification branches never touch a provider.
    """

    def __init__(
        self,
        fixture_dir: str | Path | None = None,
        *,
        now: datetime | None = None,
        scenario: str | None = None,
    ) -> None:
        self.fixture_dir = Path(fixture_dir or DEFAULT_FIXTURE_DIR)
        self.now = (now or datetime.now(UTC)).astimezone(UTC)
        self.scenario = (
            scenario if scenario is not None else os.getenv("FIXTURE_SCENARIO", "")
        ).lower()
        self.normalizer = Normalizer()
        self.calls = 0
        self.operation_calls: dict[str, int] = {}
        self._loaded = False
        self._games: list[Game] = []
        self._game_raw: dict[str, dict[str, Any]] = {}
        self._summaries: dict[str, dict[str, Any]] = {}
        self._pbp: dict[str, PlayByPlayBundle] = {}
        self._standings: list[Standing] = []
        self._history: list[HistoryRecord] = []
        self._news: list[NewsItem] = []
        self._news_partial = False

    def _load(self) -> None:
        if self._loaded:
            return
        if self.scenario == "invalid_json":
            raise ValueError("fixture JSON is invalid")
        try:
            games_raw = self._read_json("games.json")["games"]
            summary_raw = self._read_json("summary.json").get("summaries", {})
            pbp_raw = self._read_json("pbp.json").get("bundles", {})
            standings_raw = self._read_json("standings.json").get("standings", [])
            history_raw = self._read_json("history.json").get("records", [])
            # Keep custom/legacy fixture directories usable for non-news
            # operations; the bundled default always includes ``news.json``.
            news_payload = (
                self._read_json("news.json")
                if (self.fixture_dir / "news.json").is_file()
                else {"news": []}
            )
            news_raw = news_payload.get("news", news_payload.get("items", []))
            if not isinstance(news_raw, list):
                raise ValueError("fixture news must contain a list")
            self._games = [self.normalizer.game(item) for item in games_raw]
            self._game_raw = {item["game_id"]: item for item in games_raw}
            self._summaries = dict(summary_raw)
            self._pbp = {key: self.normalizer.pbp(value) for key, value in pbp_raw.items()}
            self._standings = [self.normalizer.standing(item) for item in standings_raw]
            self._history = [self.normalizer.history(item) for item in history_raw]
            news_values: list[NewsItem] = []
            news_partial = False
            for item in news_raw:
                try:
                    news_values.append(self.normalizer.news(item))
                except (TypeError, ValueError, KeyError):
                    # Keep valid articles available when one provider row is
                    # malformed; callers receive the explicit partial flag.
                    news_partial = True
            self._news = news_values
            self._news_partial = news_partial
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("fixture data could not be normalized") from exc
        self._loaded = True

    def _read_json(self, name: str) -> dict[str, Any]:
        with (self.fixture_dir / name).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError(f"fixture {name} must contain an object")
        return value

    def _result(
        self, data: Any, *, evidence_ids: list[str] | None = None, partial: bool = False
    ) -> ProviderResult[Any]:
        retrieved = self.now
        evidence = [
            self.normalizer.evidence(eid, retrieved_at_utc=retrieved, partial=partial)
            for eid in (evidence_ids or ["fixture:snapshot"])
        ]
        return ProviderResult(
            data=data, evidence=evidence, partial=partial, retrieved_at_utc=retrieved
        )

    def _failure(
        self, kind: ProviderErrorKind, message: str, *, retryable: bool
    ) -> ProviderResult[Any]:
        return ProviderResult(
            data=None,
            evidence=[],
            partial=False,
            error=ProviderError(kind=kind, retryable=retryable, safe_message=message),
            retrieved_at_utc=self.now,
        )

    def _begin(self, operation: str, budget: RequestBudget) -> ProviderResult[Any] | None:
        self.calls += 1
        self.operation_calls[operation] = self.operation_calls.get(operation, 0) + 1
        if not budget.reserve_operation():
            return self._failure(
                ProviderErrorKind.TIMEOUT, "request deadline exceeded", retryable=True
            )
        if self.scenario in {"timeout", "upstream_timeout"}:
            return self._failure(ProviderErrorKind.TIMEOUT, "upstream timed out", retryable=True)
        if self.scenario in {"rate_limit", "429", "upstream_rate_limit"}:
            return self._failure(
                ProviderErrorKind.RATE_LIMITED, "upstream rate limited", retryable=True
            )
        if self.scenario in {"auth", "upstream_auth"}:
            return self._failure(
                ProviderErrorKind.AUTH, "upstream authentication failed", retryable=False
            )
        if self.scenario in {"empty", "no_data"}:
            return self._result([])
        try:
            self._load()
        except ValueError:
            return self._failure(
                ProviderErrorKind.INVALID_JSON, "fixture payload invalid", retryable=False
            )
        return None

    @staticmethod
    def _matches_game(game: Game, filters: GameFilters) -> bool:
        if filters.season and game.season != filters.season:
            return False
        if filters.status and game.status != filters.status:
            return False
        if filters.team_ids and not (
            {game.home.canonical_id, game.away.canonical_id} & set(filters.team_ids)
        ):
            return False
        if filters.date_range:
            if not (
                filters.date_range.start_inclusive
                <= game.start_utc
                < filters.date_range.end_exclusive
            ):
                return False
        return True

    async def search_games(
        self, filters: GameFilters, budget: RequestBudget
    ) -> ProviderResult[list[Game]]:
        early = self._begin("search_games", budget)
        if early is not None:
            return early
        values = [game for game in self._games if self._matches_game(game, filters)]
        values.sort(key=lambda item: item.start_utc, reverse=True)
        return self._result(
            values, evidence_ids=[f"fixture:game:{game.game_id}" for game in values]
        )

    async def get_game_summary(
        self, game_id: str, budget: RequestBudget
    ) -> ProviderResult[GameBundle]:
        early = self._begin("get_game_summary", budget)
        if early is not None:
            return early
        game = next((item for item in self._games if item.game_id == game_id), None)
        if game is None:
            return self._failure(ProviderErrorKind.NOT_FOUND, "game not found", retryable=False)
        raw = dict(self._summaries.get(game_id, {}))
        raw.setdefault("stat_lines", [])
        raw.setdefault("leaders", [])
        raw.setdefault("series", None)
        # Include the authoritative PBP snapshot when one exists.
        if game_id in self._pbp:
            raw["plays"] = self._pbp[game_id].model_dump(mode="json")
        bundle = self.normalizer.bundle(raw, game_raw=self._game_raw[game_id])
        evidence_ids = [f"fixture:summary:{game_id}"] + [
            f"fixture:summary:{game_id}:{line.subject.canonical_id}" for line in bundle.leaders
        ]
        return self._result(bundle, evidence_ids=evidence_ids)

    async def get_play_by_play(
        self, game_id: str, budget: RequestBudget
    ) -> ProviderResult[PlayByPlayBundle]:
        early = self._begin("get_play_by_play", budget)
        if early is not None:
            return early
        value = self._pbp.get(game_id)
        if value is None:
            return self._failure(
                ProviderErrorKind.NOT_FOUND, "play by play not found", retryable=False
            )
        return self._result(value, evidence_ids=[f"fixture:pbp:{game_id}"])

    async def get_player_stats(
        self, query: StatsQuery, budget: RequestBudget
    ) -> ProviderResult[list[StatLine]]:
        early = self._begin("get_player_stats", budget)
        if early is not None:
            return early
        if query.subject.kind is not EntityKind.PLAYER:
            return self._result([], evidence_ids=["fixture:stats:none"])

        # Leader snapshots are game-scoped in the fixture.  Apply every typed
        # filter before projecting the requested scope; an earlier version only
        # checked ``game_id`` and consequently leaked rows from other seasons.
        game_by_id = {game.game_id: game for game in self._games}
        game_rows: list[tuple[Game, StatLine]] = []
        partial = False
        for game_id, raw in self._summaries.items():
            game = game_by_id.get(game_id)
            if game is None or not self._matches_stats_game(game, query):
                continue
            for item in raw.get("leaders", []):
                try:
                    subject = item.get("subject", {})
                    if subject.get("canonical_id") != query.subject.canonical_id:
                        continue
                    payload = dict(item)
                    # Fixture leader rows omit season; infer it from the
                    # authoritative game while retaining null metric values.
                    payload.setdefault("season", game.season.label)
                    row = self.normalizer.stat_line(payload)
                    game_rows.append((game, row))
                except (AttributeError, KeyError, TypeError, ValueError):
                    partial = True

        rows = self._project_stat_scope(query, game_rows)
        evidence_ids = [eid for row in rows for eid in row.evidence_ids]
        return self._result(
            rows,
            evidence_ids=list(dict.fromkeys(evidence_ids)) or ["fixture:stats:none"],
            partial=partial,
        )

    async def get_team_stats(
        self, query: StatsQuery, budget: RequestBudget
    ) -> ProviderResult[list[StatLine]]:
        # Team box-score stats are represented by the game score in the fixture.
        early = self._begin("get_team_stats", budget)
        if early is not None:
            return early
        if query.subject.kind is not EntityKind.TEAM:
            return self._result([], evidence_ids=["fixture:stats:none"])
        game_rows: list[tuple[Game, StatLine]] = []
        for game in self._games:
            if not self._matches_stats_game(game, query):
                continue
            if query.subject.canonical_id == game.home.canonical_id:
                value = game.home_score
            elif query.subject.canonical_id == game.away.canonical_id:
                value = game.away_score
            else:
                continue
            game_rows.append(
                (
                    game,
                    StatLine(
                        subject=query.subject,
                        game_id=game.game_id,
                        season=game.season,
                        scope=StatScope.GAME,
                        metrics={"points": value},
                        metric_definitions={"points": "球队得分"},
                        evidence_ids=[f"fixture:game:{game.game_id}"],
                    ),
                )
            )
        rows = self._project_stat_scope(query, game_rows)
        evidence_ids = [eid for row in rows for eid in row.evidence_ids]
        # Keep a partial marker when a selected game has a missing score.  The
        # value remains ``null``; no zero/default is fabricated by the fixture.
        partial = any(row.metrics.get("points") is None for _, row in game_rows)
        return self._result(
            rows,
            evidence_ids=list(dict.fromkeys(evidence_ids)) or ["fixture:stats:none"],
            partial=partial,
        )

    @staticmethod
    def _matches_stats_game(game: Game, query: StatsQuery) -> bool:
        """Apply the optional game/series/season/date filters consistently."""

        if query.game_id and game.game_id != query.game_id:
            return False
        if query.series_id and game.series_id != query.series_id:
            return False
        if query.season and game.season != query.season:
            return False
        if query.date_range and not (
            query.date_range.start_inclusive <= game.start_utc < query.date_range.end_exclusive
        ):
            return False
        return True

    @staticmethod
    def _project_stat_scope(
        query: StatsQuery,
        game_rows: list[tuple[Game, StatLine]],
    ) -> list[StatLine]:
        """Project game snapshots into the scope requested by ``StatsQuery``.

        The fixture has only a small set of box-score snapshots.  For a
        season/series/career request we therefore expose a deterministic sum of
        the available rows and a count, rather than returning misleading
        ``GAME`` rows.  Missing metric values stay ``None`` and are omitted from
        the sum; callers can inspect ``ProviderResult.partial`` for incompleteness.
        """

        if not game_rows:
            return []
        if query.scope is StatScope.GAME:
            return [row for _, row in game_rows]

        if query.scope is StatScope.SERIES:
            # ``StatsQuery`` validates series_id for this scope.  Retain the
            # explicit value even when a fixture has no matching game, which is
            # why the empty case above returns no row.
            series_id = query.series_id or game_rows[0][0].series_id
            if not series_id:
                return []
            season_value = (
                game_rows[0][0].season
                if all(game.season == game_rows[0][0].season for game, _ in game_rows)
                else None
            )
            return [
                FixtureProvider._aggregate_stat_rows(
                    query.subject,
                    game_rows,
                    scope=StatScope.SERIES,
                    series_id=series_id,
                    season=season_value,
                )
            ]

        if query.scope is StatScope.SEASON:
            season_value = query.season or game_rows[0][0].season
            return [
                FixtureProvider._aggregate_stat_rows(
                    query.subject,
                    game_rows,
                    scope=StatScope.SEASON,
                    season=season_value,
                )
            ]

        # CAREER intentionally carries no season/game/series identifier.
        return [
            FixtureProvider._aggregate_stat_rows(
                query.subject,
                game_rows,
                scope=StatScope.CAREER,
            )
        ]

    @staticmethod
    def _aggregate_stat_rows(
        subject: Any,
        game_rows: list[tuple[Game, StatLine]],
        *,
        scope: StatScope,
        season: SeasonLabel | None = None,
        series_id: str | None = None,
    ) -> StatLine:
        metrics: dict[str, int | float | None] = {}
        definitions: dict[str, str] = {}
        for _, row in game_rows:
            definitions.update(row.metric_definitions)
            for name, value in row.metrics.items():
                if name not in metrics:
                    metrics[name] = value
                elif value is None or metrics[name] is None:
                    # Preserve missingness rather than silently treating it as
                    # zero.  A later valid row cannot prove the missing row's
                    # value, so the aggregate remains unknown for that metric.
                    metrics[name] = None
                else:
                    metrics[name] = metrics[name] + value
        metrics["games"] = len(game_rows)
        definitions.setdefault("games", "计入场次")
        return StatLine(
            subject=subject,
            series_id=series_id,
            season=season,
            scope=scope,
            metrics=metrics,
            metric_definitions=definitions,
            evidence_ids=list(
                dict.fromkeys(eid for _, row in game_rows for eid in row.evidence_ids)
            ),
        )

    async def get_standings(
        self, season_value: SeasonLabel, budget: RequestBudget
    ) -> ProviderResult[list[Standing]]:
        early = self._begin("get_standings", budget)
        if early is not None:
            return early
        values = [item for item in self._standings if item.season == season_value]
        return self._result(values, evidence_ids=[f"fixture:standings:{season_value.label}"])

    async def get_history(
        self, query: HistoryQuery, budget: RequestBudget
    ) -> ProviderResult[list[HistoryRecord]]:
        early = self._begin("get_history", budget)
        if early is not None:
            return early
        values = [item for item in self._history if item.record_type == query.record_type]
        if query.subject_refs:
            ids = {ref.canonical_id for ref in query.subject_refs}
            values = [item for item in values if item.subject and item.subject.canonical_id in ids]
        if query.season_range:
            values = [
                item
                for item in values
                if item.season is None
                or query.season_range.start_inclusive.start_year
                <= item.season.start_year
                <= query.season_range.end_inclusive.start_year
            ]
        # Latest-history queries rely on deterministic recency rather than
        # fixture file ordering.  Records without a season (for example a
        # franchise total) remain stable after dated records.
        if query.record_type is not None:
            values.sort(
                key=lambda item: item.season.start_year if item.season is not None else -1,
                reverse=True,
            )
        return self._result(
            values[: query.limit], evidence_ids=[item.evidence_id for item in values[: query.limit]]
        )

    async def search_news(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        early = self._begin("search_news", budget)
        if early is not None:
            return early
        values = list(self._news)
        if query.subject_refs:
            subject_ids = {ref.canonical_id for ref in query.subject_refs}
            # A news item matches when at least one canonical subject overlaps
            # the typed query.  Articles without subjects are not returned for
            # a scoped request, preventing a broad article from leaking into a
            # team/player answer.
            values = [
                item
                for item in values
                if any(ref.canonical_id in subject_ids for ref in item.subject_refs)
            ]
        if query.date_range:
            values = [
                item
                for item in values
                if item.published_utc is None
                or query.date_range.start_inclusive
                <= item.published_utc
                < query.date_range.end_exclusive
            ]
        if query.keywords:
            keywords = [keyword.casefold() for keyword in query.keywords if keyword.strip()]
            if keywords:

                def searchable(item: NewsItem) -> str:
                    return " ".join(
                        part for part in (item.title, item.summary or "") if part
                    ).casefold()

                # Search semantics are OR across terms, matching common news
                # search behavior while retaining a deterministic result set.
                values = [
                    item
                    for item in values
                    if any(keyword in searchable(item) for keyword in keywords)
                ]

        # Newest articles first; undated rows are retained but sorted after
        # dated rows so they cannot displace fresh headlines unexpectedly.
        values.sort(
            key=lambda item: item.published_utc or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        values = values[: query.limit]
        evidence_ids = list(dict.fromkeys(item.evidence_id for item in values))
        return self._result(
            values,
            evidence_ids=evidence_ids or ["fixture:news:none"],
            partial=self._news_partial,
        )


__all__ = ["FixtureProvider", "DEFAULT_FIXTURE_DIR"]
