from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.src import main as main_module
from apps.api.src.application.ports import ProviderResult, RequestBudget
from apps.api.src.config import Settings
from apps.api.src.domain.models import NewsQuery, SeasonLabel
from apps.api.src.providers.espn_adapter import ESPNAdapter
from apps.api.src.providers.search_augmented_provider import SearchAugmentedProvider


def _budget() -> RequestBudget:
    return RequestBudget(datetime.now(UTC) + timedelta(seconds=3), max_provider_operations=4)


@pytest.mark.parametrize("mode", ["live", "hybrid"])
def test_public_composition_never_constructs_fixture_provider(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    class ForbiddenFixtureProvider:
        def __init__(self) -> None:
            raise AssertionError("public composition must not construct fixture data")

    monkeypatch.setattr(main_module, "FixtureProvider", ForbiddenFixtureProvider)

    provider, fallback = main_module._provider_stack(Settings(public_data_mode=mode))

    assert isinstance(provider, ESPNAdapter)
    assert fallback is None


def test_search_wrapper_preserves_primary_calendar_slice_limit() -> None:
    class Primary:
        max_date_slices = 7

    wrapped = SearchAugmentedProvider(Primary())
    assert wrapped.max_date_slices == 7


def test_search_wrapper_forwards_conference_scope() -> None:
    async def run() -> None:
        class Primary:
            async def get_standings(self, season, budget, *, conference=None):
                self.season = season
                self.conference = conference
                return ProviderResult(data=[], evidence=[], retrieved_at_utc=datetime.now(UTC))

        primary = Primary()
        wrapped = SearchAugmentedProvider(primary)
        season = SeasonLabel(start_year=2025, end_year=2026, label="2025-26")
        await wrapped.get_standings(season, _budget(), conference="West")
        assert primary.season == season
        assert primary.conference == "West"

    asyncio.run(run())


def test_search_wrapper_accepts_gateway_empty_fallback_keyword() -> None:
    async def run() -> None:
        class Primary:
            async def search_news(self, query, budget):
                return ProviderResult(data=[], evidence=[], retrieved_at_utc=datetime.now(UTC))

        wrapped = SearchAugmentedProvider(Primary())
        result = await wrapped.search_news(NewsQuery(), _budget(), fallback_on_empty=True)
        assert result.error is None
        assert result.data == []

    asyncio.run(run())


def test_search_web_does_not_call_structured_primary_news() -> None:
    async def run() -> None:
        class Primary:
            def __init__(self) -> None:
                self.news_calls = 0

            async def search_news(self, query, budget):
                self.news_calls += 1
                return ProviderResult(
                    data=[], evidence=[], retrieved_at_utc=datetime.now(UTC)
                )

        class Search:
            def __init__(self) -> None:
                self.web_calls = 0

            async def search_web(self, query, budget):
                self.web_calls += 1
                return ProviderResult(
                    data=[], evidence=[], partial=True, retrieved_at_utc=datetime.now(UTC)
                )

        primary = Primary()
        search = Search()
        wrapped = SearchAugmentedProvider(primary, search)
        result = await wrapped.search_web(NewsQuery(keywords=["2026尼克斯 马刺"]), _budget())
        assert result.error is None
        assert search.web_calls == 1
        assert primary.news_calls == 0

    asyncio.run(run())
