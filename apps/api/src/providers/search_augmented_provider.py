"""Provider composition for optional, controlled web-search augmentation."""

from __future__ import annotations

from apps.api.src.application.ports import ProviderResult, RequestBudget
from apps.api.src.domain.models import (
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
    StatsQuery,
)


class SearchAugmentedProvider:
    """Delegate NBA facts and optionally merge DDG candidates for news.

    The wrapper exposes the normal ProviderPort surface so the existing
    ProviderGateway/cache/retry boundary remains unchanged. DDG is called only
    for the typed ``search_news`` operation and its partial flag is preserved.
    """

    def __init__(self, primary, search_provider=None) -> None:
        self.primary = primary
        self.search_provider = search_provider
        self.calls = 0

    async def search_games(
        self, filters: GameFilters, budget: RequestBudget
    ) -> ProviderResult[list[Game]]:
        return await self.primary.search_games(filters, budget)

    async def get_game_summary(
        self, game_id: str, budget: RequestBudget
    ) -> ProviderResult[GameBundle]:
        return await self.primary.get_game_summary(game_id, budget)

    async def get_play_by_play(
        self, game_id: str, budget: RequestBudget
    ) -> ProviderResult[PlayByPlayBundle]:
        return await self.primary.get_play_by_play(game_id, budget)

    async def get_player_stats(
        self, query: StatsQuery, budget: RequestBudget
    ) -> ProviderResult[list[StatLine]]:
        return await self.primary.get_player_stats(query, budget)

    async def get_team_stats(
        self, query: StatsQuery, budget: RequestBudget
    ) -> ProviderResult[list[StatLine]]:
        return await self.primary.get_team_stats(query, budget)

    async def get_standings(
        self, season: SeasonLabel, budget: RequestBudget, *, conference: str | None = None
    ) -> ProviderResult[list[Standing]]:
        return await self.primary.get_standings(season, budget)

    async def get_history(
        self, query: HistoryQuery, budget: RequestBudget
    ) -> ProviderResult[list[HistoryRecord]]:
        return await self.primary.get_history(query, budget)

    async def search_news(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        primary_result = await self.primary.search_news(query, budget)
        if self.search_provider is None or budget.remaining_ms() <= 0:
            return primary_result
        # Search is supplementary. If the NBA source has a hard error, still
        # try DDG for a useful background answer; if it succeeds, its partial
        # evidence state is retained rather than hidden.
        try:
            web_result = await self.search_provider.search_news(query, budget)
        except Exception:
            web_result = None
        if web_result is None or web_result.error is not None:
            if primary_result.error is None:
                # A failed optional search must not turn a valid NBA answer
                # into a technical failure.
                return primary_result
            return primary_result
        primary_items = list(primary_result.data or []) if primary_result.error is None else []
        web_items = list(web_result.data or [])
        seen: set[str] = set()
        merged: list[NewsItem] = []
        for item in [*primary_items, *web_items]:
            key = item.news_id or item.title.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= query.limit:
                break
        if merged:
            evidence = [*primary_result.evidence, *web_result.evidence]
            return ProviderResult(
                data=merged,
                evidence=evidence,
                # Web candidates are intentionally partial. Keep a partial
                # marker even when the primary source returned full news.
                partial=bool(primary_result.partial or web_result.partial or web_items),
                retrieved_at_utc=max(primary_result.retrieved_at_utc, web_result.retrieved_at_utc),
            )
        # Preserve an authoritative empty NBA/search result. No stale fixture
        # or fabricated headline is introduced when both sources are empty.
        if primary_result.error is None:
            return ProviderResult(
                data=[],
                evidence=[*primary_result.evidence, *web_result.evidence],
                partial=bool(primary_result.partial or web_result.partial),
                retrieved_at_utc=max(primary_result.retrieved_at_utc, web_result.retrieved_at_utc),
            )
        return primary_result


__all__ = ["SearchAugmentedProvider"]
