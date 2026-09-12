"""Provider composition for optional, controlled web-search augmentation."""

from __future__ import annotations

from datetime import UTC, datetime

from apps.api.src.application.ports import (
    ProviderResult,
    RequestBudget,
    merge_capability_issues,
)
from apps.api.src.domain.errors import ProviderError, ProviderErrorKind
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
    """Delegate NBA facts and optionally merge public-search candidates.

    The wrapper exposes the normal ProviderPort surface so the existing
    ProviderGateway/cache/retry boundary remains unchanged. Structured news
    may be augmented by the configured search adapter through ``search_news``;
    pure web lookups use ``search_web`` and bypass the primary sports source.
    """

    def __init__(self, primary, search_provider=None) -> None:
        self.primary = primary
        self.search_provider = search_provider
        self.calls = 0
        # Preserve adapter capabilities used by date-scoped projections.  The
        # ESPN adapter limits one request to a bounded number of provider
        # calendar slices; hiding that limit here makes the highlights
        # availability service send an over-wide Beijing interval and turn a
        # known empty day into ``unknown``.
        self.max_date_slices = getattr(primary, "max_date_slices", None)

    def search_availability(self) -> dict[str, str | None]:
        """Return a provider-neutral, passive search diagnostic snapshot."""

        snapshot = getattr(self.search_provider, "availability", None)
        if not callable(snapshot):
            return {"status": "enabled_unverified", "error_kind": None}
        try:
            value = snapshot()
        except Exception:
            return {"status": "degraded", "error_kind": None}
        if not isinstance(value, dict):
            return {"status": "degraded", "error_kind": None}
        raw = str(value.get("status") or "").lower()
        status = {
            "unknown": "enabled_unverified",
            "ok": "ok",
            "degraded": "degraded",
        }.get(raw, "degraded")
        error_kind = value.get("error_kind")
        return {
            "status": status,
            "error_kind": str(error_kind) if error_kind else None,
        }

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
        # Keep optional conference scoping intact when the wrapped provider
        # advertises that keyword.  The built-in ESPN/fixture adapters retain
        # the legacy season-only signature because ProviderGateway applies the
        # typed conference projection itself; avoid passing ``None`` (or an
        # unsupported keyword) into those adapters.
        if conference is None:
            return await self.primary.get_standings(season, budget)
        try:
            return await self.primary.get_standings(season, budget, conference=conference)
        except TypeError:
            return await self.primary.get_standings(season, budget)

    async def get_history(
        self, query: HistoryQuery, budget: RequestBudget
    ) -> ProviderResult[list[HistoryRecord]]:
        return await self.primary.get_history(query, budget)

    async def search_web(
        self,
        query: NewsQuery,
        budget: RequestBudget,
    ) -> ProviderResult[list[NewsItem]]:
        """Search only the configured web-search adapter chain.

        This operation is deliberately not implemented by delegating to the
        primary sports provider.  ``nba_search`` is for long-tail/background
        material; calling the primary ``search_news`` first both wastes the
        shared operation budget and can return an unrelated headline that
        looks like a successful answer.  Adapters expose ``search_web`` as a
        compatibility alias to their bounded candidate search method.
        """

        if self.search_provider is None or budget.remaining_ms() <= 0:
            return ProviderResult(
                data=[],
                evidence=[],
                partial=True,
                retrieved_at_utc=datetime.now(UTC),
            )
        method = getattr(self.search_provider, "search_web", None)
        if not callable(method):
            # Keep injected/legacy search doubles working while retaining the
            # important invariant that the primary structured provider is not
            # touched by this operation.
            method = getattr(self.search_provider, "search_news", None)
        if not callable(method):
            return ProviderResult(
                data=[],
                evidence=[],
                partial=True,
                retrieved_at_utc=datetime.now(UTC),
            )
        try:
            result = await method(query, budget)
        except Exception:
            return ProviderResult(
                data=None,
                evidence=[],
                partial=True,
                error=ProviderError(
                    kind=ProviderErrorKind.HTTP,
                    retryable=True,
                    safe_message="search service unavailable",
                ),
                retrieved_at_utc=datetime.now(UTC),
            )
        return result if isinstance(result, ProviderResult) else ProviderResult(
            data=None,
            evidence=[],
            partial=True,
            retrieved_at_utc=datetime.now(UTC),
        )

    async def search_news(
        self,
        query: NewsQuery,
        budget: RequestBudget,
        *,
        fallback_on_empty: bool = False,
    ) -> ProviderResult[list[NewsItem]]:
        # ``fallback_on_empty`` is accepted for ProviderGateway parity.  The
        # primary/search composition remains authoritative about empty news;
        # the flag is intentionally forwarded only when the wrapped provider
        # explicitly supports it.
        try:
            primary_result = await self.primary.search_news(
                query, budget, fallback_on_empty=fallback_on_empty
            )
        except TypeError:
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
                # into a technical failure, but retain the degraded search
                # capability for this request's UI notice.
                issues = list(primary_result.capability_issues)
                if isinstance(web_result, ProviderResult):
                    issues = merge_capability_issues(
                        issues,
                        web_result.capability_issues,
                        [web_result.error],
                    )
                return primary_result.model_copy(update={"capability_issues": issues})
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
            capability_issues = merge_capability_issues(
                primary_result.capability_issues,
                web_result.capability_issues,
                [primary_result.error],
            )
            return ProviderResult(
                data=merged,
                evidence=evidence,
                # Web candidates are intentionally partial. Keep a partial
                # marker even when the primary source returned full news.
                partial=bool(primary_result.partial or web_result.partial or web_items),
                capability_issues=capability_issues,
                retrieved_at_utc=max(primary_result.retrieved_at_utc, web_result.retrieved_at_utc),
            )
        # Preserve an authoritative empty NBA/search result. No stale fixture
        # or fabricated headline is introduced when both sources are empty.
        if primary_result.error is None:
            return ProviderResult(
                data=[],
                evidence=[*primary_result.evidence, *web_result.evidence],
                partial=bool(primary_result.partial or web_result.partial),
                capability_issues=merge_capability_issues(
                    primary_result.capability_issues,
                    web_result.capability_issues,
                ),
                retrieved_at_utc=max(primary_result.retrieved_at_utc, web_result.retrieved_at_utc),
            )
        return primary_result


__all__ = ["SearchAugmentedProvider"]
