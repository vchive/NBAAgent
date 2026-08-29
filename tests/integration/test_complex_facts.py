from __future__ import annotations

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.providers.fixture_provider import FixtureProvider


@pytest.mark.asyncio
async def test_series_aggregate_is_derived_from_verified_games() -> None:
    result = await ChatUseCase(FixtureProvider()).handle(
        {"message": "这轮系列赛目前大比分是多少？"}
    )

    assert result.status == "completed"
    assert result.evidence_state == "verified"
    assert "3–1" in result.answer_markdown
    assert "凯尔特人" in result.answer_markdown


@pytest.mark.asyncio
async def test_last_five_seconds_uses_ordered_pbp_window() -> None:
    result = await ChatUseCase(FixtureProvider()).handle(
        {"message": "2025-26 总决赛 G4 最后 5 秒发生了什么？"}
    )

    assert result.status == "completed"
    assert "2 个回合" in result.answer_markdown
    assert "5秒" in result.answer_markdown
    assert "0秒" in result.answer_markdown


@pytest.mark.asyncio
async def test_false_winner_premise_is_corrected_without_trusting_user_text() -> None:
    result = await ChatUseCase(FixtureProvider()).handle(
        {"message": "我记得雷霆在 G4 赢了，帮我核验一下。"}
    )

    assert result.status == "completed"
    assert result.corrections
    correction = result.corrections[0]
    status = getattr(correction, "status", None)
    if status is None and isinstance(correction, dict):
        status = correction.get("status")
    assert str(getattr(status, "value", status)).lower() == "corrected"
    assert "凯尔特人" in result.answer_markdown


@pytest.mark.asyncio
async def test_empty_complex_fact_result_does_not_invent_numbers() -> None:
    result = await ChatUseCase(FixtureProvider(scenario="empty")).handle(
        {"message": "这轮系列赛目前大比分是多少？"}
    )

    assert result.status in {"no_data", "needs_clarification"}
    assert result.evidence_state in {"none", "partial"}
    assert "3–1" not in result.answer_markdown
