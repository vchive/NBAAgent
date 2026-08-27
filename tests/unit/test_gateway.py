from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.api.src.application.ports import ProviderResult, RequestBudget
from apps.api.src.domain.errors import ProviderError, ProviderErrorKind
from apps.api.src.domain.models import GameFilters
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
