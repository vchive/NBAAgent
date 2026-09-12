"""Hybrid-mode regressions for keeping demo snapshots out of public chat."""

from __future__ import annotations

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.config import Settings
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway


def _hybrid_usecase() -> tuple[ChatUseCase, FixtureProvider]:
    primary = FixtureProvider(scenario="empty")
    snapshot = FixtureProvider()
    return (
        ChatUseCase(
            primary,
            gateway=ProviderGateway(primary, fallback=snapshot, max_retries=0),
            settings=Settings(public_data_mode="hybrid"),
        ),
        snapshot,
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
async def test_hybrid_historical_queries_do_not_silently_use_demo_snapshot(
    message: str, expected: str
) -> None:
    usecase, snapshot = _hybrid_usecase()
    result = await usecase.handle({"message": message})

    assert result.status in {"needs_clarification", "no_data"}
    assert result.data_origin == "none"
    assert expected not in result.answer_markdown
    assert snapshot.calls == 0


@pytest.mark.asyncio
async def test_hybrid_current_day_schedule_stays_empty_when_live_returns_empty() -> None:
    """The historical fallback must not make an old fixture look like today's slate."""

    usecase, snapshot = _hybrid_usecase()
    result = await usecase.handle({"message": "今天有哪些 NBA 比赛？"})

    assert result.status == "no_data"
    assert "凯尔特人" not in result.answer_markdown
    assert snapshot.calls == 0
