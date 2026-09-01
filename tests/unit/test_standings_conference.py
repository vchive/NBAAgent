"""Regression coverage for conference-scoped standings questions."""

import httpx
import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.application.parser import IntentParser
from apps.api.src.application.query_planner import QueryPlanner
from apps.api.src.config import Settings
from apps.api.src.main import create_app
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway


def test_parser_and_planner_preserve_east_conference_scope() -> None:
    parsed = IntentParser().parse("2025-26 赛季东部排名第一的球队是谁？")

    assert parsed.intent.conference == "East"
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None
    assert plan.operation == "get_standings"
    assert plan.kwargs == {"conference": "East"}


def test_parser_and_planner_preserve_west_conference_scope() -> None:
    parsed = IntentParser().parse("2025-26 赛季西区排名第一的球队是谁？")

    assert parsed.intent.conference == "West"
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None
    assert plan.operation == "get_standings"
    assert plan.kwargs == {"conference": "West"}


def test_compact_both_conferences_wording_does_not_choose_one_side() -> None:
    parsed = IntentParser().parse("2025-26 赛季东西部排名第一分别是谁？")

    assert parsed.intent.conference is None


@pytest.mark.asyncio
async def test_http_standings_does_not_leak_the_other_conference() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        east = await client.post(
            "/api/v1/chat",
            json={"message": "2025-26 赛季东部排名第一的球队是谁？"},
        )
        west = await client.post(
            "/api/v1/chat",
            json={"message": "2025-26 赛季西部排名第一的球队是谁？"},
        )

    assert east.status_code == 200
    assert west.status_code == 200
    east_answer = east.json()["answer_markdown"]
    west_answer = west.json()["answer_markdown"]
    assert "凯尔特人" in east_answer
    assert "雷霆" not in east_answer
    assert "雷霆" in west_answer
    assert "凯尔特人" not in west_answer


@pytest.mark.asyncio
async def test_hybrid_historical_standings_uses_the_bounded_snapshot_when_live_is_empty() -> None:
    """An empty live archive must not degrade a historical ranking to a schedule no-data answer."""

    primary = FixtureProvider(scenario="empty")
    fallback = FixtureProvider()
    usecase = ChatUseCase(
        primary,
        gateway=ProviderGateway(primary, fallback=fallback, max_retries=0),
        settings=Settings(public_data_mode="hybrid"),
    )

    result = await usecase.handle({"message": "2025-26 赛季东部排名第一的球队是谁？"})

    assert result.status == "completed"
    assert result.evidence_state == "partial"
    assert "凯尔特人" in result.answer_markdown
    assert "60" in result.answer_markdown
