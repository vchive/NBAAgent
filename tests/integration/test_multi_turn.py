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
async def test_failed_matchup_does_not_leak_demo_game_into_g_number_follow_up() -> None:
    """Keep an unresolved matchup scoped when a later turn mentions G2."""

    usecase = ChatUseCase(FixtureProvider())
    session_id = None
    for message in ("2026尼克斯-马刺", "你觉得最精华的是哪一场？"):
        result = await usecase.handle({"session_id": session_id, "message": message})
        session_id = result.session_id
        assert result.status == "no_data"

    result = await usecase.handle(
        {"session_id": session_id, "message": "为什么不是G2？"}
    )

    assert result.status == "no_data"
    assert "尼克斯" in result.answer_markdown
    assert "马刺" in result.answer_markdown
    assert "凯尔特人" not in result.answer_markdown
    assert "雷霆" not in result.answer_markdown
    assert "107–99" not in result.answer_markdown


@pytest.mark.asyncio
async def test_explicit_matchup_switch_drops_old_game_before_pronoun_followup() -> None:
    """A new two-team topic must not leave the prior fixture game active."""

    usecase = ChatUseCase(FixtureProvider())
    first = await usecase.handle(
        {"message": "2025-26 总决赛 G4 谁得分最高？"}
    )
    assert first.status == "completed"

    switched = await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "2026尼克斯-马刺",
        }
    )
    assert switched.status == "no_data"
    context = await usecase.context_manager.load(first.session_id)
    assert context is not None
    assert context.active_game is None
    assert context.active_player is None

    follow_up = await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "这场比赛最后五秒发生了什么？",
        }
    )
    assert follow_up.status in {"needs_clarification", "no_data"}
    assert "凯尔特人" not in follow_up.answer_markdown
    assert "雷霆" not in follow_up.answer_markdown
    assert "108–104" not in follow_up.answer_markdown
    context = await usecase.context_manager.load(first.session_id)
    assert context is not None and context.active_game is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "game_two_question",
    ["G2那场怎么打的？", "那G2呢？", "第二场怎么样？"],
)
async def test_numbered_series_followup_replaces_prior_active_game(
    game_two_question: str,
) -> None:
    """A bare G2 remains in the prior series and becomes the next pronoun scope."""

    usecase = ChatUseCase(FixtureProvider())
    session_id = None
    for message in (
        "2025-26 雷霆-凯尔特人系列赛",
        "2025-26 总决赛 G4 谁赢了？",
        game_two_question,
    ):
        result = await usecase.handle(
            {"session_id": session_id, "message": message}
        )
        session_id = result.session_id
        assert result.status == "completed"

    assert "107–99" in result.answer_markdown
    assert "108–104" not in result.answer_markdown
    context = await usecase.context_manager.load(session_id)
    assert context is not None and context.active_game is not None
    assert context.active_game.canonical_id == "2026-finals-g2"

    pronoun = await usecase.handle(
        {"session_id": session_id, "message": "那场谁赢了？"}
    )
    assert pronoun.status == "completed"
    assert "雷霆" in pronoun.answer_markdown
    assert "107–99" in pronoun.answer_markdown
    assert "108–104" not in pronoun.answer_markdown


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
async def test_selected_game_venue_stays_bound_and_does_not_add_unrelated_facts() -> None:
    usecase = ChatUseCase(FixtureProvider())
    result = await usecase.handle(
        {"message": "这场比赛在哪儿举办的？", "selected_game_id": "2026-finals-g4"}
    )
    assert result.status == "completed"
    assert "TD Garden" in result.answer_markdown
    assert "Boston" in result.answer_markdown
    assert "勇士" not in result.answer_markdown
    assert "掘金" not in result.answer_markdown
    assert "分差" not in result.answer_markdown
    assert "总得分" not in result.answer_markdown


@pytest.mark.asyncio
async def test_selected_game_missing_duration_stays_bound_without_unrelated_facts() -> None:
    usecase = ChatUseCase(FixtureProvider())
    result = await usecase.handle(
        {"message": "这场比赛时长多久？", "selected_game_id": "2026-finals-g4"}
    )
    assert result.status == "completed"
    assert "雷霆" in result.answer_markdown
    assert "凯尔特人" in result.answer_markdown
    assert "暂时无法核验" in result.answer_markdown
    assert "分差" not in result.answer_markdown
    assert "总得分" not in result.answer_markdown


@pytest.mark.asyncio
async def test_selected_game_missing_coaches_stays_bound_without_unrelated_facts() -> None:
    """Coach metadata is optional; absence must not leak the score summary."""

    result = await ChatUseCase(FixtureProvider()).handle(
        {
            "message": "这场比赛双方教练都是谁？",
            "selected_game_id": "2026-demo-den-gsw",
        }
    )
    assert result.status == "completed"
    assert "教练" in result.answer_markdown
    assert "暂无" in result.answer_markdown or "无法核验" in result.answer_markdown
    assert "103–99" not in result.answer_markdown
    assert "分差" not in result.answer_markdown
    assert "总得分" not in result.answer_markdown


@pytest.mark.asyncio
async def test_selected_game_coaches_are_rendered_when_provider_supplies_them() -> None:
    result = await ChatUseCase(FixtureProvider()).handle(
        {
            "message": "这场比赛双方教练都是谁？",
            "selected_game_id": "2026-finals-g4",
        }
    )
    assert result.status == "completed"
    assert "乔·马祖拉" in result.answer_markdown
    assert "马克·戴格诺特" in result.answer_markdown
    assert "108–104" not in result.answer_markdown
    assert "分差" not in result.answer_markdown


@pytest.mark.asyncio
async def test_selected_game_binds_generic_game_metadata_without_pronoun() -> None:
    """Event drill-down scope also covers metadata wording without “这场”."""

    result = await ChatUseCase(FixtureProvider()).handle(
        {
            "message": "比赛双方教练都是谁？",
            "selected_game_id": "2026-finals-g4",
        }
    )
    assert result.status == "completed"
    assert "乔·马祖拉" in result.answer_markdown
    assert "马克·戴格诺特" in result.answer_markdown
    assert "108–104" not in result.answer_markdown
    assert "请补充" not in result.answer_markdown


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
async def test_unmatched_head_to_head_returns_scoped_message_not_team_stats() -> None:
    result = await ChatUseCase(FixtureProvider()).handle(
        {"message": "2026尼克斯-马刺"}
    )
    assert result.status == "no_data"
    assert "尼克斯" in result.answer_markdown
    assert "马刺" in result.answer_markdown
    assert "统计记录" not in result.answer_markdown


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
    assert "\n- 第4节" in result.answer_markdown
    assert "原始事件描述" not in result.answer_markdown
    assert "凯尔特人 106–102 雷霆" in result.answer_markdown
    assert "106–102" in result.answer_markdown or "108–104" in result.answer_markdown


@pytest.mark.asyncio
async def test_contextual_matchup_and_last_shooter_questions_stay_on_selected_game() -> None:
    usecase = ChatUseCase(FixtureProvider())
    first = await usecase.handle(
        {
            "message": "2025-26 总决赛 G4 谁得分最高？",
            "selected_game_id": "2026-finals-g4",
        }
    )
    assert first.status == "completed"
    second = await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "这场比赛谁打谁？",
            "selected_game_id": "2026-finals-g4",
        }
    )
    assert second.status == "completed"
    assert "对阵双方" in second.answer_markdown
    assert "雷霆" in second.answer_markdown and "凯尔特人" in second.answer_markdown
    assert "暂无可核验的逐回合记录" not in second.answer_markdown
    third = await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "最后谁投篮的，在什么位置？",
            "selected_game_id": "2026-finals-g4",
        }
    )
    assert third.status == "completed"
    # The terminal fixture row is an end-of-game marker without a shooter;
    # assert that the event-level answer was rendered instead of a generic
    # clarification, without requiring a particular missing-data wording.
    assert "出手者" in third.answer_markdown
    assert "出手位置字段" in third.answer_markdown


@pytest.mark.asyncio
async def test_selected_game_ranking_and_tactical_answers_use_verified_game_detail() -> None:
    """Selected highlights must carry both stat ranking and late-game evidence."""

    usecase = ChatUseCase(FixtureProvider())
    selected = "2026-finals-g4"

    ranked = await usecase.handle(
        {"message": "这场比赛谁是得分第三的选手？", "selected_game_id": selected}
    )
    assert ranked.status == "completed"
    assert "杰森·塔图姆" in ranked.answer_markdown
    assert "27 分" in ranked.answer_markdown

    tactical = await usecase.handle(
        {"message": "凯尔特人为什么能赢下这场比赛？", "selected_game_id": selected}
    )
    assert tactical.status == "completed"
    assert "108–104" in tactical.answer_markdown
    assert "德里克·怀特" in tactical.answer_markdown
    assert "31秒" in tactical.answer_markdown
    assert "凯尔特人 106–101 雷霆" in tactical.answer_markdown
    assert "不能据此断言" in tactical.answer_markdown


@pytest.mark.asyncio
async def test_selected_game_losing_team_win_premise_is_corrected_before_analysis() -> None:
    """A causal question must not silently accept the losing team as winner."""

    result = await ChatUseCase(FixtureProvider()).handle(
        {
            "message": "雷霆为什么能赢下这场比赛？",
            "selected_game_id": "2026-finals-g4",
        }
    )

    assert result.status == "completed"
    assert result.corrections
    correction = result.corrections[0]
    status = getattr(correction, "status", None)
    if status is None and isinstance(correction, dict):
        status = correction.get("status")
    assert str(getattr(status, "value", status)).lower() == "corrected"
    assert "核验结果显示，正确信息是：凯尔特人" in result.answer_markdown
    assert "凯尔特人" in result.answer_markdown
    assert "雷霆 能赢下比赛" not in result.answer_markdown


@pytest.mark.asyncio
async def test_selected_game_direct_last_shot_answer_is_concise_and_bounded() -> None:
    usecase = ChatUseCase(FixtureProvider())
    result = await usecase.handle(
        {"message": "最后谁投篮的，在什么位置？", "selected_game_id": "2026-finals-g4"}
    )

    assert result.status == "completed"
    assert "终场标记" in result.answer_markdown
    assert "谢伊·吉尔杰斯-亚历山大" in result.answer_markdown
    assert "出手位置字段" in result.answer_markdown
    assert "| 节次" not in result.answer_markdown


@pytest.mark.asyncio
async def test_conversational_fact_references_are_reverified_and_answered() -> None:
    usecase = ChatUseCase(FixtureProvider())
    first = await usecase.handle({"message": "2025-26 总决赛 G4 谁得分最高？"})

    scorer = await usecase.handle(
        {"session_id": first.session_id, "message": "你刚才说谁拿了 32 分？"}
    )
    shooter = await usecase.handle(
        {"session_id": first.session_id, "message": "刚才那个球是谁投的？"}
    )

    assert scorer.status == "completed"
    assert "杰伦·布朗" in scorer.answer_markdown
    assert "32 分" in scorer.answer_markdown
    assert shooter.status == "completed"
    assert "谢伊·吉尔杰斯-亚历山大" in shooter.answer_markdown
    assert "终场标记" in shooter.answer_markdown
    counters = usecase.gateway.counters()
    # Every factual turn re-enters the verified gateway; an already verified
    # box score may be served from the freshness cache instead of re-fetching.
    assert counters["cache_read_count"] >= 3
    assert counters["provider_call_count"] >= 2
