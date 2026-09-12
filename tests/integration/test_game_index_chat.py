from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.application.parser import PLAYERS, TEAMS
from apps.api.src.application.ports import ProviderResult, RuntimeStatus
from apps.api.src.config import Settings
from apps.api.src.domain.models import (
    AnswerBlockType,
    ChatRequest,
    Evidence,
    Freshness,
    Game,
    GameBundle,
    GameFilters,
    GameStatus,
    NewsItem,
    PlayByPlayBundle,
    PlayEvent,
    PlayEventType,
    SeasonLabel,
    ShotType,
    SourceClass,
    StatLine,
    StatScope,
    TrustLevel,
)
from apps.api.src.infrastructure.agent_tools import AgentToolCall
from apps.api.src.infrastructure.game_index import GameIndex
from apps.api.src.infrastructure.hermes_agent_runtime import AgentTurnResult
from apps.api.src.providers.indexed_provider import IndexedProvider


def _team(team_id: str):
    return next(team for team in TEAMS if team.canonical_id == team_id)


def _player(player_id: str):
    return next(player for player in PLAYERS if player.canonical_id == player_id)


class EmptyPrimary:
    calls = 0

    async def search_games(self, _filters, _budget):
        self.calls += 1
        return ProviderResult(data=[], evidence=[], retrieved_at_utc=datetime.now(UTC))


class UnavailableAgent:
    """Force the full-intelligence path to prove its scoped typed recovery."""

    mode = "embedded_agent"
    model = "test-model"

    async def run(self, turn, *, tool_runner, cancel):
        return AgentTurnResult(
            status=RuntimeStatus.TIMEOUT,
            finish_reason="timeout",
            latency_ms=2,
        )


class SearchChoosingAgent:
    """Model double that owns typed lookup and recap-search composition."""

    mode = "embedded_agent"
    model = "test-model"

    async def run(self, turn, *, tool_runner, cancel):
        structured = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        observations = [structured]
        calls = [AgentToolCall("nba_query", "query", structured["status"], 1)]
        answer = structured["answer_markdown"]
        if "过程" in turn.sanitized_question or "讲讲" in turn.sanitized_question:
            searched = dict(
                await tool_runner(
                    "nba_search",
                    {"query": "2026-06-14 尼克斯 马刺 G5 比赛过程"},
                )
            )
            observations.append(searched)
            calls.append(AgentToolCall("nba_search", "search", searched["status"], 1))
            answer = (
                f"{answer}\n\n**分析**\n\n"
                "公开报道补充的过程线索是：双方鏖战到末节，"
                "尼克斯依靠收官阶段的防守守住优势。"
            )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=answer,
            evidence_state=structured["evidence_state"],
            observations=observations,
            tool_calls=calls,
            latency_ms=2,
            iteration_count=1 + len(calls),
        )


class BridgeShapedQueryAgent:
    """Keep a resolved game only in the server-side observation copy."""

    mode = "embedded_agent"
    model = "test-model"

    async def run(self, turn, *, tool_runner, cancel):
        raw = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        raw_scope = raw.get("query_scope") or {}
        observation = {**raw, "query_scope": None}
        if raw_scope.get("game_id"):
            observation["_resolved_game_id"] = raw_scope["game_id"]
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=raw["answer_markdown"],
            evidence_state=raw["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall("nba_query", "query", raw["status"], 1)
            ],
            latency_ms=2,
            iteration_count=2,
        )


class PassThroughQueryAgent:
    """Expose the real nested ``nba_query`` answer without rewriting it."""

    mode = "embedded_agent"
    model = "test-model"

    def __init__(self) -> None:
        self.observations = []

    async def run(self, turn, *, tool_runner, cancel):
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        self.observations.append(observation)
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=observation["answer_markdown"],
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall("nba_query", "selected-game", observation["status"], 1)
            ],
            latency_ms=2,
            iteration_count=2,
        )


class NaturalTwoTurnAgent:
    """Return distinctive completed prose for the exact acceptance dialogue."""

    mode = "embedded_agent"
    model = "test-model"

    def __init__(self) -> None:
        self.turns = []

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        structured = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        observations = [structured]
        calls = [AgentToolCall("nba_query", "query", structured["status"], 1)]
        if len(self.turns) == 1:
            answer = (
                "这轮对决的结论很清楚：尼克斯以 4–1 赢下系列赛，"
                "整体优势最终转化成了冠军。"
            )
        else:
            searched = dict(
                await tool_runner(
                    "nba_search",
                    {"query": "2026-06-14 尼克斯 马刺 G5 比赛过程"},
                )
            )
            observations.append(searched)
            calls.append(AgentToolCall("nba_search", "search", searched["status"], 1))
            answer = (
                "尼克斯赢了最近一场，终场是 94–90。"
                "从比赛过程看，双方鏖战到末节，尼克斯依靠收官阶段的防守守住优势。"
            )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=answer,
            evidence_state=(
                observations[-1]["evidence_state"]
                if len(observations) > 1
                else structured["evidence_state"]
            ),
            observations=observations,
            tool_calls=calls,
            latency_ms=2,
            iteration_count=1 + len(calls),
        )


class SeriesRecommendationAgent:
    """Exercise the exact contextual recommendation journey through nba_query."""

    mode = "embedded_agent"
    model = "test-model"

    def __init__(self) -> None:
        self.turns = []
        self.observations = []

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        self.observations.append(observation)
        if len(self.turns) == 1:
            answer = "尼克斯以 4–1 击败马刺，赢下这轮系列赛。"
        else:
            answer = (
                "如果按比赛胶着程度和系列赛转折，我会选 **G4**。"
                "尼克斯以 **107–106** 击败马刺，这是五场总决赛中分差最小的一场；"
                "而且他们在 G3 失利后马上拿下 G4，随后只差一胜夺冠。"
            )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=answer,
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall("nba_query", "query", observation["status"], 1)
            ],
            latency_ms=2,
            iteration_count=2,
        )


class SeriesRecommendationFailureAgent(SeriesRecommendationAgent):
    """Lose the final synthesis only after the trusted series lookup completes."""

    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        self.observations.append(observation)
        if len(self.turns) == 1:
            return AgentTurnResult(
                status=RuntimeStatus.OK,
                answer_markdown="尼克斯以 4–1 击败马刺，赢下这轮系列赛。",
                evidence_state=observation["evidence_state"],
                observations=[observation],
                tool_calls=[
                    AgentToolCall("nba_query", "query", observation["status"], 1)
                ],
                latency_ms=2,
                iteration_count=2,
            )
        if self.failure == "timeout":
            return AgentTurnResult(
                status=RuntimeStatus.TIMEOUT,
                finish_reason="timeout_after_tool",
                evidence_state=observation["evidence_state"],
                observations=[observation],
                tool_calls=[
                    AgentToolCall("nba_query", "query", observation["status"], 1)
                ],
                latency_ms=2,
                iteration_count=4,
            )
        if self.failure == "candidate_list":
            return AgentTurnResult(
                status=RuntimeStatus.OK,
                answer_markdown=observation["answer_markdown"],
                evidence_state=observation["evidence_state"],
                observations=[observation],
                tool_calls=[
                    AgentToolCall("nba_query", "query", observation["status"], 1)
                ],
                latency_ms=2,
                iteration_count=2,
            )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown="请补充查询对象，我再帮您核对。",
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall("nba_query", "query", observation["status"], 1)
            ],
            latency_ms=2,
            iteration_count=2,
        )


class NoToolSeriesRecommendationAgent(SeriesRecommendationAgent):
    """Answer the recommendation from history without voluntarily querying."""

    def __init__(self, answer: str) -> None:
        super().__init__()
        self.answer = answer

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        if len(self.turns) == 1:
            observation = dict(
                await tool_runner("nba_query", {"question": turn.sanitized_question})
            )
            self.observations.append(observation)
            return AgentTurnResult(
                status=RuntimeStatus.OK,
                answer_markdown="尼克斯以 4–1 击败马刺，赢下这轮系列赛。",
                evidence_state=observation["evidence_state"],
                observations=[observation],
                tool_calls=[
                    AgentToolCall("nba_query", "query", observation["status"], 1)
                ],
                latency_ms=2,
                iteration_count=2,
            )
        # Deliberately return no observations/calls.  The application must
        # perform a controlled candidate lookup before publishing this answer.
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=self.answer,
            latency_ms=1,
            iteration_count=1,
        )


class RecommendationMemoryAgent(SeriesRecommendationAgent):
    """Exercise a recommendation, a counterfactual, and a game-level pronoun."""

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        self.observations.append(observation)
        if len(self.turns) == 1:
            answer = "尼克斯以 4–1 击败马刺，赢下这轮系列赛。"
        elif len(self.turns) == 2:
            answer = (
                "按胶着程度和系列赛转折，我推荐 **G4**。"
                "它与 G2 都只有 1 分分差；尼克斯在 G3 失利后拿下 G4，"
                "把系列赛带到 3–1。"
            )
        elif len(self.turns) == 3:
            answer = (
                "G2 同样只有 1 分分差，但 G4 紧接 G3 的失利，"
                "并把系列赛推进到 3–1，所以按转折意义我仍选 G4。"
            )
        else:
            answer = observation["answer_markdown"]
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=answer,
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall("nba_query", "query", observation["status"], 1)
            ],
            latency_ms=2,
            iteration_count=2,
        )


class ExactG2RecommendationChallengeAgent(SeriesRecommendationAgent):
    """Reproduce the live G2 choice followed by an erroneous G4 switch."""

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        self.observations.append(observation)
        turn_number = len(self.turns)
        if turn_number == 1:
            answer = "尼克斯以 4–1 击败马刺，赢下这轮系列赛。"
        elif turn_number == 2:
            answer = (
                "我从系列赛的比分来看，**最精华的应当是 G2**。\n\n"
                "**评价口径：** 以单场悬念和胜负悬念为依据，选取分差最小、"
                "过程最胶着的场次。\n\n"
                "**理由：**\n"
                "- **分差最小、最具悬念**：G2 尼克斯以 **105–104** 仅胜 1 分，"
                "是系列赛中比分最接近的一场。\n"
                "- **系列赛意义**：作为主场连续第 2 场险胜，把系列赛推向 2–0。\n\n"
                "G4 同样只差 1 分（**107–106**）；但结合关键节点，我更推荐 **G2**。"
            )
        elif turn_number == 3:
            # This answer is internally factual, but it contradicts the
            # already-published G2 choice and the user's premise that G2 was
            # supposedly not selected.
            answer = (
                "G2 同样只有 1 分分差，但我仍推荐 G4，因为 G4 紧接 G3 的失利，"
                "并把系列赛推进到 3–1。"
            )
        else:
            answer = observation["answer_markdown"]
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=answer,
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall("nba_query", "query", observation["status"], 1)
            ],
            latency_ms=2,
            iteration_count=2,
        )


class PartialSeriesIndexedProvider(IndexedProvider):
    """Mark series searches partial while retaining public structured rows."""

    async def search_games(self, filters, budget):
        result = await super().search_games(filters, budget)
        return result.model_copy(update={"partial": True})


class PlayByPlayPrimary(EmptyPrimary):
    def __init__(self) -> None:
        self.game_ids = []

    async def get_play_by_play(self, game_id, _budget):
        self.game_ids.append(game_id)
        now = datetime.now(UTC)
        home_score, away_score = {
            "hupu:168855": (95, 105),
            "hupu:168856": (104, 105),
            "hupu:168857": (111, 115),
            "hupu:168858": (107, 106),
            "hupu:168859": (90, 94),
        }.get(game_id, (90, 94))
        evidence = Evidence(
            evidence_id=f"public:pbp:{game_id}",
            source_class=SourceClass.ESTABLISHED_SPORTS,
            source_ref="public:pbp",
            url="https://example.com/game/pbp",
            fetched_at_utc=now,
            trust=TrustLevel.MEDIUM,
            freshness=Freshness.FRESH,
        )
        event = PlayEvent(
            event_id=f"{game_id}:last-minute",
            game_id=game_id,
            provider_index=1,
            period=4,
            clock_seconds_remaining=42,
            event_type=PlayEventType.SHOT,
            shooter=_player("jalen-brunson"),
            shot_type=ShotType.TWO_POINT,
            points=2,
            home_score_after=home_score,
            away_score_after=away_score,
        )
        return ProviderResult(
            data=PlayByPlayBundle(game_id=game_id, events=[event]),
            evidence=[evidence],
            retrieved_at_utc=now,
        )


class G5ClosingPlayPrimary(EmptyPrimary):
    """Return the real home-away score shape used by the indexed G5 replay."""

    async def get_play_by_play(self, game_id, _budget):
        now = datetime.now(UTC)
        evidence = Evidence(
            evidence_id=f"public:pbp:{game_id}",
            source_class=SourceClass.ESTABLISHED_SPORTS,
            source_ref="public:pbp",
            url="https://example.com/game/pbp",
            fetched_at_utc=now,
            trust=TrustLevel.MEDIUM,
            freshness=Freshness.FRESH,
        )
        event = PlayEvent(
            event_id=f"{game_id}:last-shot",
            game_id=game_id,
            provider_index=1,
            period=4,
            clock_seconds_remaining=2,
            event_type=PlayEventType.SHOT,
            shooter=_player("victor-wembanyama"),
            shot_type=ShotType.THREE_POINT,
            home_score_after=90,
            away_score_after=94,
            action_text="维克托·文班亚马三分跳投不中",
        )
        return ProviderResult(
            data=PlayByPlayBundle(game_id=game_id, events=[event]),
            evidence=[evidence],
            retrieved_at_utc=now,
        )


class WrongGameSearchPrimary(PlayByPlayPrimary):
    """Expose a plausible G5 search result to catch cross-game drift."""

    def __init__(self) -> None:
        super().__init__()
        self.search_calls = 0

    async def search_news(self, _query, _budget):
        self.search_calls += 1
        now = datetime.now(UTC)
        evidence = Evidence(
            evidence_id="search:g5-drift",
            source_class=SourceClass.SEARCH,
            source_ref="search:g5-drift",
            url="https://example.com/finals/g5",
            fetched_at_utc=now,
            trust=TrustLevel.MEDIUM,
            freshness=Freshness.UNKNOWN,
        )
        return ProviderResult(
            data=[
                NewsItem(
                    news_id="g5-drift",
                    title="尼克斯马刺 G5 收官战",
                    summary="G5 尼克斯以 94–90 取胜，布伦森得到 45 分。",
                    evidence_id=evidence.evidence_id,
                )
            ],
            evidence=[evidence],
            partial=True,
            retrieved_at_utc=now,
        )


class WrongToolRecommendationMemoryAgent(SeriesRecommendationAgent):
    """Choose tempting but semantically wrong tools throughout the journey."""

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        turn_number = len(self.turns)
        if turn_number == 1:
            tool_name = "nba_query"
            arguments = {"question": turn.sanitized_question}
            answer = "尼克斯以 4–1 击败马刺，赢下这轮系列赛。"
        elif turn_number == 2:
            tool_name = "nba_search"
            arguments = {"query": "2026 尼克斯 马刺 最精彩比赛"}
            answer = (
                "按胶着程度和系列赛转折，我推荐 **G4**。"
                "它与 G2 都只有 1 分分差，尼克斯赢下 G4 后把系列赛带到 3–1。"
            )
        elif turn_number == 3:
            tool_name = "nba_schedule"
            arguments = {"date_expression": "2026-06-11"}
            answer = (
                "G2 同样只有 1 分分差，但 G4 紧接 G3 的失利，"
                "并把系列赛推进到 3–1，所以按转折意义我仍推荐 G4。"
            )
        else:
            tool_name = "nba_search"
            arguments = {"query": "2026 尼克斯 马刺 G5 最后一分钟"}
            # Deliberately answer from another game.  The application must
            # bind this event-level follow-up to the trusted G4 PBP instead.
            answer = "G5 最后一分钟尼克斯守住优势，以 94–90 取胜。"
        observation = dict(await tool_runner(tool_name, arguments))
        self.observations.append(observation)
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=answer,
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall(tool_name, "wrong_tool_probe", observation["status"], 1)
            ],
            latency_ms=2,
            iteration_count=turn.max_iterations,
        )


class ZeroToolFactFollowUpAgent(SeriesRecommendationAgent):
    """Reuse history without tools, matching the real model's failure shape."""

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        turn_number = len(self.turns)
        if turn_number <= 2:
            observation = dict(
                await tool_runner("nba_query", {"question": turn.sanitized_question})
            )
            self.observations.append(observation)
            answer = (
                "尼克斯以 4–1 击败马刺，赢下这轮系列赛。"
                if turn_number == 1
                else (
                    "按比赛悬念，我推荐 **G2**：尼克斯以 105–104 取胜，"
                    "这是只有 1 分分差的胶着比赛。"
                )
            )
            return AgentTurnResult(
                status=RuntimeStatus.OK,
                answer_markdown=answer,
                evidence_state=observation["evidence_state"],
                observations=[observation],
                tool_calls=[
                    AgentToolCall("nba_query", "query", observation["status"], 1)
                ],
                latency_ms=2,
                iteration_count=2,
            )
        # The model relies only on transcript history for both follow-ups.  In
        # particular, the final answer incorrectly claims that G2 has no PBP.
        if turn_number == 3:
            answer = "我推荐 G2，按悬念和分差，它只有 1 分分差。"
        elif turn_number == 4:
            answer = "G2 当前没有逐回合记录，因此无法还原最后一分钟。"
        else:
            answer = "这场是尼克斯对马刺。"
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=answer,
            evidence_state="none",
            observations=[],
            tool_calls=[],
            latency_ms=2,
            iteration_count=1,
        )


class BoundedPbpRecommendationAgent(SeriesRecommendationAgent):
    """Recommend a chosen game, then exercise the supplied per-turn budget."""

    def __init__(self, game_number: int, *, invalid_facts: bool = False) -> None:
        super().__init__()
        self.game_number = game_number
        self.invalid_facts = invalid_facts
        self.pbp_attempts = 0

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        turn_number = len(self.turns)
        if turn_number <= 2:
            observation = dict(
                await tool_runner("nba_query", {"question": turn.sanitized_question})
            )
            self.observations.append(observation)
            if turn_number == 1:
                answer = "尼克斯以 4–1 击败马刺，赢下这轮系列赛。"
            elif self.invalid_facts:
                answer = (
                    "按分差和主客场因素，我推荐 **G3**：它是全系列分差最小的一场，"
                    "马刺在主场以 115–111 取胜；相比之下，G2 和 G4 的分差都更大。"
                )
            else:
                facts = {
                    2: "尼克斯以 105–104 取胜，分差 1 分",
                    3: "马刺客场以 115–111 取胜，分差 4 分",
                    4: "尼克斯以 107–106 取胜，分差 1 分",
                }[self.game_number]
                answer = (
                    f"按我更看重的比赛节点，我推荐 **G{self.game_number}**。"
                    f"{facts}；这是主观取舍，不等同于分差排名。"
                )
            return AgentTurnResult(
                status=RuntimeStatus.OK,
                answer_markdown=answer,
                evidence_state=observation["evidence_state"],
                observations=[observation],
                tool_calls=[
                    AgentToolCall("nba_query", "query", observation["status"], 1)
                ],
                latency_ms=2,
                iteration_count=2,
            )

        observations = []
        calls = []
        # A production model can produce a new paraphrase on each iteration,
        # so duplicate-argument protection alone is insufficient.  Exercise
        # every call the application budget says is available.
        for attempt in range(turn.max_tool_calls):
            self.pbp_attempts += 1
            observation = dict(
                await tool_runner(
                    "nba_query",
                    {"question": f"{turn.sanitized_question}（核对 {attempt + 1}）"},
                )
            )
            observations.append(observation)
            calls.append(
                AgentToolCall(
                    "nba_query",
                    f"pbp-{attempt + 1}",
                    observation["status"],
                    1,
                )
            )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=observations[-1]["answer_markdown"],
            evidence_state=observations[-1]["evidence_state"],
            observations=observations,
            tool_calls=calls,
            latency_ms=2,
            iteration_count=turn.max_iterations,
        )


class RecapSearchPrimary(EmptyPrimary):
    async def search_news(self, _query, _budget):
        now = datetime.now(UTC)
        evidence = Evidence(
            evidence_id="search:g5-recap",
            source_class=SourceClass.SEARCH,
            source_ref="search:g5-recap",
            url="https://example.com/g5-recap",
            fetched_at_utc=now,
            trust=TrustLevel.MEDIUM,
            freshness=Freshness.UNKNOWN,
        )
        return ProviderResult(
            data=[
                NewsItem(
                    news_id="g5-process",
                    title="尼克斯马刺G5比赛过程",
                    summary="尼克斯与马刺鏖战到末节，尼克斯依靠收官阶段的防守守住优势。",
                    evidence_id=evidence.evidence_id,
                ),
                NewsItem(
                    news_id="offseason-roster",
                    title="尼克斯夺冠后的阵容调整",
                    summary="尼克斯与马刺系列赛结束后的休赛期补强讨论。",
                    evidence_id=evidence.evidence_id,
                ),
            ],
            evidence=[evidence],
            partial=True,
            retrieved_at_utc=now,
        )
def _seed_finals(index: GameIndex) -> None:
    season = SeasonLabel(start_year=2025, end_year=2026, label="2025-26")
    evidence = Evidence(
        evidence_id="hupu:schedule:finals",
        source_class=SourceClass.ESTABLISHED_SPORTS,
        source_ref="hupu:schedule",
        url="https://nba.hupu.com/schedule/knicks",
        fetched_at_utc=datetime(2026, 9, 4, tzinfo=UTC),
        trust=TrustLevel.MEDIUM,
        freshness=Freshness.FRESH,
    )
    rows = [
        ("168855", 4, "nyk", "sas", 105, 95),
        ("168856", 6, "nyk", "sas", 105, 104),
        ("168857", 9, "sas", "nyk", 115, 111),
        ("168858", 11, "sas", "nyk", 106, 107),
        ("168859", 14, "nyk", "sas", 94, 90),
    ]
    for game_number, (source_id, day, away_id, home_id, away_score, home_score) in enumerate(
        rows, start=1
    ):
        game = Game(
            game_id=f"hupu:{source_id}",
            season=season,
            start_utc=datetime(2026, 6, day, 0, 30, tzinfo=UTC),
            away=_team(away_id),
            home=_team(home_id),
            away_score=away_score,
            home_score=home_score,
            status=GameStatus.FINAL,
            series_id="2025-26-finals-nyk-sas",
            series_game_number=game_number,
            duration_seconds=9_960 if game_number == 5 else None,
        )
        stats = (
            [
                StatLine(
                    subject=_player("jalen-brunson"),
                    game_id=game.game_id,
                    scope=StatScope.GAME,
                    metrics={"points": 45, "rebounds": 3, "assists": 3},
                    evidence_ids=[evidence.evidence_id],
                )
            ]
            if game_number == 5
            else []
        )
        assert (
            index.upsert_bundle(
                GameBundle(game=game, stat_lines=stats),
                [evidence],
                origin="public",
            )
            == "inserted"
        )


@pytest.mark.asyncio
async def test_roaming_matchup_reads_five_indexed_games_after_restart(tmp_path) -> None:
    path = tmp_path / "index.sqlite3"
    first = GameIndex(path)
    _seed_finals(first)
    first.close()

    reopened = GameIndex(path)
    primary = EmptyPrimary()
    usecase = ChatUseCase(
        IndexedProvider(primary, reopened),
        settings=Settings(default_intelligence_mode="hybrid"),
    )
    result = await usecase.handle(
        ChatRequest(message="2026尼克斯-马刺", intelligence_mode="hybrid")
    )
    assert result.status == "completed"
    tables = [block for block in result.blocks if block.type is AnswerBlockType.TABLE]
    assert tables
    assert len(tables[0].rows) == 5
    assert any("94–90" in str(cell) for row in tables[0].rows for cell in row)
    assert "系列赛大比分" in result.answer_markdown
    assert "尼克斯 4–1 马刺" in result.answer_markdown
    assert primary.calls == 0


@pytest.mark.asyncio
async def test_series_question_derives_four_to_one_from_index(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(default_intelligence_mode="hybrid"),
    )
    result = await usecase.handle(
        ChatRequest(message="2026尼克斯对马刺系列赛大比分", intelligence_mode="hybrid")
    )
    assert result.status == "completed"
    assert "4–1" in result.answer_markdown
    assert "尼克斯" in result.answer_markdown


@pytest.mark.asyncio
async def test_explicit_finals_table_excludes_regular_season_meetings(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    season = SeasonLabel(start_year=2025, end_year=2026, label="2025-26")
    evidence = Evidence(
        evidence_id="hupu:schedule:regular",
        source_class=SourceClass.ESTABLISHED_SPORTS,
        source_ref="hupu:schedule",
        url="https://nba.hupu.com/schedule/knicks",
        fetched_at_utc=datetime(2026, 9, 4, tzinfo=UTC),
        trust=TrustLevel.MEDIUM,
        freshness=Freshness.FRESH,
    )
    for source_id, day, away_id, home_id, away_score, home_score in (
        ("regular-1", 1, "nyk", "sas", 132, 134),
        ("regular-2", 2, "sas", "nyk", 89, 114),
    ):
        game = Game(
            game_id=f"public:{source_id}",
            season=season,
            start_utc=datetime(2026, 3, day, 0, 0, tzinfo=UTC),
            away=_team(away_id),
            home=_team(home_id),
            away_score=away_score,
            home_score=home_score,
            status=GameStatus.FINAL,
        )
        assert index.upsert_bundle(
            GameBundle(game=game), [evidence], origin="public"
        ) == "inserted"

    result = await ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(default_intelligence_mode="hybrid"),
    ).handle(
        ChatRequest(
            message="2026年尼克斯对马刺总决赛的大比分和每场赛果是什么？",
            intelligence_mode="hybrid",
        )
    )

    table = next(block for block in result.blocks if block.type is AnswerBlockType.TABLE)
    assert len(table.rows) == 5
    assert "132–134" not in result.answer_markdown
    assert "89–114" not in result.answer_markdown
    assert "105–95" in result.answer_markdown
    assert "94–90" in result.answer_markdown


@pytest.mark.asyncio
async def test_matchup_metadata_question_uses_latest_indexed_game_duration(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(default_intelligence_mode="hybrid"),
    )
    result = await usecase.handle(
        ChatRequest(
            message="2026尼克斯对马刺最后一场比赛时长多久",
            intelligence_mode="hybrid",
        )
    )
    assert result.status == "completed"
    assert "2小时46分钟" in result.answer_markdown


@pytest.mark.asyncio
async def test_matchup_g5_resolves_exact_indexed_game(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    primary = EmptyPrimary()
    usecase = ChatUseCase(
        IndexedProvider(primary, index),
        settings=Settings(default_intelligence_mode="hybrid"),
    )

    result = await usecase.handle(
        ChatRequest(message="2026尼克斯-马刺 G5 谁赢了？", intelligence_mode="hybrid")
    )

    assert result.status == "completed"
    assert "尼克斯" in result.answer_markdown
    assert "94–90" in result.answer_markdown
    assert "115–111" not in result.answer_markdown
    assert primary.calls == 0


@pytest.mark.asyncio
async def test_full_agent_timeout_keeps_numbered_matchup_premise_scoped(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=UnavailableAgent(),
    )

    result = await usecase.handle(
        ChatRequest(
            message="朋友说2026总决赛G5是马刺94比90赢了尼克斯，实际谁赢？",
            intelligence_mode="full",
        )
    )

    assert result.status == "completed"
    assert "尼克斯" in result.answer_markdown
    assert "94–90" in result.answer_markdown
    assert "马刺以 94–90" not in result.answer_markdown
    assert "请补充" not in result.answer_markdown
    assert result.composition["mode"] == "fallback"


@pytest.mark.asyncio
async def test_public_mode_g4_resolves_indexed_matchup_not_demo_alias(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    primary = EmptyPrimary()
    usecase = ChatUseCase(
        IndexedProvider(primary, index),
        settings=Settings(
            public_data_mode="hybrid",
            default_intelligence_mode="hybrid",
        ),
    )

    result = await usecase.handle(
        ChatRequest(
            message="2026尼克斯-马刺 G4 谁赢了？",
            intelligence_mode="hybrid",
        )
    )

    assert result.status == "completed"
    assert "尼克斯" in result.answer_markdown
    assert "107–106" in result.answer_markdown
    assert "凯尔特人" not in result.answer_markdown
    assert "雷霆" not in result.answer_markdown
    assert result.data_origin == "public"
    assert primary.calls == 0


@pytest.mark.asyncio
async def test_matchup_g5_player_line_comes_from_index(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    primary = EmptyPrimary()
    usecase = ChatUseCase(
        IndexedProvider(primary, index),
        settings=Settings(default_intelligence_mode="hybrid"),
    )

    result = await usecase.handle(
        ChatRequest(
            message="2026尼克斯对马刺 G5 布伦森多少分？",
            intelligence_mode="hybrid",
        )
    )

    assert result.status == "completed"
    assert "布伦森" in result.answer_markdown
    assert "45" in result.answer_markdown
    assert primary.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("intelligence_mode", ["hybrid", "full"])
async def test_selected_g5_scoring_leader_uses_indexed_box_score_in_both_paths(
    tmp_path, intelligence_mode: str
) -> None:
    """A natural selected-game follow-up must not collapse to score only."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    primary = EmptyPrimary()
    indexed_g5 = index.get_game_summary("hupu:168859").data.game
    agent = PassThroughQueryAgent()
    usecase = ChatUseCase(
        IndexedProvider(primary, index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
        game_registry={indexed_g5.game_id: indexed_g5},
        game_origin_registry={indexed_g5.game_id: "public"},
    )

    result = await usecase.handle(
        ChatRequest(
            message="这场比赛谁得分最高？",
            selected_game_id="hupu:168859",
            intelligence_mode=intelligence_mode,
        )
    )

    assert result.status == "completed"
    assert "杰伦·布伦森" in result.answer_markdown
    assert "45 分" in result.answer_markdown
    assert primary.calls == 0
    if intelligence_mode == "full":
        assert agent.observations
        assert "杰伦·布伦森" in agent.observations[0]["answer_markdown"]
        assert "45 分" in agent.observations[0]["answer_markdown"]
    else:
        assert not agent.observations


@pytest.mark.asyncio
async def test_selected_g5_pbp_labels_home_and_away_score_direction(tmp_path) -> None:
    """An away winner must not make a home-away PBP score look contradictory."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    indexed_g5 = index.get_game_summary("hupu:168859").data.game
    usecase = ChatUseCase(
        IndexedProvider(G5ClosingPlayPrimary(), index),
        settings=Settings(public_data_mode="hybrid"),
        game_registry={indexed_g5.game_id: indexed_g5},
        game_origin_registry={indexed_g5.game_id: "public"},
    )

    result = await usecase.handle(
        ChatRequest(
            message="最后谁投篮的，在什么位置？",
            selected_game_id="hupu:168859",
            intelligence_mode="hybrid",
        )
    )

    assert result.status == "completed"
    assert "维克托·文班亚马" in result.answer_markdown
    assert "马刺 90–94 尼克斯" in result.answer_markdown
    assert "终场比分为 **90–94**" not in result.answer_markdown


@pytest.mark.asyncio
async def test_selected_g5_ordinary_follow_up_does_not_append_default_points_leader(
    tmp_path,
) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    indexed_g5 = index.get_game_summary("hupu:168859").data.game
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(public_data_mode="hybrid"),
        game_registry={indexed_g5.game_id: indexed_g5},
        game_origin_registry={indexed_g5.game_id: "public"},
    )

    result = await usecase.handle(
        ChatRequest(
            message="这场比赛谁打谁？",
            selected_game_id="hupu:168859",
            intelligence_mode="hybrid",
        )
    )

    assert result.status == "completed"
    assert "尼克斯" in result.answer_markdown
    assert "马刺" in result.answer_markdown
    assert "杰伦·布伦森" not in result.answer_markdown
    assert "45 分" not in result.answer_markdown


@pytest.mark.asyncio
async def test_explicit_latest_game_duration_overrides_prior_g5_context(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(default_intelligence_mode="hybrid"),
    )
    session_id = uuid4()

    await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯对马刺系列赛大比分",
            intelligence_mode="hybrid",
        )
    )
    await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯对马刺 G5 布伦森多少分？",
            intelligence_mode="hybrid",
        )
    )
    result = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯对马刺最后一场比赛时长多久",
            intelligence_mode="hybrid",
        )
    )

    assert result.status == "completed"
    assert "2小时46分钟" in result.answer_markdown
    assert "系列赛大比分" not in result.answer_markdown


@pytest.mark.asyncio
async def test_full_agent_dated_recap_recovers_typed_g5_from_matchup_context(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    usecase = ChatUseCase(
        IndexedProvider(RecapSearchPrimary(), index),
        settings=Settings(
            full_intelligence_enabled=True,
            default_intelligence_mode="hybrid",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=SearchChoosingAgent(),
    )
    session_id = uuid4()

    first = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯-马刺",
            intelligence_mode="full",
        )
    )
    result = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026-06-14 08:30 这场比赛是怎么个过程，能给我讲讲吗",
            intelligence_mode="full",
        )
    )

    assert first.status == "completed"
    assert first.answer_markdown.startswith("这组系列赛的结论很清楚")
    assert "尼克斯 4–1 马刺" in first.answer_markdown
    assert "| 北京时间 |" not in first.answer_markdown
    assert result.status == "completed"
    assert "G5" in result.answer_markdown
    assert "94–90" in result.answer_markdown
    assert "布伦森" in result.answer_markdown
    assert "45" in result.answer_markdown
    assert "2小时46分钟" in result.answer_markdown
    assert "收官阶段的防守" in result.answer_markdown
    assert "阵容" not in result.answer_markdown
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"


@pytest.mark.asyncio
async def test_full_agent_preserves_exact_matchup_and_recent_game_synthesis(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    agent = NaturalTwoTurnAgent()
    usecase = ChatUseCase(
        IndexedProvider(RecapSearchPrimary(), index),
        settings=Settings(
            full_intelligence_enabled=True,
            default_intelligence_mode="hybrid",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()

    first = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯-马刺",
            intelligence_mode="full",
        )
    )
    second = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="最近的一场比赛尼克斯赢了吗，怎么赢的",
            intelligence_mode="full",
        )
    )

    assert first.answer_markdown.startswith("这轮对决的结论很清楚")
    assert "北京时间" not in first.answer_markdown
    assert second.answer_markdown.startswith("尼克斯赢了最近一场，终场是 94–90")
    assert "收官阶段的防守守住优势" in second.answer_markdown
    assert "补充线索" not in second.answer_markdown
    assert "公开资料线索" not in second.answer_markdown
    assert "交叉核验" not in second.answer_markdown
    assert "当前对阵：尼克斯、马刺" in (agent.turns[1].context_hint or "")
    assert second.composition["mode"] == "agent"
    assert second.composition["status"] == "used"


@pytest.mark.asyncio
async def test_unique_full_agent_matchup_result_becomes_canonical_follow_up_game(
    tmp_path,
) -> None:
    """A unique typed result, not model prose, owns the next deictic PBP turn."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    primary = PlayByPlayPrimary()
    agent = BridgeShapedQueryAgent()
    usecase = ChatUseCase(
        IndexedProvider(primary, index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()

    first = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="尼克斯对马刺谁得分最高？",
            intelligence_mode="full",
        )
    )
    context = await usecase.context_manager.load(session_id)
    second = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="这场比赛最后一分钟发生了什么？",
            intelligence_mode="full",
        )
    )

    assert first.status == "completed"
    assert "resolved_game" not in first.to_dict()
    assert context is not None and context.active_game is not None
    assert context.active_game.canonical_id == "hupu:168859"
    assert primary.game_ids == ["hupu:168859"]
    assert second.status == "completed"
    assert "42秒" in second.answer_markdown
    assert "马刺 90–94 尼克斯" in second.answer_markdown
    assert "终场比分为 **90–94**" not in second.answer_markdown


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recommendation_question",
    ["你觉得最精华的是哪一场？", "如果只能选一场你选哪场？"],
)
async def test_full_agent_recommends_best_game_from_prior_series_context(
    tmp_path, recommendation_question: str
) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    agent = SeriesRecommendationAgent()
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()

    first = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯-马刺",
            intelligence_mode="full",
        )
    )
    second = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message=recommendation_question,
            intelligence_mode="full",
        )
    )

    assert first.status == "completed"
    assert second.status == "completed"
    assert second.composition["mode"] == "agent"
    assert second.composition["status"] == "used"
    assert "G4" in second.answer_markdown
    assert "107–106" in second.answer_markdown
    assert "分差最小" in second.answer_markdown
    assert "请补充查询对象" not in second.answer_markdown
    assert agent.turns[1].sanitized_question == recommendation_question
    # One lookup needs an initial planning pass, the tool observation, and a
    # final synthesis pass.  Two iterations stop at the observation boundary.
    assert agent.turns[1].max_iterations == 3
    assert agent.turns[1].max_tool_calls == 1
    assert "当前对阵：尼克斯、马刺" in (agent.turns[1].context_hint or "")
    assert agent.observations[1]["status"] == "completed"
    assert "尼克斯" in agent.observations[1]["answer_markdown"]
    assert "马刺" in agent.observations[1]["answer_markdown"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["timeout", "generic_clarification", "candidate_list"]
)
async def test_series_recommendation_recovers_a_choice_not_a_candidate_dump(
    tmp_path, failure: str
) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    agent = SeriesRecommendationFailureAgent(failure)
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()

    await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯-马刺",
            intelligence_mode="full",
        )
    )
    result = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="你觉得最精华的是哪一场？",
            intelligence_mode="full",
        )
    )

    assert result.status == "completed"
    expected_composition = "fallback" if failure == "timeout" else "agent"
    expected_status = "fallback" if failure == "timeout" else "used"
    assert result.composition["mode"] == expected_composition
    assert result.composition["status"] == expected_status
    assert "G4" in result.answer_markdown
    assert "107–106" in result.answer_markdown
    assert "G2" in result.answer_markdown
    assert "1 分" in result.answer_markdown
    assert "3–1" in result.answer_markdown
    assert "胶着" in result.answer_markdown or "分差" in result.answer_markdown
    assert "转折" in result.answer_markdown or "系列赛进程" in result.answer_markdown
    assert "本轮可供比较的比赛" not in result.answer_markdown
    assert "请补充查询对象" not in result.answer_markdown


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "expected_game_number", "expected_game_id"),
    [
        (
            "按胶着程度和系列赛转折，我推荐 **G4**。"
            "它与 G2 都只有 1 分分差，但 G4 让尼克斯把系列赛推进到 3–1。",
            4,
            "hupu:168858",
        ),
        (
            "按系列赛转折，首选第四场。尼克斯赢下后把大比分推进到 3–1。",
            4,
            "hupu:168858",
        ),
        (
            "若以比赛悬念和节点重要性衡量，答案是 G4；它的分差只有 1 分。",
            4,
            "hupu:168858",
        ),
        (
            "按胶着程度，G4 更值得看；它与 G2 都只有 1 分分差。",
            4,
            "hupu:168858",
        ),
        (
            "我不推荐 G2，我推荐 G4，因为 G4 的系列赛意义更关键。",
            4,
            "hupu:168858",
        ),
        (
            "我推荐第二战，它与 G4 同为一分险胜。",
            2,
            "hupu:168856",
        ),
        (
            "我的选择是第二场，因为比赛一直胶着到最后。",
            2,
            "hupu:168856",
        ),
        (
            "若只能看一场我看 G2，它的终场分差只有一分。",
            2,
            "hupu:168856",
        ),
    ],
)
async def test_no_tool_agent_recommendation_is_grounded_then_preserved(
    tmp_path,
    answer: str,
    expected_game_number: int,
    expected_game_id: str,
) -> None:
    """A fluent no-tool synthesis survives after one controlled series lookup."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    agent = NoToolSeriesRecommendationAgent(answer)
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()

    await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯-马刺",
            intelligence_mode="full",
        )
    )
    result = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="你觉得最精华的是哪一场？",
            intelligence_mode="full",
        )
    )

    assert result.status == "completed"
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
    assert result.answer_markdown == answer
    assert (
        usecase._recommended_game_number_from_answer(result.answer_markdown)
        == expected_game_number
    )
    assert usecase.telemetry.latest().agent_tool_call_count == 1
    context = await usecase.context_manager.load(session_id)
    assert context is not None and context.active_game is not None
    assert context.active_game.canonical_id == expected_game_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        "本轮可供比较的比赛：G1、G2、G3、G4、G5。",
        "按胶着程度我推荐 G4，因为马刺以 130–100 赢下这场。",
    ],
)
async def test_no_tool_agent_unqualified_recommendation_uses_scoped_recovery(
    tmp_path, answer: str
) -> None:
    """Candidate dumps and unsupported facts cannot escape as final prose."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    agent = NoToolSeriesRecommendationAgent(answer)
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()
    await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯-马刺",
            intelligence_mode="full",
        )
    )

    result = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="你觉得最精华的是哪一场？",
            intelligence_mode="full",
        )
    )

    assert result.status == "completed"
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
    assert "G4" in result.answer_markdown
    assert "107–106" in result.answer_markdown
    assert "3–1" in result.answer_markdown
    assert "本轮可供比较的比赛" not in result.answer_markdown
    assert "130–100" not in result.answer_markdown
    assert "马刺以 130" not in result.answer_markdown


@pytest.mark.asyncio
async def test_no_tool_agent_inverse_selection_keeps_series_and_negative_criterion(
    tmp_path,
) -> None:
    """A least-watchable choice is Agent-owned and never flipped by best-game recovery."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    answer = (
        "如果按悬念和分差，我最不推荐 **G1**："
        "这场分差最大，比赛胶着程度相对最低。"
    )
    agent = NoToolSeriesRecommendationAgent(answer)
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()
    await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯-马刺",
            intelligence_mode="full",
        )
    )

    result = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="哪场最不值得回看？",
            intelligence_mode="full",
        )
    )

    assert result.status == "completed"
    assert result.composition["mode"] == "agent"
    assert result.answer_markdown == answer
    assert "G4" not in result.answer_markdown


@pytest.mark.asyncio
async def test_recommended_verified_game_becomes_safe_follow_up_scope(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    primary = PlayByPlayPrimary()
    agent = RecommendationMemoryAgent()
    usecase = ChatUseCase(
        IndexedProvider(primary, index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()

    prompts = (
        "2026尼克斯-马刺",
        "你觉得最精华的是哪一场？",
        "为什么不是G2？",
        "你刚推荐的那场最后一分钟发生了什么？",
    )
    results = []
    for prompt in prompts:
        results.append(
            await usecase.handle(
                ChatRequest(
                    session_id=session_id,
                    message=prompt,
                    intelligence_mode="full",
                )
            )
        )

    assert "G2" in results[2].answer_markdown
    assert "G4" in results[2].answer_markdown
    assert agent.observations[2].get("coverage") == "series_candidates_ready"
    assert "G4" in (agent.turns[2].context_hint or "")
    assert "G4" in (agent.turns[3].context_hint or "")
    assert primary.game_ids == ["hupu:168858"]
    assert "42秒" in results[3].answer_markdown
    assert "请补充" not in results[3].answer_markdown


@pytest.mark.asyncio
async def test_g2_recommendation_challenge_preserves_published_choice_and_scope(
    tmp_path,
) -> None:
    """Exact live journey: a “why not G2” challenge must not switch to G4."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    primary = PlayByPlayPrimary()
    agent = ExactG2RecommendationChallengeAgent()
    usecase = ChatUseCase(
        PartialSeriesIndexedProvider(primary, index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()

    prompts = (
        "2026尼克斯-马刺",
        "你觉得最精华的是哪一场？",
        "为什么不是G2？",
        "你刚推荐的那场最后一分钟发生了什么？",
    )
    results = [
        await usecase.handle(
            ChatRequest(
                session_id=session_id,
                message=prompt,
                intelligence_mode="full",
            )
        )
        for prompt in prompts
    ]

    assert all(result.status == "completed" for result in results)
    assert all(result.composition["mode"] == "agent" for result in results)
    # The invalid implicit home-team claim is repaired without changing the
    # Agent's subjective G2 choice.
    assert "G2" in results[1].answer_markdown
    assert "作为主场" not in results[1].answer_markdown
    # The user challenged the exact game that was already recommended.
    assert "刚才推荐的就是 **G2**" in results[2].answer_markdown
    assert "G4" not in results[2].answer_markdown
    assert agent.observations[2]["query_scope"]["active_game_number"] == 2
    # The subsequent pronoun remains bound to G2, never the tempting G4 row.
    assert primary.game_ids == ["hupu:168856"]
    assert "42秒" in results[3].answer_markdown
    assert "马刺 104–105 尼克斯" in results[3].answer_markdown
    assert "主队 104–105 客队" not in results[3].answer_markdown


@pytest.mark.asyncio
async def test_recommendation_wrong_tools_are_rebound_to_one_typed_scope(tmp_path) -> None:
    """A series choice and its PBP follow-up never drift to another game."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    primary = WrongGameSearchPrimary()
    agent = WrongToolRecommendationMemoryAgent()
    usecase = ChatUseCase(
        IndexedProvider(primary, index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()

    prompts = (
        "2026尼克斯-马刺",
        "你觉得最精华的是哪一场？",
        "为什么不是G2？",
        "你刚推荐的那场最后一分钟发生了什么？",
    )
    results = []
    tool_counts = []
    for prompt in prompts:
        results.append(
            await usecase.handle(
                ChatRequest(
                    session_id=session_id,
                    message=prompt,
                    intelligence_mode="full",
                )
            )
        )
        tool_counts.append(usecase.telemetry.latest().agent_tool_call_count)

    assert all(result.status == "completed" for result in results)
    assert all(
        result.composition == {"mode": "agent", "status": "used", "latency_ms": 2}
        for result in results
    )
    assert tool_counts == [1, 1, 1, 1]
    assert all(turn.max_iterations == 3 for turn in agent.turns[1:3])
    assert all(turn.max_tool_calls == 1 for turn in agent.turns[1:3])
    assert agent.observations[1].get("coverage") == "series_candidates_ready"
    assert agent.observations[2].get("coverage") == "series_candidates_ready"
    assert primary.search_calls == 0
    assert primary.game_ids == ["hupu:168858"]
    assert "42秒" in results[3].answer_markdown
    assert "G5" not in results[3].answer_markdown
    assert "94–90" not in results[3].answer_markdown


@pytest.mark.asyncio
async def test_zero_tool_fact_follow_up_gets_one_typed_game_observation(tmp_path) -> None:
    """A history-only PBP answer is repaired without blocking the response."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    primary = PlayByPlayPrimary()
    agent = ZeroToolFactFollowUpAgent()
    usecase = ChatUseCase(
        IndexedProvider(primary, index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()

    prompts = (
        "2026尼克斯-马刺",
        "你觉得最精华的是哪一场？",
        "为什么不是G2？",
        "你刚推荐的那场最后一分钟发生了什么？",
    )
    results = [
        await usecase.handle(
            ChatRequest(
                session_id=session_id,
                message=prompt,
                intelligence_mode="full",
            )
        )
        for prompt in prompts
    ]

    final = results[-1]
    assert final.status == "completed"
    assert final.composition == {"mode": "agent", "status": "used", "latency_ms": 2}
    assert primary.game_ids == ["hupu:168856"]
    assert agent.observations[-1].get("coverage") == "series_candidates_ready"
    assert usecase.telemetry.latest().agent_tool_call_count == 1
    assert "42秒" in final.answer_markdown
    assert "杰伦·布伦森" in final.answer_markdown
    assert "没有逐回合" not in final.answer_markdown
    assert "无法还原" not in final.answer_markdown

    # The same zero-observation grounding applies to an ordinary objective
    # game follow-up, not only to the final-minute/PBP wording.
    matchup = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="这场谁打谁？",
            intelligence_mode="full",
        )
    )
    assert matchup.status == "completed"
    assert matchup.composition == {
        "mode": "agent",
        "status": "used",
        "latency_ms": 2,
    }
    assert usecase.telemetry.latest().agent_tool_call_count == 1
    assert "尼克斯" in matchup.answer_markdown
    assert "马刺" in matchup.answer_markdown
    assert "105–104" in matchup.answer_markdown


@pytest.mark.asyncio
async def test_invalid_recommendation_relations_use_scoped_series_recovery(tmp_path) -> None:
    """A subjective G3 choice cannot publish false margin/home comparisons."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    agent = BoundedPbpRecommendationAgent(3, invalid_facts=True)
    usecase = ChatUseCase(
        IndexedProvider(EmptyPrimary(), index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()
    await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="2026尼克斯-马刺",
            intelligence_mode="full",
        )
    )
    result = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="你觉得最精华的是哪一场？",
            intelligence_mode="full",
        )
    )

    assert result.status == "completed"
    assert result.composition == {"mode": "agent", "status": "used", "latency_ms": 2}
    assert "G4" in result.answer_markdown
    assert "107–106" in result.answer_markdown
    assert "G2" in result.answer_markdown
    assert "最小分差" in result.answer_markdown
    assert "G3**：它是全系列分差最小" not in result.answer_markdown
    assert "马刺在主场" not in result.answer_markdown
    context = await usecase.context_manager.load(session_id)
    assert context is not None and context.active_game is not None
    assert context.active_game.canonical_id == "hupu:168858"


def test_tied_minimum_margin_cannot_be_described_as_unique(tmp_path) -> None:
    """Chinese G labels and a contradictory unique-margin claim are rejected."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    season = SeasonLabel(start_year=2025, end_year=2026, label="2025-26")
    games = index.search_games(
        GameFilters(team_ids=["nyk", "sas"], season=season),
        limit=20,
    ).data
    candidates = {
        int(game.series_game_number): game
        for game in games
        if game.series_game_number is not None
    }
    answer = (
        "我以悬念最足为口径，推荐第2场（尼克斯 105–104 马刺）。"
        "它是全轮唯一接近绝杀级别的分差；第4场同样是1分险胜。"
    )

    assert not ChatUseCase._series_recommendation_facts_valid(answer, candidates)


def test_recovery_keeps_an_already_recommended_g2(tmp_path) -> None:
    """A failed challenge turn must not silently switch a prior G2 choice."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    season = SeasonLabel(start_year=2025, end_year=2026, label="2025-26")
    games = index.search_games(
        GameFilters(team_ids=["nyk", "sas"], season=season),
        limit=20,
    ).data
    candidates = {
        int(game.series_game_number): game
        for game in games
        if game.series_game_number is not None
    }
    observation = {
        "status": "completed",
        "intent": "nba_query",
        "coverage": "series_candidates_ready",
        "query_scope": {"active_game_number": 2},
        "answer_markdown": "本轮可供比较的比赛。",
    }

    answer = ChatUseCase._series_recommendation_recovery(
        "为什么不是G2？",
        [observation],
        candidates,
    )

    assert answer is not None
    assert "推荐的就是 **G2**" in answer
    assert "105–104" in answer
    assert "G4" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("game_number", "game_id"),
    [(2, "hupu:168856"), (3, "hupu:168857"), (4, "hupu:168858")],
)
async def test_recommended_game_pbp_has_one_canonical_query(
    tmp_path, game_number: int, game_id: str
) -> None:
    """G2/G3/G4 follow-ups each receive one exact PBP observation."""

    index = GameIndex(tmp_path / "index.sqlite3")
    _seed_finals(index)
    primary = PlayByPlayPrimary()
    agent = BoundedPbpRecommendationAgent(game_number)
    usecase = ChatUseCase(
        IndexedProvider(primary, index),
        settings=Settings(
            public_data_mode="hybrid",
            full_intelligence_enabled=True,
            default_intelligence_mode="full",
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_agent",
            siliconflow_api_key="test-key",
        ),
        agent_runtime=agent,
    )
    session_id = uuid4()
    for prompt in (
        "2026尼克斯-马刺",
        "你觉得最精华的是哪一场？",
    ):
        result = await usecase.handle(
            ChatRequest(
                session_id=session_id,
                message=prompt,
                intelligence_mode="full",
            )
        )
        assert result.status == "completed"

    replay = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="你刚推荐的那场最后一分钟发生了什么？",
            intelligence_mode="full",
        )
    )

    assert replay.status == "completed"
    assert replay.composition == {"mode": "agent", "status": "used", "latency_ms": 2}
    assert agent.turns[-1].max_tool_calls == 1
    assert agent.turns[-1].max_iterations == 3
    assert agent.pbp_attempts == 1
    assert primary.game_ids == [game_id]
    assert usecase.telemetry.latest().agent_tool_call_count == 1
    assert "42秒" in replay.answer_markdown
    assert "杰伦·布伦森" in replay.answer_markdown
