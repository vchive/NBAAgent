"""Map parsed intents to typed provider operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.api.src.domain.models import (
    EntityKind,
    GameFilters,
    HistoryQuery,
    HistoryRecordType,
    IntentName,
    NewsQuery,
    QueryIntent,
    SeasonRange,
    StatScope,
    StatsQuery,
)


@dataclass(slots=True)
class QueryPlan:
    operation: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    @property
    def provider_method(self) -> str:
        return self.operation


class QueryPlanner:
    def build(self, intent: QueryIntent) -> QueryPlan | None:
        # Planning is a trust boundary as well as a convenience for the chat
        # state machine.  Callers may invoke the planner directly (for
        # example, an evaluation harness), so never broaden an unresolved
        # game/period/subject slot into a provider-wide search.  The use case
        # normally emits the clarification first; this guard prevents a
        # future caller from accidentally bypassing that invariant.
        if intent.missing_slots:
            return None
        entities = list(intent.entities)
        game = next((item for item in entities if item.kind is EntityKind.GAME), None)
        team = next((item for item in entities if item.kind is EntityKind.TEAM), None)
        player = next((item for item in entities if item.kind is EntityKind.PLAYER), None)
        metric_names = {str(getattr(metric, "name", "")).casefold() for metric in intent.metrics}
        if intent.intent_name in {IntentName.PLAY_BY_PLAY}:
            if game is None:
                return None
            return QueryPlan("get_play_by_play", (game.canonical_id,), description="读取逐回合事件")
        if intent.intent_name in {IntentName.FACT_CHECK, IntentName.TACTICAL, IntentName.RECAP}:
            # A future championship question is intentionally answered by the
            # application scope policy; it must never be satisfied by looking
            # up the latest historical champion (nor by selecting an unrelated
            # recent game as a proxy).
            if any(
                getattr(metric, "name", "")
                in {"championship_prediction", "game_outcome_prediction"}
                for metric in intent.metrics
            ):
                return None
            # Event-level fact checks (for example “最后一攻是不是某人投的”) need
            # the complete PBP bundle, not just a box-score summary.  The
            # parser keeps the claim predicates typed so this route remains
            # deterministic and does not broaden ordinary score corrections.
            if intent.intent_name is IntentName.FACT_CHECK and (
                intent.clock_window is not None
                or any(
                    getattr(claim, "predicate", "")
                    in {"last_shooter", "last_assister", "last_shot_type", "last_score_after"}
                    for claim in intent.premise_claims
                )
            ):
                if game is None:
                    return None
                return QueryPlan(
                    "get_play_by_play",
                    (game.canonical_id,),
                    description="读取逐回合事件并核验前提",
                )
            if game is None:
                # A team-only tactical question can use the most recent fixture
                # through a typed search; it never invokes a URL from user text.
                filters = GameFilters(
                    season=intent.season, team_ids=[team.canonical_id] if team else []
                )
                return QueryPlan("search_games", (filters,), description="查找相关比赛")
            return QueryPlan("get_game_summary", (game.canonical_id,), description="读取比赛摘要")
        # ``news`` is represented as a metric marker rather than a new intent
        # enum so the public category mapping remains backwards compatible.
        # Route it before the ordinary DATA/SCHEDULE/HISTORY branches: phrases
        # such as “总决赛赛后新闻” also contain generic game/history words.
        if metric_names & {"news", "background"} and intent.intent_name in {
            IntentName.DATA,
            IntentName.SCHEDULE_RESULT,
            IntentName.HISTORY,
            IntentName.FOLLOW_UP,
        }:
            subject_refs = [
                item
                for item in entities
                if item.kind in {EntityKind.TEAM, EntityKind.PLAYER, EntityKind.GAME}
            ]
            # De-duplicate references while preserving parser order.  Empty
            # subjects intentionally mean a broad news search; unlike a stats
            # lookup this is a valid, bounded query and must not be clarified.
            unique_refs = []
            seen_ids: set[str] = set()
            for ref in subject_refs:
                if ref.canonical_id not in seen_ids:
                    unique_refs.append(ref)
                    seen_ids.add(ref.canonical_id)
            query = NewsQuery(
                subject_refs=unique_refs,
                # Raw user text is not copied into provider keywords.  The
                # fixture/live adapters receive only typed subjects/date range;
                # this also prevents prompt-like news text from becoming a
                # provider instruction.
                keywords=[],
                date_range=intent.date_range,
                limit=10,
            )
            return QueryPlan("search_news", (query,), description="读取新闻背景")
        if intent.intent_name is IntentName.HISTORY:
            subject_refs = [team or player] if (team or player) else []
            # The parser distinguishes a latest champion lookup from a
            # franchise title-count question.  Keep that distinction at the
            # typed provider boundary instead of asking the renderer to infer
            # it from returned values (a title name and a numeric count have
            # different provenance and freshness semantics).
            metric = intent.metrics[0] if intent.metrics else None
            metric_name = getattr(metric, "name", "")
            if metric_name == "franchise_record":
                record_type = HistoryRecordType.FRANCHISE_RECORD
                limit = 1
            else:
                record_type = HistoryRecordType.CHAMPIONSHIP
                # ``unit=None`` is the parser's explicit marker for a latest
                # champion lookup (for example “最近一次总冠军是谁？”).
                # Broad “历届/各届” queries retain the default ``次`` unit
                # and receive the full bounded history.
                limit = 1 if getattr(metric, "unit", None) is None else 20
            season_range = (
                SeasonRange(start_inclusive=intent.season, end_inclusive=intent.season)
                if intent.season is not None
                else None
            )
            query = HistoryQuery(
                subject_refs=subject_refs,
                season_range=season_range,
                record_type=record_type,
                limit=limit,
            )
            return QueryPlan("get_history", (query,), description="读取历史纪录")
        if intent.intent_name is IntentName.SCHEDULE_RESULT:
            team_ids = [item.canonical_id for item in entities if item.kind is EntityKind.TEAM]
            metric = intent.metrics[0] if intent.metrics else None
            metric_scope = getattr(
                getattr(metric, "scope", None), "value", getattr(metric, "scope", None)
            )
            # A concrete game reference must remain authoritative.  Searching
            # the whole scoreboard here used to return the newest fixture even
            # when the user asked for G3/G2 and included the word “比赛”.
            # Series aggregates are the one exception: they intentionally fan
            # out to all games before deterministic derivation.
            if game is not None and metric_scope != StatScope.SERIES.value:
                return QueryPlan(
                    "get_game_summary", (game.canonical_id,), description="读取比赛摘要"
                )
            if (
                intent.metrics
                and getattr(intent.metrics[0], "name", "") == "rank"
                and intent.season is not None
            ):
                # Preserve the existing season-only provider contract while
                # carrying an optional conference scope to the gateway.  The
                # gateway performs the final typed filtering so legacy/custom
                # providers that only accept ``get_standings(season)`` remain
                # usable.
                kwargs = {"conference": intent.conference} if intent.conference is not None else {}
                return QueryPlan(
                    "get_standings",
                    (intent.season,),
                    kwargs=kwargs,
                    description="读取排名",
                )
            # Series questions need all games so Derivation can count wins.
            return QueryPlan(
                "search_games",
                (
                    GameFilters(
                        date_range=intent.date_range, season=intent.season, team_ids=team_ids
                    ),
                ),
                description="读取赛程与赛果",
            )
        if intent.intent_name is IntentName.DATA:
            if game is not None:
                return QueryPlan(
                    "get_game_summary", (game.canonical_id,), description="读取比赛数据"
                )
            if player is not None:
                if intent.season is not None:
                    query = StatsQuery(subject=player, scope=StatScope.SEASON, season=intent.season)
                else:
                    # Fixture provider can answer a player lookup from game
                    # leaders; a missing season is represented as a clarification
                    # by callers when no record is found.
                    query = StatsQuery(subject=player, scope=StatScope.CAREER)
                return QueryPlan("get_player_stats", (query,), description="读取球员统计")
            if team is not None:
                if intent.season is not None:
                    query = StatsQuery(subject=team, scope=StatScope.SEASON, season=intent.season)
                else:
                    query = StatsQuery(subject=team, scope=StatScope.CAREER)
                return QueryPlan("get_team_stats", (query,), description="读取球队统计")
            return None
        if intent.intent_name is IntentName.FOLLOW_UP:
            if game is not None:
                if intent.clock_window is not None:
                    return QueryPlan(
                        "get_play_by_play", (game.canonical_id,), description="读取追问的逐回合"
                    )
                return QueryPlan(
                    "get_game_summary", (game.canonical_id,), description="读取追问的比赛"
                )
            return None
        return None


def build_query_plan(intent: QueryIntent) -> QueryPlan | None:
    return QueryPlanner().build(intent)


__all__ = ["QueryPlan", "QueryPlanner", "build_query_plan"]
