from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from apps.api.src.api.schemas import HighlightDetailResponse, HighlightGame
from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.application.highlights import HighlightsService
from apps.api.src.config import Settings
from apps.api.src.main import create_app
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway


@dataclass
class _MutableClock:
    value: datetime

    def now_utc(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


def _settings(path: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "public_data_mode": "fixture",
        "highlights_demo_date": "2026-06-12",
        "highlights_cache_enabled": True,
        "highlights_cache_db": path,
        "highlights_cache_live_ttl_seconds": 1,
        "highlights_cache_recent_ttl_seconds": 1,
        "highlights_cache_history_ttl_seconds": 1,
        "highlights_cache_detail_ttl_seconds": 1,
    }
    values.update(overrides)
    return Settings(**values)


def _app(path: str, *, provider: FixtureProvider, clock: _MutableClock, **settings: object):
    config = _settings(path, **settings)
    gateway = ProviderGateway(provider, max_retries=0)
    usecase = ChatUseCase(provider, gateway=gateway, clock=clock, settings=config)
    return create_app(settings=config, usecase=usecase)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "operation"),
    [
        ("/api/v1/highlights/recent?limit=5&timezone=Asia/Shanghai", "search_games"),
        (
            "/api/v1/highlights/range?from=2026-06-06&to=2026-06-12&timezone=Asia/Shanghai",
            "search_games",
        ),
        ("/api/v1/highlights?date=2026-06-10&timezone=Asia/Shanghai", "search_games"),
    ],
)
async def test_historical_endpoints_write_then_hit_without_provider(
    tmp_path, endpoint: str, operation: str
) -> None:
    provider = FixtureProvider()
    clock = _MutableClock(datetime(2026, 8, 31, 12, tzinfo=UTC))
    app = _app(str(tmp_path / "highlights.sqlite3"), provider=provider, clock=clock)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(endpoint)
        calls_after_first = provider.operation_calls.get(operation, 0)
        second = await client.get(endpoint)

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert calls_after_first > 0
    assert provider.operation_calls.get(operation, 0) == calls_after_first
    assert app.state.highlights_cache.counters()["persistent_cache_hit_count"] >= 1


@pytest.mark.asyncio
async def test_stale_historical_result_is_served_when_background_refresh_fails(tmp_path) -> None:
    provider = FixtureProvider()
    clock = _MutableClock(datetime(2026, 8, 31, 12, tzinfo=UTC))
    app = _app(str(tmp_path / "highlights.sqlite3"), provider=provider, clock=clock)
    endpoint = "/api/v1/highlights/recent?limit=5&timezone=Asia/Shanghai"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(endpoint)
        clock.advance(seconds=2)
        provider.scenario = "timeout"
        stale = await client.get(endpoint)

    assert first.status_code == stale.status_code == 200
    assert stale.json()["games"] == first.json()["games"]
    assert app.state.highlights_cache.counters()["persistent_cache_stale_hit_count"] == 1


@pytest.mark.asyncio
async def test_expired_current_day_result_is_not_served_as_live_fact(tmp_path) -> None:
    provider = FixtureProvider()
    clock = _MutableClock(datetime(2026, 6, 12, 4, tzinfo=UTC))
    app = _app(
        str(tmp_path / "highlights.sqlite3"),
        provider=provider,
        clock=clock,
        public_data_mode="hybrid",
        highlights_demo_date="",
    )
    endpoint = "/api/v1/highlights?timezone=Asia/Shanghai"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(endpoint)
        clock.advance(seconds=2)
        provider.scenario = "timeout"
        expired = await client.get(endpoint)

    assert first.status_code == 200
    assert expired.status_code == 504
    assert expired.json()["error"]["code"] == "UPSTREAM_TIMEOUT"


@pytest.mark.asyncio
async def test_detail_is_cached_after_first_read(tmp_path) -> None:
    provider = FixtureProvider()
    clock = _MutableClock(datetime(2026, 8, 31, 12, tzinfo=UTC))
    app = _app(str(tmp_path / "highlights.sqlite3"), provider=provider, clock=clock)
    endpoint = "/api/v1/highlights/2026-finals-g4/detail?timezone=Asia/Shanghai"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(endpoint)
        calls = provider.operation_calls.get("get_game_summary", 0)
        second = await client.get(endpoint)

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert calls == 1
    assert provider.operation_calls.get("get_game_summary", 0) == calls


@pytest.mark.asyncio
async def test_recent_prefetches_at_most_five_completed_game_details(tmp_path) -> None:
    provider = FixtureProvider()
    clock = _MutableClock(datetime(2026, 8, 31, 12, tzinfo=UTC))
    app = _app(str(tmp_path / "highlights.sqlite3"), provider=provider, clock=clock)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/highlights/recent?limit=20&timezone=Asia/Shanghai"
        )
        while tasks := list(getattr(app.state, "highlights_refresh_tasks", set())):
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0)

    assert response.status_code == 200
    assert len(response.json()["games"]) > 5
    assert provider.operation_calls.get("get_game_summary", 0) == 5
    assert app.state.highlights_cache.count() == 6  # recent projection + five details


@pytest.mark.asyncio
async def test_cached_recent_games_are_rebound_to_selected_game_registry(tmp_path) -> None:
    provider = FixtureProvider()
    clock = _MutableClock(datetime(2026, 8, 31, 12, tzinfo=UTC))
    app = _app(str(tmp_path / "highlights.sqlite3"), provider=provider, clock=clock)
    endpoint = "/api/v1/highlights/recent?limit=5&timezone=Asia/Shanghai"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(endpoint)
        game_id = first.json()["games"][0]["game_id"]
        app.state.game_registry.clear()
        second = await client.get(endpoint)

    assert second.status_code == 200
    assert game_id in app.state.game_registry
    assert app.state.game_registry[game_id].game_id == game_id


@pytest.mark.asyncio
async def test_simultaneous_recent_cache_misses_share_one_provider_load(tmp_path) -> None:
    provider = FixtureProvider()
    clock = _MutableClock(datetime(2026, 8, 31, 12, tzinfo=UTC))
    app = _app(str(tmp_path / "highlights.sqlite3"), provider=provider, clock=clock)
    endpoint = "/api/v1/highlights/recent?limit=5&timezone=Asia/Shanghai"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first, second = await asyncio.gather(client.get(endpoint), client.get(endpoint))

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert provider.operation_calls.get("search_games", 0) == 1


@pytest.mark.asyncio
async def test_live_detail_uses_short_ttl_and_is_reverified(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FixtureProvider()
    clock = _MutableClock(datetime(2026, 8, 31, 12, tzinfo=UTC))
    app = _app(
        str(tmp_path / "highlights.sqlite3"),
        provider=provider,
        clock=clock,
        highlights_cache_live_ttl_seconds=1,
        highlights_cache_detail_ttl_seconds=60,
    )
    calls = 0

    async def live_detail(
        self: HighlightsService, game_id: str, *, timezone_name: str
    ) -> HighlightDetailResponse:
        nonlocal calls
        calls += 1
        return HighlightDetailResponse(
            game=HighlightGame(
                game_id=game_id,
                start_utc=datetime(2026, 8, 31, 12, tzinfo=UTC),
                home_name="凯尔特人",
                home_abbreviation="BOS",
                away_name="雷霆",
                away_abbreviation="OKC",
                status="live",
                home_score=88 + calls,
                away_score=87,
            ),
            leaders=[],
            plays=[],
            as_of_beijing="2026-08-31 20:00",
            evidence_state="partial",
        )

    monkeypatch.setattr(HighlightsService, "detail", live_detail)
    endpoint = "/api/v1/highlights/live-g1/detail?timezone=Asia/Shanghai"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get(endpoint)
        clock.advance(seconds=2)
        second = await client.get(endpoint)

    assert first.status_code == second.status_code == 200
    assert first.json()["game"]["home_score"] == 89
    assert second.json()["game"]["home_score"] == 90
    assert calls == 2
