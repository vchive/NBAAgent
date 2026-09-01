from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.config import Settings
from apps.api.src.domain.time_policy import FixedClock
from apps.api.src.infrastructure.cache import InMemoryTTLCache
from apps.api.src.main import create_app
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway


def _app(path: str, scenario: str = ""):
    settings = Settings(
        public_data_mode="fixture",
        highlights_demo_date="2026-06-12",
        highlights_cache_enabled=True,
        highlights_cache_db=path,
    )
    provider = FixtureProvider(scenario=scenario)
    clock = FixedClock(datetime(2026, 8, 31, 12, tzinfo=UTC))
    usecase = ChatUseCase(
        provider,
        gateway=ProviderGateway(provider, max_retries=0),
        clock=clock,
        settings=settings,
    )
    return create_app(settings=settings, usecase=usecase), provider


@pytest.mark.asyncio
async def test_recent_and_detail_survive_application_restart(tmp_path) -> None:
    path = str(tmp_path / "highlights.sqlite3")
    first, first_provider = _app(path)
    recent_url = "/api/v1/highlights/recent?limit=5&timezone=Asia/Shanghai"
    detail_url = "/api/v1/highlights/2026-finals-g4/detail?timezone=Asia/Shanghai"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first), base_url="http://first"
    ) as client:
        recent = await client.get(recent_url)
        detail = await client.get(detail_url)
    assert recent.status_code == detail.status_code == 200
    assert first_provider.calls > 0
    first.state.highlights_cache.close()

    second, unavailable_provider = _app(path, scenario="timeout")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=second), base_url="http://second"
    ) as client:
        cached_recent = await client.get(recent_url)
        cached_detail = await client.get(detail_url)

    assert cached_recent.status_code == cached_detail.status_code == 200
    assert cached_recent.json() == recent.json()
    assert cached_detail.json() == detail.json()
    assert unavailable_provider.calls == 0


@pytest.mark.asyncio
async def test_unavailable_persistent_store_falls_back_to_provider() -> None:
    app, provider = _app("/proc/nba-agent/highlights.sqlite3")
    app.state.chat_use_case.gateway.cache = InMemoryTTLCache()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/highlights/recent?limit=5&timezone=Asia/Shanghai"
        )
        health = await client.get("/healthz")
        ready = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["games"]
    assert provider.calls > 0
    assert app.state.highlights_cache.available is False
    assert health.json()["dependencies"]["highlights_cache"]["status"] == "degraded"
    assert ready.status_code == 200
    assert "/proc/nba-agent" not in health.text
