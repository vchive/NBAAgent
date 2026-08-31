from __future__ import annotations

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.providers.fixture_provider import FixtureProvider


@pytest.mark.asyncio
async def test_three_turn_context_and_fresh_session_isolation() -> None:
    usecase = ChatUseCase(FixtureProvider())
    session_id = None
    answers = []
    for message in (
        "2025-26 总决赛 G4 谁得分最高？",
        "那场最后五秒发生了什么？",
        "最后那个球是哪位球员？",
    ):
        result = await usecase.handle({"session_id": session_id, "message": message})
        assert result.status == "completed"
        session_id = result.session_id
        answers.append(result.answer_markdown)
    assert "杰伦·布朗" in answers[0]
    assert "2 个回合" in answers[1]
    fresh = await usecase.handle({"message": "那场最后五秒发生了什么？"})
    assert fresh.status == "needs_clarification"


@pytest.mark.asyncio
async def test_recent_game_question_loads_latest_fixture_pbp_without_context() -> None:
    usecase = ChatUseCase(FixtureProvider())
    result = await usecase.handle({"message": "最近一场比赛的关键回合是什么？"})
    assert result.status == "completed"
    assert "2 个回合" in result.answer_markdown
    assert "谢伊·吉尔杰斯-亚历山大" in result.answer_markdown


@pytest.mark.asyncio
async def test_selected_highlight_game_binds_chat_context_for_shorthand_and_matchup() -> None:
    """A card selection must scope both pronoun and explicit matchup turns."""

    usecase = ChatUseCase(FixtureProvider())
    selected = "2026-finals-g4"

    when = await usecase.handle(
        {"message": "这场比赛什么时候打的？", "selected_game_id": selected}
    )
    assert when.status == "completed"
    assert "2026-06-12 09:30" in when.answer_markdown

    leader = await usecase.handle(
        {"message": "雷霆 对 凯尔特人 谁得分最高？", "selected_game_id": selected}
    )
    assert leader.status == "completed"
    assert "杰伦·布朗" in leader.answer_markdown
    assert "32 分" in leader.answer_markdown


@pytest.mark.asyncio
async def test_selected_highlight_game_does_not_override_unrelated_matchup() -> None:
    usecase = ChatUseCase(FixtureProvider())
    result = await usecase.handle(
        {"message": "湖人 对 勇士 谁得分最高？", "selected_game_id": "2026-finals-g4"}
    )
    assert result.status == "completed"
    assert "湖人" in result.answer_markdown
    assert "2026-06-12 09:30" not in result.answer_markdown


@pytest.mark.asyncio
async def test_selected_game_does_not_override_explicit_schedule_scope() -> None:
    usecase = ChatUseCase(FixtureProvider())
    result = await usecase.handle(
        {
            "message": "今天有哪些 NBA 比赛？",
            "selected_game_id": "2026-finals-g4",
        }
    )
    assert result.status in {"completed", "no_data"}
    # The selected historical card is not rendered as today's schedule.
    assert "2026-06-12 09:30" not in result.answer_markdown


@pytest.mark.asyncio
async def test_selected_game_handles_matchup_typo_and_last_five_seconds_query() -> None:
    usecase = ChatUseCase(FixtureProvider())
    result = await usecase.handle(
        {
            "message": "雷霆堆栈凯尔特人，最后 5 秒那个上篮，是谁投的、当时比分多少？",
            "selected_game_id": "2026-finals-g4",
        }
    )
    assert result.status == "completed"
    assert "2 个回合" in result.answer_markdown
    assert "106–102" in result.answer_markdown or "108–104" in result.answer_markdown
