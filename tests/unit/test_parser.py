"""Regression coverage for natural-language intent/time parsing."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.api.src.application.parser import GAMES, IntentParser
from apps.api.src.application.query_planner import QueryPlanner
from apps.api.src.domain.models import (
    Category,
    ConversationContext,
    EntityKind,
    HistoryRecordType,
    IntentName,
    TimeWindowScope,
)
from apps.api.src.domain.time_policy import FixedClock, local_date_range, resolve_season_phrase


def test_full_calendar_date_is_not_misread_as_a_season() -> None:
    clock = FixedClock(datetime(2026, 6, 12, 12, tzinfo=UTC))
    parsed = IntentParser(clock=clock).parse("2026-06-12 有哪些比赛？")

    assert parsed.intent.intent_name is IntentName.SCHEDULE_RESULT
    assert parsed.intent.season is None
    assert parsed.intent.date_range == local_date_range(date(2026, 6, 12))
    assert resolve_season_phrase("2026-06-12 有哪些比赛？", clock) is None


def test_schedule_week_phrases_produce_a_bounded_date_range() -> None:
    clock = FixedClock(datetime(2026, 8, 30, 10, tzinfo=UTC))
    parsed = IntentParser(clock=clock).parse("下周有比赛吗？")
    assert parsed.intent.intent_name is IntentName.SCHEDULE_RESULT
    assert parsed.intent.date_range is not None
    assert parsed.intent.date_range.start_inclusive == datetime(2026, 8, 30, 16, tzinfo=UTC)
    assert parsed.intent.date_range.end_exclusive == datetime(2026, 9, 6, 16, tzinfo=UTC)
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_games"


def test_common_appearance_typo_and_durant_alias_are_understood() -> None:
    parsed = IntentParser().parse("杜兰特近期出厂次数")

    assert parsed.intent.intent_name is IntentName.DATA
    assert any(item.canonical_id == "kevin-durant" for item in parsed.intent.entities)
    assert parsed.intent.metrics[0].name == "games"
    assert not parsed.missing_slots


def test_matchup_typo_is_normalized_when_two_teams_are_present() -> None:
    parsed = IntentParser().parse("雷霆堆栈凯尔特人最后 5 秒那个上篮是谁投的？")

    assert parsed.intent.intent_name is IntentName.PLAY_BY_PLAY
    team_ids = {
        item.canonical_id
        for item in parsed.intent.entities
        if item.kind is EntityKind.TEAM
    }
    assert team_ids == {"okc", "bos"}


def test_chinese_last_five_seconds_is_play_by_play() -> None:
    parsed = IntentParser().parse("2025-26 总决赛 G4 最后五秒发生了什么？")

    assert parsed.intent.intent_name is IntentName.PLAY_BY_PLAY
    assert parsed.intent.clock_window is not None
    assert parsed.intent.clock_window.scope is TimeWindowScope.GAME_END
    assert parsed.intent.clock_window.end_seconds == Decimal("5")
    assert any(entity.canonical_id == "2026-finals-g4" for entity in parsed.intent.entities)


def test_game_number_forms_do_not_confuse_game_with_period() -> None:
    arabic = IntentParser().parse("2025-26 总决赛第4场比赛结果")
    chinese = IntentParser().parse("2025-26 总决赛第四场比赛结果")

    for parsed in (arabic, chinese):
        assert parsed.intent.game_number == 4
        assert parsed.intent.period is None
        assert [
            item.canonical_id for item in parsed.intent.entities if item.kind.value == "GAME"
        ] == ["2026-finals-g4"]


def test_explicit_fourth_quarter_keeps_period_scope() -> None:
    parsed = IntentParser().parse("总决赛 G4 第四节最后五秒")
    assert parsed.intent.game_number == 4
    assert parsed.intent.period == 4
    assert parsed.intent.clock_window is not None
    assert parsed.intent.clock_window.scope is TimeWindowScope.PERIOD_END


def test_history_latest_and_franchise_count_use_distinct_plans() -> None:
    latest = IntentParser().parse("最近一次总冠军是谁？")
    latest_plan = QueryPlanner().build(latest.intent)
    assert latest_plan is not None
    latest_query = latest_plan.args[0]
    assert latest_query.record_type is HistoryRecordType.CHAMPIONSHIP
    assert latest_query.limit == 1

    count = IntentParser().parse("凯尔特人历史夺冠次数")
    count_plan = QueryPlanner().build(count.intent)
    assert count_plan is not None
    count_query = count_plan.args[0]
    assert count_query.record_type is HistoryRecordType.FRANCHISE_RECORD
    assert count_query.limit == 1


def test_latest_franchise_title_year_is_not_misread_as_a_count() -> None:
    parsed = IntentParser().parse("凯尔特人队史上一次夺冠是哪一年？")
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None
    query = plan.args[0]
    assert query.record_type is HistoryRecordType.CHAMPIONSHIP
    assert query.limit == 1


def test_game_specific_schedule_result_uses_summary_lookup() -> None:
    parsed = IntentParser().parse("总决赛 G3 比赛结果")
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None
    assert plan.operation == "get_game_summary"
    assert plan.args == ("2026-finals-g3",)


def test_shorthand_without_active_game_requires_clarification() -> None:
    parsed = IntentParser().parse("那场比分如何？")
    assert any(slot.name == "game" for slot in parsed.missing_slots)


def test_recent_game_play_by_play_is_resolved_without_manual_card_selection() -> None:
    parsed = IntentParser().parse("最近一场比赛的关键回合是什么？")
    assert parsed.intent.intent_name is IntentName.PLAY_BY_PLAY
    assert parsed.intent.recent_game is True
    assert not parsed.intent.missing_slots
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "get_recent_play_by_play"


def test_unspecified_game_reference_requires_clarification() -> None:
    parsed = IntentParser().parse("某场最后一攻是不是三分？")
    assert any(slot.name == "game" for slot in parsed.missing_slots)


def test_explicit_game_reference_is_not_overridden_by_shorthand() -> None:
    context = ConversationContext(
        session_id=uuid4(),
        active_game={
            "kind": "GAME",
            "canonical_id": "2026-finals-g3",
            "display_name": "2025-26 总决赛 G3",
        },
        expires_at_utc=datetime(2026, 8, 28, tzinfo=UTC),
    )
    parsed = IntentParser().parse("G4 那场比赛谁赢了？", context)
    assert parsed.intent.intent_name is IntentName.SCHEDULE_RESULT
    assert parsed.intent.intent_name is not IntentName.FOLLOW_UP
    assert any(
        item.kind is EntityKind.GAME and item.canonical_id == "2026-finals-g4"
        for item in parsed.intent.entities
    )


def test_selected_game_ranked_stat_preserves_data_intent() -> None:
    """A card-scoped ranking is a box-score query, not a generic follow-up."""

    context = ConversationContext(
        session_id=uuid4(),
        active_game={
            "kind": "GAME",
            "canonical_id": "2026-finals-g4",
            "display_name": "2025-26 总决赛 G4",
        },
        expires_at_utc=datetime(2026, 8, 28, tzinfo=UTC),
    )
    parsed = IntentParser().parse("这场比赛谁是得分第三的选手？", context)

    assert parsed.intent.intent_name is IntentName.DATA
    assert parsed.intent.metrics[0].name == "points"
    assert parsed.intent.metrics[0].rank == 3
    assert any(
        item.kind is EntityKind.GAME and item.canonical_id == "2026-finals-g4"
        for item in parsed.intent.entities
    )


def test_selected_game_tactical_question_preserves_tactical_intent() -> None:
    context = ConversationContext(
        session_id=uuid4(),
        active_game={
            "kind": "GAME",
            "canonical_id": "2026-finals-g4",
            "display_name": "2025-26 总决赛 G4",
        },
        expires_at_utc=datetime(2026, 8, 28, tzinfo=UTC),
    )
    parsed = IntentParser().parse("凯尔特人为什么能赢下这场比赛？", context)

    assert parsed.intent.intent_name is IntentName.TACTICAL
    assert any(
        item.kind is EntityKind.GAME and item.canonical_id == "2026-finals-g4"
        for item in parsed.intent.entities
    )


@pytest.mark.parametrize(
    ("question", "metric"),
    [
        ("这场比赛在哪儿举办的？", "venue"),
        ("这场比赛在哪儿进行的？", "venue"),
        ("这场比赛时长多久？", "game_duration"),
    ],
)
def test_game_metadata_questions_are_typed_instead_of_falling_through_to_stats(
    question: str, metric: str
) -> None:
    context = ConversationContext(
        session_id=uuid4(),
        active_game={
            "kind": "GAME",
            "canonical_id": "2026-finals-g4",
            "display_name": "2025-26 总决赛 G4",
        },
        expires_at_utc=datetime(2026, 8, 28, tzinfo=UTC),
    )
    parsed = IntentParser().parse(question, context)
    assert any(item.name == metric for item in parsed.intent.metrics)


def test_each_period_clock_window_is_explicit_and_does_not_require_a_period_slot() -> None:
    parsed = IntentParser().parse("总决赛 G4 每节最后五秒发生了什么？")
    assert parsed.intent.clock_window is not None
    assert parsed.intent.clock_window.scope is TimeWindowScope.PERIOD_END
    assert parsed.intent.clock_window.all_periods is True
    assert parsed.intent.period is None
    assert not any(slot.name == "period" for slot in parsed.missing_slots)


def test_unnamed_period_clock_window_requests_a_period_clarification() -> None:
    parsed = IntentParser().parse("总决赛 G4 某节最后五秒发生了什么？")
    assert any(slot.name == "period" for slot in parsed.missing_slots)


def test_rank_without_season_uses_current_nba_season() -> None:
    clock = FixedClock(datetime(2026, 8, 27, 12, tzinfo=UTC))
    parsed = IntentParser(clock=clock).parse("凯尔特人排名")
    assert parsed.intent.season is not None
    assert parsed.intent.season.label == "2026-27"
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "get_standings"


def test_series_pronoun_is_not_treated_as_a_missing_single_game() -> None:
    parsed = IntentParser().parse("这轮系列赛目前大比分是多少？")
    assert parsed.intent.intent_name is IntentName.SCHEDULE_RESULT
    assert parsed.intent.metrics[0].scope.value == "SERIES"
    assert not any(slot.name == "game" for slot in parsed.missing_slots)
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_games"


def test_contextual_clock_only_follow_up_inherits_active_game() -> None:
    context = ConversationContext(
        session_id=uuid4(),
        active_game={
            "kind": "GAME",
            "canonical_id": "2026-finals-g4",
            "display_name": "2025-26 总决赛 G4",
        },
        expires_at_utc=datetime(2026, 8, 28, tzinfo=UTC),
    )
    parsed = IntentParser().parse("每节最后五秒发生了什么？", context)
    assert parsed.intent.intent_name is IntentName.FOLLOW_UP
    assert any(item.canonical_id == "2026-finals-g4" for item in parsed.intent.entities)
    assert not any(slot.name == "game" for slot in parsed.missing_slots)


def test_contextual_last_shooter_question_inherits_active_game() -> None:
    context = ConversationContext(
        session_id=uuid4(),
        active_game={
            "kind": "GAME",
            "canonical_id": "2026-finals-g4",
            "display_name": "2025-26 总决赛 G4",
        },
        expires_at_utc=datetime(2026, 8, 28, tzinfo=UTC),
    )
    parsed = IntentParser().parse("最后谁投篮的，在什么位置？", context)
    assert parsed.intent.intent_name is IntentName.PLAY_BY_PLAY
    assert any(item.canonical_id == "2026-finals-g4" for item in parsed.intent.entities)
    assert not any(slot.name == "game" for slot in parsed.missing_slots)


def test_explicit_non_fixture_season_does_not_bind_fixture_game() -> None:
    parsed = IntentParser().parse("2024-25 总决赛 G4 比赛结果")
    assert parsed.intent.game_number == 4
    assert not any(item.kind is EntityKind.GAME for item in parsed.intent.entities)
    assert any(slot.name == "game" for slot in parsed.missing_slots)


@pytest.mark.parametrize("question", ["1999 G4结果", "1999 G4 比赛结果"])
def test_bare_calendar_year_does_not_bind_current_fixture_game(question: str) -> None:
    """A bare year must constrain game resolution instead of using the G4 alias."""

    parsed = IntentParser().parse(question)

    assert parsed.intent.game_number == 4
    assert parsed.intent.season is not None
    assert parsed.intent.season.label == "1998-99"
    assert not any(item.kind is EntityKind.GAME for item in parsed.intent.entities)
    assert any(slot.name == "game" for slot in parsed.missing_slots)
    # Even direct planner callers must not turn the unresolved season/game
    # into a broad search that could return the newest fixture.
    assert QueryPlanner().build(parsed.intent) is None


def test_bare_fixture_ending_year_still_resolves_known_game() -> None:
    parsed = IntentParser().parse("2026 G4 比赛结果")

    assert parsed.intent.season is not None
    assert parsed.intent.season.label == "2025-26"
    assert any(
        item.kind is EntityKind.GAME and item.canonical_id == "2026-finals-g4"
        for item in parsed.intent.entities
    )
    assert not parsed.missing_slots


@pytest.mark.parametrize(
    "question",
    [
        "G4 谁命中了最后一投？",
        "G4 最后一次投篮是谁？",
        "G4 最后一攻是谁？",
    ],
)
def test_open_last_shot_questions_route_to_play_by_play(question: str) -> None:
    """Open event questions must not fall through to a box-score lookup."""

    parsed = IntentParser().parse(question)
    assert parsed.intent.intent_name is IntentName.PLAY_BY_PLAY
    assert any(item.kind is EntityKind.GAME for item in parsed.intent.entities)


def test_conversational_last_shot_reference_stays_play_by_play() -> None:
    context = ConversationContext(
        session_id=uuid4(),
        active_game=GAMES["2026-finals-g4"],
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
    )

    parsed = IntentParser().parse("刚才那个球是谁投的？", context)

    assert parsed.intent.intent_name is IntentName.PLAY_BY_PLAY
    assert any(metric.name == "last_shot_detail" for metric in parsed.intent.metrics)
    assert not parsed.missing_slots


def test_conversational_score_value_lookup_stays_data_intent() -> None:
    context = ConversationContext(
        session_id=uuid4(),
        active_game=GAMES["2026-finals-g4"],
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
    )

    parsed = IntentParser().parse("你刚才说谁拿了 32 分？", context)

    assert parsed.intent.intent_name is IntentName.DATA
    assert any(metric.name == "points" for metric in parsed.intent.metrics)


def test_negated_betting_prediction_is_in_scope_and_bounded() -> None:
    parsed = IntentParser().parse("不参与博彩，预测哪队赢")

    assert parsed.intent.intent_name is IntentName.TACTICAL
    assert parsed.intent.metrics[0].name == "game_outcome_prediction"
    assert QueryPlanner().build(parsed.intent) is None


def test_verified_data_tactical_wording_still_routes_to_model_analysis() -> None:
    """Evidence qualifiers must not turn a tactical question into fact-check."""

    parsed = IntentParser().parse(
        "请基于总决赛 G4 的已核验数据，分析凯尔特人限制雷霆挡拆的原因。"
    )

    assert parsed.intent.intent_name is IntentName.TACTICAL
    assert parsed.intent.category is Category.F


def test_historical_finals_calendar_year_maps_to_ending_season() -> None:
    parsed = IntentParser().parse("1999 年总决赛马刺打尼克斯，最后谁夺冠了？")

    assert parsed.intent.intent_name is IntentName.HISTORY
    assert parsed.intent.season is not None
    assert parsed.intent.season.label == "1998-99"
    team_ids = {
        item.canonical_id for item in parsed.intent.entities if item.kind is EntityKind.TEAM
    }
    assert {"sas", "nyk"} <= team_ids
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "get_history"
    assert plan.args[0].season_range is not None
    assert plan.args[0].season_range.start_inclusive.label == "1998-99"


@pytest.mark.parametrize(
    "question",
    [
        "谁会夺冠？",
        "凯尔特人能否夺冠？",
        "总冠军预测",
        "谁会是冠军？",
        "谁将是总冠军？",
        "哪队会是总冠军？",
        "今年总冠军是谁？",
        "本赛季总冠军是哪队？",
        "今年哪队夺冠？",
        "下赛季争冠热门是谁？",
    ],
)
def test_future_championship_wording_is_not_a_history_lookup(question: str) -> None:
    parsed = IntentParser().parse(question)

    assert parsed.intent.intent_name is IntentName.TACTICAL
    assert parsed.intent.metrics[0].name == "championship_prediction"
    assert QueryPlanner().build(parsed.intent) is None


@pytest.mark.parametrize(
    "question",
    [
        "2025-26 总冠军是谁？",
        "1999 年总决赛谁夺冠了？",
        "上赛季总冠军是谁？",
    ],
)
def test_explicit_or_past_championship_wording_remains_history(question: str) -> None:
    parsed = IntentParser().parse(question)

    assert parsed.intent.intent_name is IntentName.HISTORY
    assert parsed.intent.metrics[0].name != "championship_prediction"
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "get_history"
