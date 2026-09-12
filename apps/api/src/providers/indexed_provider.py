"""ProviderPort wrapper that reuses the persistent structured/BM25 index."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from apps.api.src.application.parser import resolve_entities
from apps.api.src.application.ports import (
    ProviderResult,
    RequestBudget,
    merge_capability_issues,
)
from apps.api.src.domain.models import (
    EntityKind,
    Game,
    GameBundle,
    GameFilters,
    GameStatus,
    HistoryQuery,
    NewsItem,
    NewsQuery,
    PlayByPlayBundle,
    SeasonLabel,
    SourceClass,
    Standing,
    StatLine,
    StatsQuery,
)
from apps.api.src.infrastructure.game_index import GameIndex, IndexHit

_HISTORICAL_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)|\d{4}[-/]\d{2,4}")


def _provider_result(hit: IndexHit[Any]) -> ProviderResult[Any]:
    return ProviderResult(
        data=hit.data,
        evidence=hit.evidence,
        partial=hit.partial,
        retrieved_at_utc=hit.retrieved_at_utc,
    )


def _dedupe_evidence(values):
    seen: set[str] = set()
    result = []
    for value in values:
        if value.evidence_id in seen:
            continue
        seen.add(value.evidence_id)
        result.append(value)
    return result


class IndexedProvider:
    """Read-through/write-through public index around an existing provider."""

    def __init__(
        self,
        primary: Any,
        index: GameIndex,
        *,
        detail_provider: Any | None = None,
    ) -> None:
        self.primary = primary
        self.index = index
        self.detail_provider = detail_provider
        self.calls = 0
        self.max_date_slices = getattr(primary, "max_date_slices", None)

    def search_availability(self) -> dict[str, str | None]:
        method = getattr(self.primary, "search_availability", None)
        return (
            method()
            if callable(method)
            else {"status": "enabled_unverified", "error_kind": None}
        )

    @staticmethod
    def _structured_evidence(evidence) -> list[Any]:
        return [
            item
            for item in evidence
            if item.source_class in {SourceClass.OFFICIAL, SourceClass.ESTABLISHED_SPORTS}
        ]

    def _store_games(self, result: ProviderResult[list[Game]]) -> None:
        if result.error is not None or not result.data or result.used_fallback:
            return
        evidence = self._structured_evidence(result.evidence)
        if not evidence:
            return
        for game in result.data:
            if isinstance(game, Game):
                self.index.upsert_bundle(
                    GameBundle(game=game),
                    evidence,
                    origin="public",
                    retrieved_at_utc=result.retrieved_at_utc,
                )

    def _store_summary(self, result: ProviderResult[GameBundle]) -> None:
        if (
            result.error is not None
            or not isinstance(result.data, GameBundle)
            or result.used_fallback
        ):
            return
        evidence = self._structured_evidence(result.evidence)
        if evidence:
            self.index.upsert_bundle(
                result.data,
                evidence,
                origin="public",
                retrieved_at_utc=result.retrieved_at_utc,
            )

    def search_cached_games(self, filters: GameFilters) -> ProviderResult[list[Game]]:
        """Read the complete requested window without invoking the live source.

        Date-driven navigation can span many upstream request slices.  This
        explicit local capability lets callers satisfy a recent-games request
        before spending its deadline on empty off-season slices.
        """

        return _provider_result(self.index.search_games(filters))

    def _store_play_by_play(
        self,
        game_id: str,
        result: ProviderResult[PlayByPlayBundle],
    ) -> IndexHit[PlayByPlayBundle] | None:
        if (
            result.error is not None
            or not isinstance(result.data, PlayByPlayBundle)
            or not result.data.events
            or result.used_fallback
        ):
            return None
        summary = self.index.get_game_summary(game_id)
        if summary is None:
            return None
        evidence = self._structured_evidence(result.evidence) or self._structured_evidence(
            summary.evidence
        )
        if not evidence:
            return None
        self.index.upsert_bundle(
            GameBundle(
                game=summary.data.game,
                stat_lines=summary.data.stat_lines,
                leaders=summary.data.leaders,
                plays=result.data,
            ),
            evidence,
            origin="public",
            retrieved_at_utc=result.retrieved_at_utc,
        )
        return self.index.get_play_by_play(game_id)

    async def search_games(
        self, filters: GameFilters, budget: RequestBudget
    ) -> ProviderResult[list[Game]]:
        local = self.index.search_games(filters)
        if local.data:
            return _provider_result(local)
        self.calls += 1
        result = await self.primary.search_games(filters, budget)
        if isinstance(result, ProviderResult):
            self._store_games(result)
        return result

    @staticmethod
    def _summary_complete(bundle: GameBundle) -> bool:
        game = bundle.game
        if game.status is not GameStatus.FINAL:
            return True
        # A final schedule row often carries one incidental detail (for
        # example venue) while still lacking the box score and event feed.
        # Treat only a player box score or PBP bundle as detail-complete so a
        # later venue/coach/stat question still gets a chance to enrich it
        # from the public detail source.
        return bool(
            bundle.stat_lines
            or bundle.plays is not None
        )

    async def get_game_summary(
        self, game_id: str, budget: RequestBudget
    ) -> ProviderResult[GameBundle]:
        local = self.index.get_game_summary(game_id)
        if local is not None and self._summary_complete(local.data):
            return _provider_result(local)
        if (
            str(game_id).startswith("hupu:")
            and self.detail_provider is not None
            and budget.remaining_ms() > 0
        ):
            self.calls += 1
            detail = await self.detail_provider.get_game_summary(game_id, budget)
            if isinstance(detail, ProviderResult) and detail.error is None:
                self._store_summary(detail)
                refreshed = self.index.get_game_summary(game_id)
                return _provider_result(refreshed) if refreshed is not None else detail
            if local is not None:
                return ProviderResult(
                    data=local.data,
                    evidence=local.evidence,
                    partial=True,
                    retrieved_at_utc=local.retrieved_at_utc,
                )
        # A schedule row can contain only score/time.  Keep going to the
        # primary public detail source for every other game id instead of
        # treating that shallow row as a complete summary.  Returning the
        # shallow row here used to make venue, coaches, box score and PBP
        # permanently unavailable for indexed ESPN games.
        if local is not None:
            self.calls += 1
            result = await self.primary.get_game_summary(game_id, budget)
            if isinstance(result, ProviderResult) and result.error is None:
                self._store_summary(result)
                refreshed = self.index.get_game_summary(game_id)
                return _provider_result(refreshed) if refreshed is not None else result
            return _provider_result(local)
        self.calls += 1
        result = await self.primary.get_game_summary(game_id, budget)
        if isinstance(result, ProviderResult):
            self._store_summary(result)
        return result

    async def get_play_by_play(
        self, game_id: str, budget: RequestBudget
    ) -> ProviderResult[PlayByPlayBundle]:
        local = self.index.get_play_by_play(game_id)
        if local is not None:
            return _provider_result(local)
        self.calls += 1
        source = self.primary
        if (
            str(game_id).startswith("hupu:")
            and self.detail_provider is not None
            and callable(getattr(self.detail_provider, "get_play_by_play", None))
        ):
            # Hupu and ESPN identifiers are not interchangeable.  The old
            # route sent every Hupu id to the primary ESPN adapter, which
            # guaranteed an on-demand PBP miss.
            source = self.detail_provider
        result = await source.get_play_by_play(game_id, budget)
        if isinstance(result, ProviderResult):
            refreshed = self._store_play_by_play(game_id, result)
            if refreshed is not None:
                return _provider_result(refreshed)
        return result

    async def get_player_stats(
        self, query: StatsQuery, budget: RequestBudget
    ) -> ProviderResult[list[StatLine]]:
        if query.game_id:
            local = self.index.get_game_summary(query.game_id)
            if local is not None:
                rows = [
                    line
                    for line in local.data.stat_lines
                    if line.subject.canonical_id == query.subject.canonical_id
                ]
                if rows:
                    return _provider_result(
                        IndexHit(rows, local.evidence, local.retrieved_at_utc, local.partial)
                    )
        self.calls += 1
        return await self.primary.get_player_stats(query, budget)

    async def get_team_stats(
        self, query: StatsQuery, budget: RequestBudget
    ) -> ProviderResult[list[StatLine]]:
        self.calls += 1
        return await self.primary.get_team_stats(query, budget)

    async def get_standings(
        self, season: SeasonLabel, budget: RequestBudget, *, conference: str | None = None
    ) -> ProviderResult[list[Standing]]:
        self.calls += 1
        if conference is None:
            return await self.primary.get_standings(season, budget)
        try:
            return await self.primary.get_standings(season, budget, conference=conference)
        except TypeError:
            return await self.primary.get_standings(season, budget)

    async def get_history(
        self, query: HistoryQuery, budget: RequestBudget
    ) -> ProviderResult[Any]:
        self.calls += 1
        return await self.primary.get_history(query, budget)

    @staticmethod
    def _effective_news_query(query: NewsQuery) -> NewsQuery:
        if query.subject_refs:
            return query
        text = " ".join(query.keywords)
        refs = [
            item
            for item in resolve_entities(text)
            if item.kind in {EntityKind.TEAM, EntityKind.PLAYER, EntityKind.GAME}
        ]
        return query.model_copy(update={"subject_refs": refs}) if refs else query

    def _store_documents(self, query: NewsQuery, result: ProviderResult[list[NewsItem]]) -> None:
        if result.error is not None or not result.data or result.used_fallback:
            return
        evidence_by_id = {item.evidence_id: item for item in result.evidence}
        for item in result.data:
            if not isinstance(item, NewsItem):
                continue
            detected_refs = [
                value
                for value in resolve_entities(f"{item.title} {item.summary or ''}")
                if value.kind in {EntityKind.TEAM, EntityKind.PLAYER, EntityKind.GAME}
            ]
            refs_by_id = {
                value.canonical_id: value
                for value in [*item.subject_refs, *detected_refs]
            }
            refs = list(refs_by_id.values()) or list(query.subject_refs)
            normalized = item.model_copy(update={"subject_refs": refs})
            evidence = evidence_by_id.get(item.evidence_id)
            if evidence is not None and evidence.source_class is not SourceClass.FIXTURE:
                self.index.upsert_document(normalized, evidence, origin="public")

    async def search_news(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        effective = self._effective_news_query(query)
        local = self.index.search_documents(effective)
        historical = bool(_HISTORICAL_RE.search(" ".join(query.keywords)))
        if local.data and historical:
            return _provider_result(local)
        self.calls += 1
        remote = await self.primary.search_news(effective, budget)
        if isinstance(remote, ProviderResult):
            self._store_documents(effective, remote)
        if not local.data:
            return remote
        if not isinstance(remote, ProviderResult):
            return _provider_result(local)
        if remote.error is not None:
            local_result = _provider_result(local)
            return local_result.model_copy(
                update={
                    "capability_issues": merge_capability_issues(
                        remote.capability_issues,
                        [remote.error],
                    )
                }
            )
        merged: list[NewsItem] = []
        seen: set[str] = set()
        for item in [*local.data, *(remote.data or [])]:
            key = item.news_id or item.title.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= effective.limit:
                break
        return ProviderResult(
            data=merged,
            evidence=_dedupe_evidence([*local.evidence, *remote.evidence]),
            partial=True,
            capability_issues=remote.capability_issues,
            retrieved_at_utc=max(local.retrieved_at_utc, remote.retrieved_at_utc),
        )

    async def search_web(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        """Retrieve narrative evidence through SQLite BM25 and web search.

        ``nba_search`` has its own provider operation so a pure web lookup does
        not first call the structured ESPN news endpoint.  Historical hits can
        be served entirely from the persistent index; otherwise the configured
        web adapter is queried and its bounded partial documents are written
        back for subsequent turns/restarts.
        """

        effective = self._effective_news_query(query)
        local = self.index.search_documents(effective)
        historical = bool(_HISTORICAL_RE.search(" ".join(query.keywords)))
        if local.data and historical:
            return _provider_result(local)

        self.calls += 1
        method = getattr(self.primary, "search_web", None)
        if not callable(method):
            # Compatibility for a legacy injected primary.  This fallback is
            # still below the indexed wrapper and is never the structured
            # ESPN-first path used by the production composition.
            method = getattr(self.primary, "search_news", None)
        if not callable(method):
            remote = ProviderResult(
                data=[],
                evidence=[],
                partial=True,
                retrieved_at_utc=datetime.now(UTC),
            )
        else:
            remote = await method(effective, budget)
        if isinstance(remote, ProviderResult):
            self._store_documents(effective, remote)
        if not local.data:
            return remote
        if not isinstance(remote, ProviderResult):
            return _provider_result(local)
        if remote.error is not None:
            local_result = _provider_result(local)
            return local_result.model_copy(
                update={
                    "capability_issues": merge_capability_issues(
                        remote.capability_issues,
                        [remote.error],
                    )
                }
            )
        merged: list[NewsItem] = []
        seen: set[str] = set()
        for item in [*local.data, *(remote.data or [])]:
            key = item.news_id or item.title.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= effective.limit:
                break
        return ProviderResult(
            data=merged,
            evidence=_dedupe_evidence([*local.evidence, *remote.evidence]),
            partial=bool(local.partial or remote.partial or remote.data),
            capability_issues=remote.capability_issues,
            retrieved_at_utc=max(local.retrieved_at_utc, remote.retrieved_at_utc),
        )


__all__ = ["IndexedProvider"]
