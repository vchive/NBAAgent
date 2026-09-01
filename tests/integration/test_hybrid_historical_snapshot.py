"""Hybrid-mode regressions for historical data omitted by a live archive."""

from __future__ import annotations

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.config import Settings
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway


def _hybrid_usecase() -> ChatUseCase:
    primary = FixtureProvider(scenario="empty")
    return ChatUseCase(
        primary,
        gateway=ProviderGateway(primary, fallback=FixtureProvider(), max_retries=0),
        settings=Settings(public_data_mode="hybrid"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("2025-26 总决赛 G4 谁得分最高？", "杰伦·布朗"),
        ("2025-26 总决赛系列赛大比分是多少？", "3–1"),
        ("1999 年总冠军是谁？", "马刺"),
        ("凯尔特人历史夺冠次数？", "18"),
    ],
)
async def test_hybrid_historical_queries_use_matching_verified_snapshot_when_live_is_empty(
    message: str, expected: str
) -> None:
    result = await _hybrid_usecase().handle({"message": message})

    assert result.status == "completed"
    assert result.evidence_state == "partial"
    assert expected in result.answer_markdown


@pytest.mark.asyncio
async def test_hybrid_current_day_schedule_stays_empty_when_live_returns_empty() -> None:
    """The historical fallback must not make an old fixture look like today's slate."""

    result = await _hybrid_usecase().handle({"message": "今天有哪些 NBA 比赛？"})

    assert result.status == "no_data"
    assert "凯尔特人" not in result.answer_markdown
