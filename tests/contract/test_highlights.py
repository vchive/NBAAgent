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


@pytest.mark.asyncio
async def test_highlights_contract_valid_empty_and_future_dates() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        good = await client.get("/api/v1/highlights?date=2026-06-12&timezone=Asia/Shanghai")
        empty = await client.get("/api/v1/highlights?date=2026-06-13&timezone=Asia/Shanghai")
        future = await client.get("/api/v1/highlights?date=2999-01-01&timezone=Asia/Shanghai")
    assert good.status_code == 200 and good.json()["games"]
    assert good.json()["date"] == "2026-06-12"
    # A normal NBA slate can contain multiple games on one local calendar
    # date.  The highlights projection must preserve every normalized game;
    # the UI is responsible for selecting one card for the detailed HUD.
    assert [game["game_id"] for game in good.json()["games"]] == [
        "2026-finals-g4",
        "2026-demo-den-gsw",
        "2026-demo-lal-nyk",
    ]
    assert all(
        game["series_game_number"] is None
        for game in good.json()["games"]
        if game["game_id"].startswith("2026-demo-")
    )
    assert empty.status_code == 200 and empty.json()["games"] == []
    assert future.status_code == 400 and future.json()["error"]["code"] == "INVALID_PAYLOAD"


@pytest.mark.asyncio
async def test_fixture_demo_date_populates_unscoped_today_projection() -> None:
    """A fixture deployment can keep the interview landing page populated."""

    app = create_app(settings=Settings(highlights_demo_date="2026-06-12"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/highlights", params={"timezone": "Asia/Shanghai"}
        )

    assert response.status_code == 200
    assert response.json()["date"] == "2026-06-12"
    assert len(response.json()["games"]) == 3


@pytest.mark.asyncio
async def test_hybrid_fallback_is_not_presented_as_today() -> None:
    """A live-provider outage must not turn an old snapshot into today's slate."""

    primary = FixtureProvider(scenario="timeout")
    fallback = FixtureProvider()
    gateway = ProviderGateway(primary, fallback=fallback, max_retries=0)
    clock = FixedClock(datetime(2026, 6, 12, 12, 0, tzinfo=UTC))
    settings = Settings(public_data_mode="hybrid")
    usecase = ChatUseCase(primary, gateway=gateway, clock=clock, settings=settings)
    app = create_app(settings=settings, usecase=usecase)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/highlights",
            params={"timezone": "Asia/Shanghai"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_BUSY"


@pytest.mark.asyncio
async def test_highlights_recent_returns_latest_five_games() -> None:
    app = create_app(settings=Settings(highlights_demo_date="2026-06-12"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/highlights/recent",
            params={"limit": 5, "timezone": "Asia/Shanghai"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["from"] == "2026-02-13"
    assert payload["to"] == "2026-06-12"
    assert [game["game_id"] for game in payload["games"]] == [
        "2026-finals-g4",
        "2026-demo-den-gsw",
        "2026-demo-lal-nyk",
        "2026-finals-g3",
        "2026-finals-g2",
    ]


@pytest.mark.asyncio
async def test_highlights_range_returns_all_games_in_interval() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/highlights/range",
            params={
                "from": "2026-06-06",
                "to": "2026-06-12",
                "timezone": "Asia/Shanghai",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["from"] == "2026-06-06"
    assert payload["to"] == "2026-06-12"
    assert len(payload["games"]) == 6
    assert payload["games"][0]["game_id"] == "2026-finals-g4"


@pytest.mark.asyncio
async def test_highlights_range_rejects_future_and_wide_intervals() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        future = await client.get(
            "/api/v1/highlights/range",
            params={"from": "2999-01-01", "to": "2999-01-02"},
        )
        wide = await client.get(
            "/api/v1/highlights/range",
            params={"from": "2025-01-01", "to": "2026-01-01"},
        )

    assert future.status_code == 400
    assert future.json()["error"]["code"] == "INVALID_PAYLOAD"
    assert wide.status_code == 400
    assert wide.json()["error"]["code"] == "INVALID_PAYLOAD"


@pytest.mark.asyncio
async def test_highlight_detail_returns_leaders_and_play_by_play() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/highlights/2026-finals-g4/detail",
            params={"timezone": "Asia/Shanghai"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["game"]["home_score"] == 108
    assert payload["game"]["away_score"] == 104
    assert payload["leaders"][0]["player_name"] == "杰伦·布朗"
    assert payload["leaders"][0]["points"] == 32
    assert len(payload["plays"]) == 6
    assert payload["plays"][-1]["period"] == 4
    assert payload["plays"][-1]["home_score"] == 108
    assert payload["plays"][-1]["away_score"] == 104


@pytest.mark.asyncio
async def test_highlight_detail_not_found_is_safe() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/highlights/not-a-game/detail")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "provider" not in response.text.lower()


@pytest.mark.asyncio
async def test_highlights_availability_returns_tri_state_fixture_calendar() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/highlights/availability",
            params={
                "from": "2026-06-06",
                "to": "2026-06-13",
                "timezone": "Asia/Shanghai",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["from"] == "2026-06-06"
    assert payload["to"] == "2026-06-13"
    assert [day["status"] for day in payload["days"]] == [
        "available",
        "empty",
        "available",
        "empty",
        "available",
        "empty",
        "available",
        "empty",
    ]
    assert payload["days"][0]["game_count"] == 1
    assert payload["days"][6]["game_count"] == 3
    assert payload["days"][1]["game_count"] == 0
    assert all(day["is_future"] is False for day in payload["days"])


@pytest.mark.asyncio
async def test_highlights_availability_marks_future_days_unknown_without_querying_them() -> None:
    app = create_app()
    provider = app.state.provider
    before = provider.calls
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/highlights/availability",
            params={
                "from": "2999-01-01",
                "to": "2999-01-03",
                "timezone": "Asia/Shanghai",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert [day["status"] for day in payload["days"]] == ["unknown"] * 3
    assert all(day["is_future"] is True for day in payload["days"])
    assert provider.calls == before


@pytest.mark.asyncio
async def test_highlights_availability_does_not_turn_upstream_failure_into_empty() -> None:
    provider = FixtureProvider(scenario="timeout")
    gateway = ProviderGateway(provider, cache=InMemoryTTLCache())
    usecase = ChatUseCase(provider, gateway=gateway)
    app = create_app(usecase=usecase)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/highlights/availability",
            params={"from": "2026-06-06", "to": "2026-06-07"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [day["status"] for day in payload["days"]] == ["unknown", "unknown"]
    assert payload["evidence_state"] == "none"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-06-01", "to": "2026-07-02"},
        {"from": "2026-06-08"},
        {"from": "2026-06-09", "to": "2026-06-08"},
        {"from": "2026-02-30", "to": "2026-03-01"},
    ],
)
async def test_highlights_availability_rejects_invalid_ranges(params: dict[str, str]) -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/highlights/availability", params=params)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PAYLOAD"
