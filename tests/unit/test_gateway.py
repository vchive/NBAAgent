from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.api.src.application.ports import ProviderResult, RequestBudget
from apps.api.src.domain.errors import ProviderError, ProviderErrorKind
from apps.api.src.domain.models import GameFilters, HistoryQuery, HistoryRecordType
from apps.api.src.infrastructure.cache import InMemoryTTLCache
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway


class _Provider:
    def __init__(self, *, error: bool = False, value: str = "") -> None:
        self.error = error
        self.value = value
        self.calls = 0

    async def search_games(self, _filters, *, budget):
        self.calls += 1
        if self.error:
            return ProviderResult(
                data=None,
                evidence=[],
                error=ProviderError(
                    kind=ProviderErrorKind.TIMEOUT,
                    retryable=False,
                    safe_message="upstream timed out",
                ),
                retrieved_at_utc=datetime.now(UTC),
            )
        return ProviderResult(data=[self.value], evidence=[], retrieved_at_utc=datetime.now(UTC))


@pytest.mark.asyncio
async def test_gateway_uses_fallback_only_after_primary_error() -> None:
    primary = _Provider(error=True)
    fallback = _Provider(value="fixture")
    gateway = ProviderGateway(primary, fallback_provider=fallback, max_retries=0)
    result = await gateway.search_games(
        GameFilters(),
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=2)),
    )
    assert result.error is None
    assert result.data == ["fixture"]
    assert primary.calls == 1
    assert fallback.calls == 1
    assert gateway.counters()["provider_call_count"] == 2


@pytest.mark.asyncio
async def test_gateway_preserves_primary_quota_issue_when_fallback_succeeds() -> None:
    class QuotaProvider:
        async def search_games(self, _filters, *, budget):
            return ProviderResult(
                data=None,
                evidence=[],
                error=ProviderError(
                    kind=ProviderErrorKind.QUOTA_EXHAUSTED,
                    retryable=False,
                    safe_message="quota exhausted",
                ),
                retrieved_at_utc=datetime.now(UTC),
            )

    gateway = ProviderGateway(
        QuotaProvider(),
        fallback_provider=_Provider(value="fixture"),
        max_retries=0,
    )
    result = await gateway.search_games(
        GameFilters(),
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=2)),
    )

    assert result.error is None
    assert result.data == ["fixture"]
    assert result.capability_issues[0].kind is ProviderErrorKind.QUOTA_EXHAUSTED


@pytest.mark.asyncio
async def test_gateway_does_not_cache_request_scoped_capability_issue() -> None:
    class RecoveredProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def search_games(self, _filters, *, budget):
            self.calls += 1
            return ProviderResult(
                data=["public"],
                evidence=[],
                capability_issues=[
                    ProviderError(
                        kind=ProviderErrorKind.QUOTA_EXHAUSTED,
                        retryable=False,
                        safe_message="quota exhausted",
                    )
                ],
                retrieved_at_utc=datetime.now(UTC),
            )

    provider = RecoveredProvider()
    gateway = ProviderGateway(provider, cache=InMemoryTTLCache(), max_retries=0)
    first = await gateway.search_games(
        GameFilters(),
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=2)),
    )
    cached = await gateway.search_games(
        GameFilters(),
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=2)),
    )

    assert first.capability_issues
    assert cached.data == ["public"]
    assert cached.capability_issues == []
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_gateway_does_not_replace_authoritative_empty_result() -> None:
    primary = _Provider(value="")
    fallback = _Provider(value="fixture")

    # Make the primary return an empty list, not an error.
    async def empty(_filters, *, budget):
        primary.calls += 1
        return ProviderResult(data=[], evidence=[], retrieved_at_utc=datetime.now(UTC))

    primary.search_games = empty
    gateway = ProviderGateway(primary, fallback_provider=fallback, max_retries=0)
    result = await gateway.search_games(
        GameFilters(),
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=2)),
    )
    assert result.error is None
    assert result.data == []
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_explicit_public_refresh_bypasses_cache_and_disables_fallback() -> None:
    """A user-requested public recheck must touch only the primary source."""

    primary = _Provider(value="first-public")
    fallback = _Provider(value="fixture")
    gateway = ProviderGateway(
        primary,
        fallback_provider=fallback,
        cache=InMemoryTTLCache(),
        max_retries=0,
    )
    filters = GameFilters()

    first = await gateway.search_games(
        filters,
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=2)),
    )
    primary.value = "fresh-public"
    refreshed = await gateway.search_games(
        filters,
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=2)),
        force_refresh=True,
        allow_fallback=False,
    )

    assert first.data == ["first-public"]
    assert refreshed.data == ["fresh-public"]
    assert primary.calls == 2
    assert fallback.calls == 0

    primary.error = True
    failed = await gateway.search_games(
        GameFilters(season=None),
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=2)),
        force_refresh=True,
        allow_fallback=False,
    )
    assert failed.error is not None
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_history_empty_cache_does_not_disable_an_explicit_snapshot_fallback() -> None:
    """Fallback policy is part of the cache key, even though it is not a provider argument."""

    primary = FixtureProvider(scenario="empty")
    fallback = FixtureProvider()
    gateway = ProviderGateway(
        primary,
        fallback=fallback,
        cache=InMemoryTTLCache(),
        max_retries=0,
    )
    query = HistoryQuery(record_type=HistoryRecordType.CHAMPIONSHIP, limit=1)

    authoritative_empty = await gateway.get_history(
        query,
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=2)),
    )
    recovered = await gateway.get_history(
        query,
        budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=2)),
        fallback_on_empty=True,
    )

    assert authoritative_empty.data == []
    assert recovered.error is None
    assert recovered.partial is True
    assert recovered.data and recovered.data[0].subject is not None
    assert recovered.data[0].subject.display_name == "凯尔特人"
    assert fallback.operation_calls["get_history"] == 1
