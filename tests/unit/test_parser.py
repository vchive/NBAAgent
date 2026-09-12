"""Regression coverage for natural-language intent/time parsing."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from apps.api.src.application.parser import (
    GAMES,
    IntentParser,
    is_contextual_series_selection_question,
    is_inverse_series_selection_question,
    is_positive_series_selection_question,
    is_subjective_comparison_question,
)
from apps.api.src.application.query_planner import QueryPlanner
from apps.api.src.domain.models import (
    Category,
    ConversationContext,
    EntityKind,
    HistoryRecordType,
    IntentName,
    StatScope,
    TimeWindowScope,
    TurnSummary,
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


@pytest.mark.parametrize(
    "question",
    ["2026尼克斯-马刺", "尼克斯 vs 马刺", "尼克斯对马刺"],
)
def test_head_to_head_subject_is_not_single_team_stats_lookup(question: str) -> None:
    parsed = IntentParser().parse(question)
    assert parsed.intent.intent_name is IntentName.SCHEDULE_RESULT
    assert parsed.intent.matchup is True
    assert {
        item.canonical_id
        for item in parsed.intent.entities
        if item.kind is EntityKind.TEAM
    } == {
        "nyk",
        "sas",
    }
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_matchup"
    assert set(plan.args[0].team_ids) == {"nyk", "sas"}


def test_finals_series_result_uses_series_scope_without_narrowing_to_one_game() -> None:
    parsed = IntentParser(include_fixture_games=False).parse(
        "2026年尼克斯对马刺总决赛的大比分和每场赛果是什么？"
    )

    assert parsed.intent.intent_name is IntentName.SCHEDULE_RESULT
    assert parsed.intent.game_number is None
    assert parsed.intent.matchup is True
    assert all(metric.scope is StatScope.SERIES for metric in parsed.intent.metrics)


def test_numbered_matchup_premise_without_vs_is_still_a_complete_game_scope() -> None:
    parsed = IntentParser(include_fixture_games=False).parse(
        "朋友说2026总决赛G5是马刺94比90赢了尼克斯，实际谁赢？"
    )

    assert parsed.intent.game_number == 5
    assert parsed.intent.matchup is True
    assert not parsed.missing_slots
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_matchup"
    assert set(plan.args[0].team_ids) == {"nyk", "sas"}
    assert plan.args[0].series_game_number == 5


def test_head_to_head_stat_lookup_resolves_game_before_team_stats() -> None:
    parsed = IntentParser().parse("尼克斯对马刺谁得分最高？")
    plan = QueryPlanner().build(parsed.intent)
    assert parsed.intent.intent_name is IntentName.DATA
    assert plan is not None and plan.operation == "search_matchup"
    assert plan.kwargs["summary_if_match"] is True


def test_game_number_with_matchup_does_not_bind_unrelated_fixture_alias() -> None:
    """A generic G# alias must stay scoped to the two named teams."""

    parsed = IntentParser().parse("尼克斯对马刺 G2 谁赢了？")

    assert parsed.intent.game_number == 2
    assert parsed.intent.matchup is True
    assert not any(item.kind is EntityKind.GAME for item in parsed.intent.entities)
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_matchup"
    filters = plan.args[0]
    assert set(filters.team_ids) == {"nyk", "sas"}
    assert filters.series_game_number == 2


def test_contextual_game_number_stays_on_prior_matchup_when_fixture_lacks_series() -> None:
    """A failed series lookup must not make a later “为什么不是 G2” pick G2 of
    an unrelated built-in snapshot.
    """

    parser = IntentParser()
    matchup = parser.parse("2026尼克斯-马刺").intent
    context = ConversationContext(
        session_id=uuid4(),
        active_team=next(
            item for item in matchup.entities if item.canonical_id == "nyk"
        ),
        active_season=matchup.season,
        recent_turn_summaries=[
            TurnSummary(
                turn_index=1,
                user_intent=matchup.intent_name.value,
                user_message="2026尼克斯-马刺",
                active_refs=matchup.entities,
                text_summary="暂未找到公开比赛记录。",
            )
        ],
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
    )

    parsed = parser.parse("为什么不是G2？", context)

    assert parsed.intent.game_number == 2
    assert parsed.intent.matchup is True
    assert {
        item.canonical_id
        for item in parsed.intent.entities
        if item.kind is EntityKind.TEAM
    } == {"nyk", "sas"}
    assert not any(item.kind is EntityKind.GAME for item in parsed.intent.entities)
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_matchup"
    assert set(plan.args[0].team_ids) == {"nyk", "sas"}


@pytest.mark.parametrize(
    "question",
    [
        "你觉得最精华的是哪一场？",
        "哪场最精彩？",
        "推荐一场最好看的",
        "最值得回看的是哪场比赛？",
        "这轮最经典的是第几场？",
        "哪场最有观赏价值？",
        "挑一场最值得一看的",
        "哪一战最精彩？",
        "如果只能选一场你选哪场？",
        "哪场比分最接近？",
        "最焦灼的是哪一场？",
        "哪场最扣人心弦？",
        "含金量最高的是哪一战？",
        "整轮只看一场的话看哪场？",
        "哪场最过瘾？",
        "优先回看哪场？",
        "哪场最有悬念？",
        "哪一场最关键？",
        "最重要的是哪一战？",
        "你会选哪场？",
        "最好的是哪场比赛？",
        "最值得复盘的是哪一场？",
    ],
)
def test_subjective_series_selection_inherits_matchup_context(question: str) -> None:
    parser = IntentParser()
    matchup = parser.parse("2026尼克斯-马刺").intent
    context = ConversationContext(
        session_id=uuid4(),
        active_team=next(
            item for item in matchup.entities if item.canonical_id == "nyk"
        ),
        active_season=matchup.season,
        recent_turn_summaries=[
            TurnSummary(
                turn_index=1,
                user_intent=matchup.intent_name.value,
                user_message="2026尼克斯-马刺",
                active_refs=matchup.entities,
                text_summary="尼克斯以 4–1 赢下系列赛。",
            )
        ],
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
    )

    assert is_contextual_series_selection_question(question)
    parsed = parser.parse(question, context)

    assert parsed.intent.intent_name is IntentName.RECAP
    assert parsed.intent.matchup is True
    assert parsed.intent.season == matchup.season
    assert not parsed.missing_slots
    assert {
        item.canonical_id
        for item in parsed.intent.entities
        if item.kind is EntityKind.TEAM
    } == {"nyk", "sas"}
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_matchup"


@pytest.mark.parametrize(
    "question",
    [
        "哪场最不精彩？",
        "不推荐哪场？",
        "哪一场最不值得看？",
    ],
)
def test_inverse_series_selection_does_not_use_best_game_recovery(
    question: str,
) -> None:
    parser = IntentParser()
    matchup = parser.parse("2026尼克斯-马刺").intent
    context = ConversationContext(
        session_id=uuid4(),
        active_game={
            "kind": "GAME",
            "canonical_id": "hupu:168858",
            "display_name": "2025-26 总决赛 G4",
        },
        active_team=next(
            item for item in matchup.entities if item.canonical_id == "nyk"
        ),
        active_season=matchup.season,
        recent_turn_summaries=[
            TurnSummary(
                turn_index=1,
                user_intent=matchup.intent_name.value,
                user_message="2026尼克斯-马刺",
                active_refs=matchup.entities,
                text_summary="尼克斯以 4–1 赢下系列赛。",
            )
        ],
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
    )

    assert is_contextual_series_selection_question(question)
    assert is_inverse_series_selection_question(question)
    assert not is_positive_series_selection_question(question)

    parsed = parser.parse(question, context)
    assert parsed.intent.intent_name is IntentName.RECAP
    assert parsed.intent.matchup is True
    assert parsed.intent.season == matchup.season
    assert {
        item.canonical_id
        for item in parsed.intent.entities
        if item.kind is EntityKind.TEAM
    } == {"nyk", "sas"}
    # The prior G4 is a recommended item, not the scope of an inverse choice.
    assert not any(item.kind is EntityKind.GAME for item in parsed.intent.entities)
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_matchup"
    assert plan.args[0].series_game_number is None


def _recommended_finals_context() -> tuple[ConversationContext, object]:
    parser = IntentParser()
    matchup = parser.parse("2026尼克斯-马刺").intent
    recommended_game = {
        "kind": "GAME",
        "canonical_id": "hupu:168858",
        "display_name": "2025-26 总决赛 G4",
        "aliases": ["G4", "第四场"],
    }
    context = ConversationContext(
        session_id=uuid4(),
        active_game=recommended_game,
        active_team=next(
            item for item in matchup.entities if item.canonical_id == "nyk"
        ),
        active_season=matchup.season,
        recent_turn_summaries=[
            TurnSummary(
                turn_index=1,
                user_intent=matchup.intent_name.value,
                user_message="2026尼克斯-马刺",
                active_refs=matchup.entities,
                text_summary="尼克斯以 4–1 赢下系列赛。",
            ),
            TurnSummary(
                turn_index=2,
                user_intent=IntentName.RECAP.value,
                user_message="你觉得最精华的是哪一场？",
                active_refs=[*matchup.entities, recommended_game],
                text_summary="推荐 G4。",
            ),
        ],
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
    )
    return context, matchup


@pytest.mark.parametrize(
    "question",
    ["G2那场怎么打的？", "那G2呢？", "第二场怎么样？"],
)
def test_explicit_series_game_followup_overrides_recommended_active_game(
    question: str,
) -> None:
    context, matchup = _recommended_finals_context()

    parsed = IntentParser().parse(question, context)

    assert parsed.intent.game_number == 2
    assert parsed.intent.matchup is True
    assert parsed.intent.season == matchup.season
    assert {
        item.canonical_id
        for item in parsed.intent.entities
        if item.kind is EntityKind.TEAM
    } == {"nyk", "sas"}
    assert not any(item.kind is EntityKind.GAME for item in parsed.intent.entities)
    assert not parsed.missing_slots
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_matchup"
    assert set(plan.args[0].team_ids) == {"nyk", "sas"}
    assert plan.args[0].series_game_number == 2


def test_recommendation_reason_inherits_chosen_game_and_series() -> None:
    context, matchup = _recommended_finals_context()

    parsed = IntentParser().parse("为什么选它？", context)

    assert is_positive_series_selection_question("为什么选它？")
    assert parsed.intent.intent_name is IntentName.RECAP
    assert parsed.intent.matchup is True
    assert parsed.intent.season == matchup.season
    assert any(
        item.kind is EntityKind.GAME and item.canonical_id == "hupu:168858"
        for item in parsed.intent.entities
    )
    assert {
        item.canonical_id
        for item in parsed.intent.entities
        if item.kind is EntityKind.TEAM
    } == {"nyk", "sas"}
    assert not parsed.missing_slots


def test_recommendation_comparison_keeps_series_and_explicit_g_number() -> None:
    context, matchup = _recommended_finals_context()

    parsed = IntentParser().parse("它比G2好在哪？", context)

    assert is_positive_series_selection_question("它比G2好在哪？")
    assert parsed.intent.intent_name is IntentName.RECAP
    assert parsed.intent.game_number == 2
    assert parsed.intent.matchup is True
    assert parsed.intent.season == matchup.season
    assert not any(item.kind is EntityKind.GAME for item in parsed.intent.entities)
    assert {
        item.canonical_id
        for item in parsed.intent.entities
        if item.kind is EntityKind.TEAM
    } == {"nyk", "sas"}
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_matchup"
    assert plan.args[0].series_game_number == 2


@pytest.mark.parametrize(
    "question",
    ["这场咋赢的？", "这场发生了啥？", "这场看点在哪？"],
)
def test_colloquial_recap_inherits_active_game(question: str) -> None:
    context, _matchup = _recommended_finals_context()

    parsed = IntentParser().parse(question, context)

    assert parsed.intent.intent_name is IntentName.RECAP
    assert any(
        item.kind is EntityKind.GAME and item.canonical_id == "hupu:168858"
        for item in parsed.intent.entities
    )
    assert not parsed.missing_slots


@pytest.mark.parametrize(
    "question,players",
    [
        ("乔丹和詹姆斯谁更伟大？请说出判断依据", {"michael-jordan", "lebron-james"}),
        ("文班亚马和邓肯谁更伟大？", {"victor-wembanyama", "tim-duncan"}),
        ("库里和杜兰特谁更强？", {"stephen-curry", "kevin-durant"}),
    ],
)
def test_subjective_player_comparison_is_open_analysis(
    question: str, players: set[str]
) -> None:
    assert is_subjective_comparison_question(question)

    parsed = IntentParser().parse(question)

    assert parsed.intent.intent_name is IntentName.RECAP
    assert not parsed.missing_slots
    assert {
        item.canonical_id
        for item in parsed.intent.entities
        if item.kind is EntityKind.PLAYER
    } == players


def test_dated_recap_inherits_both_teams_from_previous_matchup() -> None:
    parser = IntentParser()
    matchup = parser.parse("2026尼克斯-马刺").intent
    context = ConversationContext(
        session_id=uuid4(),
        active_team=next(
            item for item in matchup.entities if item.canonical_id == "nyk"
        ),
        active_season=matchup.season,
        recent_turn_summaries=[
            TurnSummary(
                turn_index=1,
                user_intent=matchup.intent_name.value,
                user_message="2026尼克斯-马刺",
                active_refs=matchup.entities,
                text_summary="两队总决赛交手记录。",
            )
        ],
        expires_at_utc=datetime.now(UTC) + timedelta(hours=1),
    )

    parsed = parser.parse(
        "2026-06-14 08:30 这场比赛是怎么个过程，能给我讲讲吗",
        context,
    )

    assert parsed.intent.intent_name is IntentName.RECAP
    assert parsed.intent.matchup is True
    assert not parsed.missing_slots
    assert {
        item.canonical_id
        for item in parsed.intent.entities
        if item.kind is EntityKind.TEAM
    } == {"nyk", "sas"}
    assert parsed.intent.date_range == local_date_range(date(2026, 6, 14))
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_matchup"
    assert plan.kwargs["summary_if_match"] is True


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


def test_unknown_fixture_matchup_game_number_becomes_structured_filter() -> None:
    parsed = IntentParser().parse("2026尼克斯-马刺 G5 谁赢了？")

    assert parsed.intent.game_number == 5
    assert parsed.intent.matchup is True
    assert not any(slot.name == "game" for slot in parsed.missing_slots)
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None
    assert plan.operation == "search_matchup"
    assert plan.args[0].series_game_number == 5


def test_public_parser_never_binds_matchup_game_number_to_demo_fixture() -> None:
    parsed = IntentParser(include_fixture_games=False).parse(
        "2026尼克斯-马刺 G4 谁赢了？"
    )

    assert parsed.intent.game_number == 4
    assert parsed.intent.matchup is True
    assert not any(
        item.kind is EntityKind.GAME for item in parsed.intent.entities
    )
    assert not parsed.missing_slots
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "search_matchup"
    assert plan.args[0].series_game_number == 4


def test_public_parser_requires_series_scope_for_bare_game_number() -> None:
    parsed = IntentParser(include_fixture_games=False).parse("2026 G4 比赛结果")

    assert parsed.intent.game_number == 4
    assert not any(item.kind is EntityKind.GAME for item in parsed.intent.entities)
    assert any(slot.name == "game" for slot in parsed.missing_slots)
    assert QueryPlanner().build(parsed.intent) is None


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
    question = "最近一场比赛的关键回合是什么？"
    assert not is_contextual_series_selection_question(question)
    parsed = IntentParser().parse(question)
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
    parsed = IntentParser().parse("雷霆为什么能赢下这场比赛？", context)

    assert parsed.intent.intent_name is IntentName.TACTICAL
    assert any(
        item.kind is EntityKind.GAME and item.canonical_id == "2026-finals-g4"
        for item in parsed.intent.entities
    )
    assert len(parsed.intent.premise_claims) == 1
    assert parsed.intent.premise_claims[0].predicate == "winner"
    assert parsed.intent.premise_claims[0].claimed_value == "雷霆"


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


def test_game_coach_question_is_typed_as_coach_metadata() -> None:
    context = ConversationContext(
        session_id=uuid4(),
        active_game={
            "kind": "GAME",
            "canonical_id": "2026-finals-g4",
            "display_name": "2025-26 总决赛 G4",
        },
        expires_at_utc=datetime(2026, 8, 28, tzinfo=UTC),
    )
    parsed = IntentParser().parse("这场比赛双方教练都是谁？", context)

    assert parsed.intent.metrics[0].name == "coaches"
    assert parsed.intent.intent_name is IntentName.DATA
    assert not parsed.missing_slots
    plan = QueryPlanner().build(parsed.intent)
    assert plan is not None and plan.operation == "get_game_summary"


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
