from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.application.ports import ProviderResult, RuntimeStatus
from apps.api.src.config import Settings
from apps.api.src.domain.errors import ProviderError, ProviderErrorKind
from apps.api.src.domain.models import (
    ErrorCode,
    Evidence,
    Freshness,
    NewsItem,
    SourceClass,
    TrustLevel,
)
from apps.api.src.domain.time_policy import FixedClock
from apps.api.src.infrastructure.agent_tools import AgentToolCall
from apps.api.src.infrastructure.hermes_agent_runtime import AgentTurnResult
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway
from apps.api.src.providers.search_augmented_provider import SearchAugmentedProvider


class FakeSmartAgent:
    mode = "embedded_agent"
    model = "test-model"

    def __init__(self, *, unavailable: bool = False, answer_override: str | None = None) -> None:
        self.unavailable = unavailable
        self.answer_override = answer_override
        self.turns = []

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        if self.unavailable:
            return AgentTurnResult(
                status=RuntimeStatus.UNAVAILABLE,
                finish_reason="test_unavailable",
                latency_ms=1,
            )
        if turn.sanitized_question.lower() in {
            "nihao",
            "hello",
            "nishishei",
            "你是谁",
            "你能做什么",
        }:
            return AgentTurnResult(
                status=RuntimeStatus.OK,
                answer_markdown="您好！我可以帮您查询 NBA 赛程、比赛和球员表现。",
                latency_ms=1,
                iteration_count=1,
            )
        observation = dict(
            await tool_runner("nba_schedule", {"date_expression": "下周"})
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=self.answer_override or observation["answer_markdown"],
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_schedule", "hash", "no_data", 1)],
            latency_ms=2,
            iteration_count=2,
        )


class WrongToolAgent(FakeSmartAgent):
    async def run(self, turn, *, tool_runner, cancel):
        observation = dict(await tool_runner("nba_schedule", {"date_expression": "下周"}))
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=observation["answer_markdown"],
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_schedule", "hash", "no_data", 1)],
            latency_ms=1,
            iteration_count=1,
        )


class WrongIntentQueryAgent(FakeSmartAgent):
    """Call the generic query tool but return a schedule-only observation."""

    async def run(self, turn, *, tool_runner, cancel):
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        # Simulate a nested parser/provider misclassification: the tool name
        # is generic, but its typed observation is still only a schedule.
        observation["intent"] = "schedule_result"
        observation["answer_markdown"] = "北京时间 2026-06-14 至 2026-06-14 的 NBA 赛程："
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=observation["answer_markdown"],
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_query", "hash", observation["status"], 1)],
            latency_ms=1,
            iteration_count=1,
        )


class QueryingSmartAgent(FakeSmartAgent):
    """Exercise the real selected-game nba_query bridge from a fake planner."""

    def __init__(self) -> None:
        super().__init__()
        self.tool_names: list[str] = []

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        self.tool_names.append("nba_query")
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=observation["answer_markdown"],
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_query", "hash", "completed", 1)],
            latency_ms=2,
            iteration_count=2,
        )


class SearchingSmartAgent(FakeSmartAgent):
    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        observation = dict(
            await tool_runner("nba_search", {"query": turn.sanitized_question})
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=observation["answer_markdown"],
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_search", "hash", observation["status"], 1)],
            latency_ms=2,
            iteration_count=2,
        )


class LowQualityTacticalAgent(FakeSmartAgent):
    """Return a search-summary collage instead of a usable tactical answer."""

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        observation = dict(
            await tool_runner("nba_search", {"query": turn.sanitized_question})
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=(
                "分析：球迷热议库里的无球跑动。"
                "另外，文章提到挡拆防守。"
                "另外，公开资料认为轮转很重要。"
            ),
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_search", "hash", observation["status"], 1)],
            latency_ms=2,
            iteration_count=2,
        )


class RepeatingScheduleAgent(FakeSmartAgent):
    """Try four tool calls to verify the request-wide provider budget."""

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        observations = []
        for _ in range(4):
            observations.append(
                dict(await tool_runner("nba_schedule", {"date_expression": "今天"}))
            )
        first = observations[0]
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=first["answer_markdown"],
            evidence_state=first["evidence_state"],
            observations=observations,
            tool_calls=[
                AgentToolCall("nba_schedule", str(index), item["status"], 1)
                for index, item in enumerate(observations)
            ],
            latency_ms=2,
            iteration_count=4,
        )


class ExplicitSearchAgent(FakeSmartAgent):
    """Choose one exact web query and synthesize its observation."""

    search_query = "2026总决赛系列赛综述"

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        observation = dict(
            await tool_runner("nba_search", {"query": self.search_query})
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown="公开报道将其描述为尼克斯与马刺的一轮总决赛交锋。",
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_search", "hash", "completed", 1)],
            latency_ms=2,
            iteration_count=2,
        )


class QueryThenSearchAgent(FakeSmartAgent):
    """Explicitly own the structured-miss to web-search transition."""

    search_query = "尼克斯 马刺 2026 总决赛回顾"

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        structured = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        searched = dict(
            await tool_runner("nba_search", {"query": self.search_query})
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown="公开报道显示，这是尼克斯与马刺的一轮总决赛交锋。",
            evidence_state=searched["evidence_state"],
            observations=[structured, searched],
            tool_calls=[
                AgentToolCall("nba_query", "query", structured["status"], 1),
                AgentToolCall("nba_search", "search", searched["status"], 1),
            ],
            latency_ms=3,
            iteration_count=3,
        )


class ContextSearchAgent(FakeSmartAgent):
    """Use a typed first turn, then search a deictic follow-up."""

    search_query = "总决赛 G4 比赛过程"

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        if len(self.turns) == 1:
            observation = dict(
                await tool_runner("nba_query", {"question": turn.sanitized_question})
            )
            tool_name = "nba_query"
        else:
            observation = dict(
                await tool_runner("nba_search", {"query": self.search_query})
            )
            tool_name = "nba_search"
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=observation["answer_markdown"],
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall(tool_name, "scope", observation["status"], 1)],
            latency_ms=2,
            iteration_count=2,
        )


class SynthesizingQueryAgent(QueryingSmartAgent):
    """Explicitly query, search and then return a concise synthesis."""

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        self.tool_names.append("nba_query")
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        searched = dict(
            await tool_runner(
                "nba_search", {"query": "尼克斯 马刺 2026 总决赛综述"}
            )
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=(
                "公开报道普遍将其描述为尼克斯与马刺的一轮总决赛交锋；"
                "当前结构化比赛数据未覆盖对应场次，具体赛果仍需交叉核验。"
            ),
            evidence_state=searched["evidence_state"],
            observations=[observation, searched],
            tool_calls=[
                AgentToolCall("nba_query", "query", observation["status"], 1),
                AgentToolCall("nba_search", "search", searched["status"], 1),
            ],
            latency_ms=2,
            iteration_count=3,
        )


class MislabeledEvidenceQueryAgent(QueryingSmartAgent):
    """Simulate a runtime envelope that under-reports completed tool evidence."""

    async def run(self, turn, *, tool_runner, cancel):
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=observation["answer_markdown"],
            evidence_state="none",
            observations=[observation],
            tool_calls=[AgentToolCall("nba_query", "hash", "completed", 1)],
            latency_ms=2,
            iteration_count=2,
        )


class ParaphrasingQueryAgent(QueryingSmartAgent):
    """Use an incorrect planning paraphrase to verify the user text stays authoritative."""

    async def run(self, turn, *, tool_runner, cancel):
        observation = dict(
            await tool_runner(
                "nba_query",
                {"question": "2025-26 总决赛系列赛大比分"},
            )
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=observation["answer_markdown"],
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_query", "hash", observation["status"], 1)],
            latency_ms=2,
            iteration_count=2,
        )


class NaturalSummaryAgent(QueryingSmartAgent):
    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        self.tool_names.append("nba_query")
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=(
                "简要概括：凯尔特人以 108–104 击败雷霆，"
                "杰伦·布朗得到全场最高的 32 分。"
            ),
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_query", "hash", "completed", 1)],
            latency_ms=2,
            iteration_count=2,
        )


class SearchOnlyProvider:
    async def search_news(self, query, budget):
        now = datetime.now(UTC)
        evidence = Evidence(
            evidence_id="baidu:search:test",
            source_class=SourceClass.SEARCH,
            source_ref="baidu.search",
            url="https://www.baidu.com/s",
            fetched_at_utc=now,
            trust=TrustLevel.MEDIUM,
            freshness=Freshness.UNKNOWN,
        )
        item = NewsItem(
            news_id=evidence.evidence_id,
            title="ESPN 库里挡拆防守策略与比赛复盘",
            summary="Sportsradar provider 整理了夹击、换防和弱侧轮转的常见方法。",
            subject_refs=[],
            evidence_id=evidence.evidence_id,
        )
        return ProviderResult(
            data=[item], evidence=[evidence], partial=True, retrieved_at_utc=now
        )


class MatchupSearchProvider:
    async def search_news(self, query, budget):
        now = datetime.now(UTC)
        evidence = Evidence(
            evidence_id="baidu:search:matchup",
            source_class=SourceClass.SEARCH,
            source_ref="baidu.search",
            url="https://www.baidu.com/s",
            fetched_at_utc=now,
            trust=TrustLevel.MEDIUM,
            freshness=Freshness.UNKNOWN,
        )
        rows = [
            ("尼克斯马刺第四场回顾", "尼克斯与马刺第四场比赛报道。"),
            ("尼克斯马刺第三场回顾", "尼克斯与马刺第三场比赛报道。"),
            ("尼克斯马刺第一场回顾", "尼克斯与马刺第一场比赛报道。"),
            ("总冠军系列赛结果", "尼克斯与马刺总决赛第五场结束，系列赛夺冠结果。"),
        ]
        items = [
            NewsItem(
                news_id=f"search:matchup:{index}",
                title=title,
                summary=summary,
                subject_refs=[],
                evidence_id=evidence.evidence_id,
            )
            for index, (title, summary) in enumerate(rows)
        ]
        return ProviderResult(
            data=items, evidence=[evidence], partial=True, retrieved_at_utc=now
        )


class RecordingMatchupSearchProvider(MatchupSearchProvider):
    def __init__(self) -> None:
        self.queries = []

    async def search_news(self, query, budget):
        self.queries.append(query)
        return await super().search_news(query, budget)


class IrrelevantMatchupSearchProvider:
    async def search_news(self, query, budget):
        now = datetime.now(UTC)
        evidence = Evidence(
            evidence_id="search:irrelevant",
            source_class=SourceClass.SEARCH,
            source_ref="search.test",
            url="https://example.test/search",
            fetched_at_utc=now,
            trust=TrustLevel.MEDIUM,
            freshness=Freshness.UNKNOWN,
        )
        items = [
            NewsItem(
                news_id="search:irrelevant:1",
                title="Tony Parker 谈法国篮球",
                summary="一篇只讨论法国俱乐部的采访。",
                evidence_id=evidence.evidence_id,
            ),
            NewsItem(
                news_id="search:irrelevant:2",
                title="NBA offseason recap",
                summary="一篇没有提到查询双方球队的休赛期报道。",
                evidence_id=evidence.evidence_id,
            ),
        ]
        return ProviderResult(
            data=items,
            evidence=[evidence],
            partial=True,
            retrieved_at_utc=now,
        )


class AdversarialQueryingAgent(QueryingSmartAgent):
    """Return a fluent but factually unsafe paraphrase after a valid NBA call."""

    def __init__(self, answer: str) -> None:
        super().__init__()
        self.answer = answer

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        self.tool_names.append("nba_query")
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=self.answer,
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_query", "hash", "completed", 1)],
            latency_ms=2,
            iteration_count=2,
        )


class ObservationOnlyAgent(QueryingSmartAgent):
    """Simulate an iteration/transport boundary after a useful tool call."""

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        self.tool_names.append("nba_query")
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        return AgentTurnResult(
            status=RuntimeStatus.TIMEOUT,
            answer_markdown=None,
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_query", "hash", "completed", 1)],
            finish_reason="timeout_after_tool",
            latency_ms=2,
            iteration_count=4,
        )


class QuotaWithoutObservationAgent(FakeSmartAgent):
    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        return AgentTurnResult(
            status=RuntimeStatus.UNAVAILABLE,
            finish_reason="quota_exhausted",
            error_code=ErrorCode.COMPOSER_UNAVAILABLE,
            retryable=False,
            latency_ms=2,
        )


class QuotaAfterObservationAgent(QueryingSmartAgent):
    async def run(self, turn, *, tool_runner, cancel):
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        return AgentTurnResult(
            status=RuntimeStatus.UNAVAILABLE,
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[AgentToolCall("nba_query", "hash", observation["status"], 1)],
            finish_reason="quota_exhausted",
            error_code=ErrorCode.COMPOSER_UNAVAILABLE,
            retryable=False,
            latency_ms=2,
        )


class NestedQueryNoticeAgent(QueryingSmartAgent):
    """Mirror the real tool bridge's private notice-to-call projection."""

    async def run(self, turn, *, tool_runner, cancel):
        observation = dict(
            await tool_runner("nba_query", {"question": turn.sanitized_question})
        )
        notices = observation.get("_public_notices") or []
        notice = notices[0] if notices else {}
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=observation["answer_markdown"],
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall(
                    "nba_query",
                    "hash",
                    observation["status"],
                    1,
                    error_code=notice.get("code"),
                    retryable=bool(notice.get("retryable", False)),
                )
            ],
            latency_ms=2,
            iteration_count=2,
        )


class EmptyGameProvider(FixtureProvider):
    """Keep the provider contract available while returning no usable facts."""

    @staticmethod
    def _empty() -> ProviderResult:
        return ProviderResult(
            data=[],
            evidence=[],
            partial=False,
            retrieved_at_utc=datetime.now(UTC),
        )

    async def search_games(self, *_args, **_kwargs):
        return self._empty()

    async def get_game_summary(self, *_args, **_kwargs):
        return self._empty()

    async def get_player_stats(self, *_args, **_kwargs):
        return self._empty()

    async def get_play_by_play(self, *_args, **_kwargs):
        return self._empty()


class QuotaGameProvider(EmptyGameProvider):
    async def search_games(self, *_args, **_kwargs):
        return ProviderResult(
            data=None,
            evidence=[],
            partial=False,
            error=ProviderError(
                kind=ProviderErrorKind.QUOTA_EXHAUSTED,
                retryable=False,
                safe_message="quota exhausted",
            ),
            retrieved_at_utc=datetime.now(UTC),
        )


class UsableFixtureWithSearchQuotaNotice(FixtureProvider):
    async def get_game_summary(self, game_id, budget):
        result = await super().get_game_summary(game_id, budget)
        if result.error is not None:
            return result
        return result.model_copy(
            update={
                "capability_issues": [
                    ProviderError(
                        kind=ProviderErrorKind.QUOTA_EXHAUSTED,
                        retryable=False,
                        safe_message="quota exhausted",
                    )
                ]
            }
        )


class TruncatedRecapAgent(QueryThenSearchAgent):
    """Return completed observations with a budget/heading-only final text."""

    async def run(self, turn, *, tool_runner, cancel):
        result = await super().run(turn, tool_runner=tool_runner, cancel=cancel)
        return result.model_copy(
            update={
                "answer_markdown": "工具预算已用尽\n## 先给结论\n## 过程讲解（公开报道线索）",
            }
        )


class PublicMirrorProvider(FixtureProvider):
    """Expose the fixture shape as a primary public source with external IDs."""

    @staticmethod
    def _public(result):
        return result.model_copy(
            update={
                "evidence": [
                    item.model_copy(
                        update={"source_class": SourceClass.ESTABLISHED_SPORTS}
                    )
                    for item in result.evidence
                ]
            }
        )

    async def search_games(self, filters, budget):
        result = await super().search_games(filters, budget)
        if result.error is not None:
            return result
        games = [
            game.model_copy(update={"game_id": f"public-{game.game_id}"})
            for game in (result.data or [])
        ]
        return self._public(result.model_copy(update={"data": games}))

    async def get_game_summary(self, game_id, budget):
        internal_id = str(game_id).removeprefix("public-")
        result = await super().get_game_summary(internal_id, budget)
        return self._public(result)


def settings() -> Settings:
    return Settings(
        full_intelligence_enabled=True,
        default_intelligence_mode="hybrid",
        llm_mode="live",
        runtime_profile="hybrid",
        hermes_lite_mode="embedded_agent",
        siliconflow_api_key="test-key",
    )


@pytest.mark.asyncio
async def test_full_agent_handles_greeting_without_tool() -> None:
    agent = FakeSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)
    result = await usecase.handle({"message": "nihao", "intelligence_mode": "full"})
    assert result.status == "completed", result.to_dict()
    assert "您好" in result.answer_markdown
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"


@pytest.mark.asyncio
async def test_full_agent_can_search_long_tail_instead_of_premature_clarification() -> None:
    primary = SearchAugmentedProvider(FixtureProvider(), SearchOnlyProvider())
    gateway = ProviderGateway(primary, max_retries=0)
    agent = SearchingSmartAgent()
    usecase = ChatUseCase(primary, settings=settings(), gateway=gateway, agent_runtime=agent)
    result = await usecase.handle(
        {"message": "如果要限制库里，该怎么布置防守？", "intelligence_mode": "full"}
    )
    assert result.status == "completed"
    assert "夹击" in result.answer_markdown or "换防" in result.answer_markdown
    assert "请补充查询对象" not in result.answer_markdown
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
    assert usecase.telemetry.latest().agent_tool_names == ["nba_search"]


@pytest.mark.asyncio
async def test_low_quality_tactical_search_collage_is_repaired_on_agent_route() -> None:
    primary = SearchAugmentedProvider(FixtureProvider(), SearchOnlyProvider())
    gateway = ProviderGateway(primary, max_retries=0)
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=gateway,
        agent_runtime=LowQualityTacticalAgent(),
    )

    result = await usecase.handle(
        {
            "message": "如何同时限制库里的无球跑动和挡拆？",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
    assert "阻断接球路线" in result.answer_markdown
    assert "挤过掩护" in result.answer_markdown
    assert "弱侧轮转" in result.answer_markdown
    assert "保护篮下" in result.answer_markdown
    for forbidden in ("球迷热议", "文章", "报道", "公开资料", "另外"):
        assert forbidden not in result.answer_markdown
    assert usecase.telemetry.latest().fallback_reason == "agent_tactical_quality_repaired"


@pytest.mark.asyncio
async def test_agent_search_uses_web_provider_without_structured_news_call() -> None:
    class Primary(FixtureProvider):
        def __init__(self) -> None:
            super().__init__()
            self.news_calls = 0

        async def search_news(self, query, budget):
            self.news_calls += 1
            return await super().search_news(query, budget)

    class WebSearch(SearchOnlyProvider):
        def __init__(self) -> None:
            self.web_calls = 0

        async def search_web(self, query, budget):
            self.web_calls += 1
            return await super().search_news(query, budget)

    primary_source = Primary()
    web_source = WebSearch()
    primary = SearchAugmentedProvider(primary_source, web_source)
    gateway = ProviderGateway(primary, max_retries=0)
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=gateway,
        agent_runtime=SearchingSmartAgent(),
    )
    result = await usecase.handle(
        {"message": "库里挡拆防守怎么限制？", "intelligence_mode": "full"}
    )
    assert result.status == "completed"
    assert web_source.web_calls == 1
    assert primary_source.news_calls == 0


@pytest.mark.asyncio
async def test_clear_matchup_prioritizes_series_result_and_does_not_reask_scope() -> None:
    primary = SearchAugmentedProvider(FixtureProvider(), MatchupSearchProvider())
    gateway = ProviderGateway(primary, max_retries=0)
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=gateway,
        agent_runtime=QueryThenSearchAgent(),
    )

    result = await usecase.handle(
        {"message": "2026尼克斯-马刺", "intelligence_mode": "full"}
    )

    assert result.status == "completed"
    assert result.answer_markdown.startswith("公开报道显示")
    assert "暂未找到 **尼克斯 对 马刺**" not in result.answer_markdown
    assert "总决赛交锋" in result.answer_markdown
    assert "公开资料线索（待交叉核验）" not in result.answer_markdown
    assert len(result.answer_markdown) < 900
    assert "**2026**" not in result.answer_markdown
    assert "请补充日期或场次" not in result.answer_markdown
    assert result.evidence_state == "partial"


@pytest.mark.asyncio
async def test_agent_selected_search_query_is_preserved_with_typed_user_scope() -> None:
    search = RecordingMatchupSearchProvider()
    primary = SearchAugmentedProvider(FixtureProvider(), search)
    gateway = ProviderGateway(primary, max_retries=0)
    agent = ExplicitSearchAgent()
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=gateway,
        agent_runtime=agent,
    )

    result = await usecase.handle(
        {"message": "2026尼克斯-马刺", "intelligence_mode": "full"}
    )

    assert agent.turns[0].sanitized_question == "2026尼克斯-马刺"
    assert len(search.queries) == 1
    assert search.queries[0].keywords == [agent.search_query]
    assert {
        item.canonical_id for item in search.queries[0].subject_refs
    } == {"nyk", "sas"}
    assert result.composition["mode"] == "agent"
    assert result.answer_markdown.startswith("公开报道将其描述为")


@pytest.mark.asyncio
async def test_selected_game_scopes_agent_search_without_rewriting_query() -> None:
    search = RecordingMatchupSearchProvider()
    primary = SearchAugmentedProvider(FixtureProvider(), search)
    agent = SearchingSmartAgent()
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=ProviderGateway(primary, max_retries=0),
        agent_runtime=agent,
    )

    result = await usecase.handle(
        {
            "message": "这场比赛怎么打的？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    # Every returned document is about a different matchup.  The selected
    # game is correctly attached to the search scope, but irrelevant evidence
    # must not be promoted to a successful recap.
    assert result.status == "no_data"
    assert search.queries[0].keywords == ["这场比赛怎么打的？"]
    assert {
        item.canonical_id for item in search.queries[0].subject_refs
    } >= {"2026-finals-g4", "okc", "bos"}


@pytest.mark.asyncio
async def test_session_game_scopes_later_agent_search_without_rewriting_query() -> None:
    search = RecordingMatchupSearchProvider()
    primary = SearchAugmentedProvider(FixtureProvider(), search)
    agent = ContextSearchAgent()
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=ProviderGateway(primary, max_retries=0),
        agent_runtime=agent,
    )

    first = await usecase.handle(
        {"message": "2025-26 总决赛 G4 谁得分最高？", "intelligence_mode": "full"}
    )
    second = await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "这场比赛的过程呢？",
            "intelligence_mode": "full",
        }
    )

    assert second.status == "no_data"
    assert search.queries[0].keywords == [agent.search_query]
    assert {
        item.canonical_id for item in search.queries[0].subject_refs
    } >= {"2026-finals-g4", "okc", "bos"}


@pytest.mark.asyncio
async def test_agent_owns_structured_miss_to_search_transition() -> None:
    search = RecordingMatchupSearchProvider()
    primary = SearchAugmentedProvider(FixtureProvider(), search)
    gateway = ProviderGateway(primary, max_retries=0)
    agent = QueryThenSearchAgent()
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=gateway,
        agent_runtime=agent,
    )

    result = await usecase.handle(
        {"message": "2026尼克斯-马刺", "intelligence_mode": "full"}
    )

    assert len(search.queries) == 1
    assert search.queries[0].keywords == [agent.search_query]
    # The Agent performed the transition itself and returned a complete
    # synthesis, so this is an Agent answer rather than an observation repair.
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
    assert "总决赛交锋" in result.answer_markdown


@pytest.mark.asyncio
async def test_open_ended_game_summary_preserves_agent_synthesis() -> None:
    agent = NaturalSummaryAgent()
    usecase = ChatUseCase(
        FixtureProvider(), settings=settings(), agent_runtime=agent
    )

    result = await usecase.handle(
        {
            "message": "2025-26 总决赛 G4 给我概括一下",
            "intelligence_mode": "full",
        }
    )

    assert result.composition["mode"] == "agent"
    assert result.answer_markdown.startswith("简要概括：")
    assert "凯尔特人以 108–104 击败雷霆" in result.answer_markdown
    assert "杰伦·布朗" in result.answer_markdown


@pytest.mark.asyncio
async def test_news_query_synthesizes_mixed_search_observation() -> None:
    primary = SearchAugmentedProvider(FixtureProvider(), MatchupSearchProvider())
    gateway = ProviderGateway(primary, max_retries=0)
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=gateway,
        agent_runtime=MislabeledEvidenceQueryAgent(),
    )

    result = await usecase.handle(
        {
            "message": "2026尼克斯夺冠后有哪些阵容调整新闻？",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed", result.to_dict()
    # Public answers should lead with the synthesized content, not with the
    # retrieval/provenance workflow that produced it.
    assert "尼克斯与马刺第四场比赛报道" in result.answer_markdown
    assert not result.answer_markdown.startswith("根据相关公开报道")
    assert "公开资料线索（待交叉核验）" not in result.answer_markdown
    assert "不标记为已核验事实" not in result.answer_markdown
    assert "交叉核验" not in result.answer_markdown
    assert result.evidence_state == "partial"


@pytest.mark.asyncio
async def test_nba_query_uses_original_question_not_planner_paraphrase() -> None:
    usecase = ChatUseCase(
        FixtureProvider(),
        settings=settings(),
        agent_runtime=ParaphrasingQueryAgent(),
    )

    result = await usecase.handle(
        {
            "message": "2025-26 总决赛 G4 谁得分最高？",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert "杰伦·布朗" in result.answer_markdown
    assert "32 分" in result.answer_markdown
    assert "系列赛大比分" not in result.answer_markdown


@pytest.mark.asyncio
async def test_mixed_search_observation_keeps_agent_synthesis_instead_of_raw_list() -> None:
    primary = SearchAugmentedProvider(FixtureProvider(), MatchupSearchProvider())
    gateway = ProviderGateway(primary, max_retries=0)
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=gateway,
        agent_runtime=SynthesizingQueryAgent(),
    )

    result = await usecase.handle(
        {"message": "2026尼克斯-马刺", "intelligence_mode": "full"}
    )

    assert result.status == "completed"
    assert result.answer_markdown.startswith("公开报道普遍将其描述为")
    assert "公开资料线索（待交叉核验）" not in result.answer_markdown
    assert "具体赛果仍需交叉核验" in result.answer_markdown


@pytest.mark.asyncio
async def test_truncated_recap_is_recovered_before_public_output() -> None:
    primary = SearchAugmentedProvider(FixtureProvider(), MatchupSearchProvider())
    gateway = ProviderGateway(primary, max_retries=0)
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=gateway,
        agent_runtime=TruncatedRecapAgent(),
    )

    result = await usecase.handle(
        {
            "message": "2025-26 总决赛 G4 这场比赛是怎么个过程？",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert "工具预算" not in result.answer_markdown
    assert not result.answer_markdown.rstrip().endswith("## 过程讲解（公开报道线索）")
    assert "108–104" in result.answer_markdown
    assert result.composition["mode"] == "fallback"
    assert result.composition["status"] == "fallback"


@pytest.mark.asyncio
async def test_explicit_matchup_drops_every_unrelated_search_candidate() -> None:
    primary = SearchAugmentedProvider(
        FixtureProvider(),
        IrrelevantMatchupSearchProvider(),
    )
    gateway = ProviderGateway(primary, max_retries=0)
    usecase = ChatUseCase(
        primary,
        settings=settings(),
        gateway=gateway,
        agent_runtime=SearchingSmartAgent(),
    )

    result = await usecase.handle(
        {"message": "2026尼克斯-马刺", "intelligence_mode": "full"}
    )

    assert "Tony Parker" not in result.answer_markdown
    assert "offseason recap" not in result.answer_markdown
    assert "暂未检索到" in result.answer_markdown


def test_tactical_search_observation_gets_an_analytical_frame() -> None:
    raw = (
        "公开资料线索（待交叉核验）：\n"
        "- **库里挡拆防守策略**：优先挤过掩护，限制三分出手。\n"
        "- **勇士对位讨论**：弱侧需要及时轮转补位。"
    )
    answer = ChatUseCase._ground_agent_answer(
        "如果要限制库里，该怎么布置防守？",
        raw,
        observations=[],
    )
    assert answer.startswith("可参考的通用防守思路")
    assert "分析建议" in answer
    assert "公开资料线索" not in answer
    assert "交叉核验" not in answer
    assert "库里挡拆防守策略" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["nishishei", "你是谁", "nihao", "你能做什么"])
async def test_capability_questions_do_not_fall_back_to_nba_parser(question: str) -> None:
    agent = FakeSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)
    result = await usecase.handle({"message": question, "intelligence_mode": "full"})
    assert result.status == "completed"
    assert "请补充查询对象" not in result.answer_markdown
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
    assert usecase.telemetry.latest().agent_tool_names == []


@pytest.mark.asyncio
async def test_capability_question_has_local_answer_when_agent_unavailable() -> None:
    agent = FakeSmartAgent(unavailable=True)
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)
    result = await usecase.handle({"message": "你是谁", "intelligence_mode": "full"})
    assert result.status == "completed"
    assert "我是 COURTSIDE" in result.answer_markdown
    assert "请补充查询对象" not in result.answer_markdown
    assert result.composition == {
        "mode": "deterministic",
        "status": "not_requested",
        "latency_ms": 1,
    }


def test_user_question_is_passed_to_agent_unchanged() -> None:
    assert ChatUseCase._agent_question("下周有NBA的比赛买") == "下周有NBA的比赛买"
    assert ChatUseCase._agent_question("买球赔率") == "买球赔率"


def test_agent_prose_removes_internal_workflow_narration() -> None:
    answer = (
        "查询杜兰特新闻时，我先尝试了新闻工具三次，均返回安全校验失败；"
        "随后改用公开网页检索，拿到了以下线索：\n\n"
        "- **近期动态**：公开资料提到球队训练。"
    )
    cleaned = ChatUseCase._ground_agent_answer(
        "杜兰特近期有什么新闻？",
        answer,
        observations=[],
    )
    assert "新闻工具" not in cleaned
    assert "改用公开网页检索" not in cleaned
    assert "公开资料线索" not in cleaned
    assert "搜索" not in cleaned
    assert "目前可用资料不足以可靠回答" in cleaned


def test_truncated_recap_recovers_complete_mixed_observations() -> None:
    """A budget/heading-only model result must not cross the API boundary."""

    structured = {
        "status": "completed",
        "intent": "nba_query",
        "answer_markdown": "凯尔特人以 108–104 击败雷霆，比赛在终场前守住 4 分优势。",
        "evidence_state": "verified",
    }
    searched = {
        "status": "completed",
        "intent": "web_search",
        "answer_markdown": (
            "公开资料线索（待交叉核验）：\n"
            "- **末节报道**：凯尔特人在收官阶段加强防守并守住领先。\n"
            "- **赛后复盘**：报道将关键转折归因于末节执行。"
        ),
        "evidence_state": "partial",
    }
    truncated = "工具预算已用尽\n## 先给结论\n## 过程讲解（公开报道线索）"

    answer = ChatUseCase._ground_agent_answer(
        "这场比赛是怎么个过程，能给我讲讲吗",
        truncated,
        [structured, searched],
    )

    assert "工具预算" not in answer
    assert not answer.rstrip().endswith("## 过程讲解（公开报道线索）")
    assert "108–104" in answer
    assert "分析：" in answer
    assert "交叉核验" not in answer
    assert "收官阶段加强防守" in answer


def test_truncated_recap_does_not_append_noisy_search_to_complete_typed_analysis() -> None:
    """A complete game-scoped typed explanation wins over unrelated search prose."""

    structured = {
        "status": "completed",
        "intent": "nba_query",
        "answer_markdown": (
            "这是 **2025-26 系列赛 G5**，尼克斯以 **94–90** 取胜。\n\n"
            "尼克斯能赢下比赛，已核验的直接依据是末节这一回合后建立并守住了领先优势。\n\n"
            "**事实依据**：第四节还剩 7 秒，奥吉·阿努诺比罚球得到 1 分，"
            "将比分带到 **90–94**。"
        ),
        "evidence_state": "verified",
    }
    searched = {
        "status": "completed",
        "intent": "web_search",
        "answer_markdown": (
            "公开资料线索（待交叉核验）：\n"
            "- **收官战报道**：JR0941078292 3分钟前 远征球迷功劳也不小。"
            "尼克斯 G5 以94-90赢球。 另外，这是G4的史诗级逆转。"
            " 另外，布伦森常规赛MVP评选中未获得任何选票。"
        ),
        "evidence_state": "partial",
    }

    answer = ChatUseCase._ground_agent_answer(
        "最后一场比赛是怎样的，谁赢了怎么赢的",
        "工具预算已用尽\n## 结论",
        [structured, searched],
    )

    assert "事实依据" in answer
    assert "第四节还剩 7 秒" in answer
    for leaked in ("JR0941078292", "G4", "MVP评选", "另外，这是"):
        assert leaked not in answer


def test_clear_recap_does_not_keep_structured_clarification_when_search_exists() -> None:
    answer = ChatUseCase._ground_agent_answer(
        "2026-06-14 08:30 这场比赛是怎么个过程，能给我讲讲吗",
        "请补充具体比赛，我再帮您核对。",
        [
            {
                "status": "needs_clarification",
                "intent": "nba_query",
                "answer_markdown": "请补充具体比赛，我再帮您核对。",
                "evidence_state": "none",
            },
            {
                "status": "completed",
                "intent": "web_search",
                "answer_markdown": (
                    "公开资料线索（待交叉核验）：\n"
                    "- **比赛过程报道**：末节双方比分胶着，胜负在收官阶段确定。"
                ),
                "evidence_state": "partial",
            },
        ],
    )

    assert "请补充具体比赛" not in answer
    assert "收官阶段确定" in answer


def test_recap_drops_schedule_projection_when_agent_chose_schedule_tool() -> None:
    answer = ChatUseCase._ground_agent_answer(
        "2026-06-14 08:30 这场比赛是怎么个过程，能给我讲讲吗",
        "北京时间 2026-06-14 的 NBA 赛程：\n- 2026-06-14 08:30：尼克斯 vs 马刺（已结束，94–90）",
        [
            {
                "status": "completed",
                "intent": "schedule_result",
                "answer_markdown": (
                    "北京时间 2026-06-14 的 NBA 赛程：\n"
                    "- 2026-06-14 08:30：尼克斯 vs 马刺（已结束，94–90）"
                ),
                "evidence_state": "verified",
            },
            {
                "status": "completed",
                "intent": "web_search",
                "answer_markdown": (
                    "公开资料线索（待交叉核验）：\n"
                    "- **比赛过程报道**：尼克斯在收官阶段守住领先并赢球。"
                ),
                "evidence_state": "partial",
            },
        ],
    )

    assert "NBA 赛程" not in answer
    assert "收官阶段守住领先" in answer


def test_search_recovery_is_short_and_deduplicated() -> None:
    long_news = "。".join(
        [
            "新华社报道尼克斯与马刺的系列赛进展",
            "文章还列出了球员表现和比赛过程",
            "后文包含大量与本次问题无关的背景资料" * 30,
        ]
    ) + "。"
    blocks = [
        {"label": "新闻标题", "value": "尼克斯与马刺相关报道"},
        {"label": "新闻摘要", "content": long_news},
        {"label": "新闻标题", "value": "尼克斯与马刺相关报道"},
        {"label": "新闻摘要", "content": long_news},
        {"label": "新闻标题", "value": "另一条报道"},
        {"label": "新闻摘要", "content": "另一条报道的简短摘要。"},
    ]

    compact = ChatUseCase._compact_news_blocks(blocks)

    assert compact.startswith("公开资料线索（待交叉核验）：")
    assert compact.count("- **") == 2
    assert len(compact) < 900
    assert "大量与本次问题无关" not in compact


def test_combined_structured_miss_keeps_search_as_unverified_context() -> None:
    answer = ChatUseCase._compact_search_markdown(
        "公开资料检索到以下相关线索（仅作背景参考）：\n"
        "- **第一条报道**：第一条报道的摘要。\n"
        "- **第二条报道**：第二条报道的摘要。\n"
        "- **第三条报道**：第三条报道的摘要。\n"
        "- **第四条报道**：不应展示。"
    )

    assert answer.startswith("公开资料线索（待交叉核验）：")
    assert answer.count("- ") == 3
    assert "第四条报道" not in answer


def test_completed_search_is_synthesized_instead_of_rendered_as_result_list() -> None:
    raw = (
        "公开资料线索（待交叉核验）：\n"
        "- **尼克斯夺得总冠军**：纽约尼克斯在总决赛中以4-1战胜"
        "圣安东尼奥马刺，布伦森当选总决赛MVP。\n"
        "- **第五场赛后报道**：尼克斯在第五场以94-90取胜。"
    )
    observation = {
        "status": "completed",
        "intent": "web_search",
        "answer_markdown": raw,
    }

    answer = ChatUseCase._ground_agent_answer(
        "2026尼克斯-马刺",
        raw,
        [observation],
    )

    # Search evidence remains internal; recovery returns the synthesized
    # claim directly instead of narrating how it was retrieved.
    assert answer.startswith("纽约尼克斯在总决赛中以4-1战胜")
    assert "以4-1战胜" in answer
    assert "公开资料线索（待交叉核验）：" not in answer
    assert "不标记为已核验事实" not in answer
    assert "交叉核验" not in answer


def test_mixed_recap_cannot_label_search_details_as_verified_facts() -> None:
    answer = ChatUseCase._ground_agent_answer(
        "这场比赛是怎么个过程？",
        "**已核验的硬事实**\n\n- 终场比分 94–90。\n- 公开报道称末节完成逆转。",
        [
            {
                "status": "completed",
                "intent": "nba_query",
                "answer_markdown": "终场比分 94–90。",
            },
            {
                "status": "completed",
                "intent": "web_search",
                "answer_markdown": "公开报道线索：末节完成逆转。",
            },
        ],
    )

    assert "已核验的硬事实" not in answer
    assert "综合判断" in answer


def test_failed_model_search_answer_is_repaired_from_completed_observation() -> None:
    raw = (
        "公开资料线索（待交叉核验）：\n"
        "- **尼克斯夺冠**：尼克斯以4-1击败马刺，夺得2026年NBA总冠军。"
    )
    answer = ChatUseCase._ground_agent_answer(
        "2026尼克斯-马刺",
        "暂未找到两队的公开交手记录，请补充日期或场次。",
        [{"status": "completed", "intent": "web_search", "answer_markdown": raw}],
    )

    assert "暂未找到" not in answer
    assert "尼克斯以4-1击败马刺" in answer


def test_search_synthesis_prefers_the_question_topic_over_generic_outcome() -> None:
    raw = (
        "公开资料线索（待交叉核验）：\n"
        "- **尼克斯夺冠**：尼克斯以4-1击败马刺，夺得NBA总冠军。\n"
        "- **休赛期阵容观察**：尼克斯夺冠后的阵容调整聚焦内线深度和续约。\n"
        "- **尼克斯夺冠，篮网三巨头离队**：篮网完成多笔交易，阵容调整超过六成。\n"
        "- **纽约尼克斯队**：球队成立于1946年，主场设在麦迪逊广场花园。"
    )

    answer = ChatUseCase._synthesize_search_answer(
        "尼克斯夺冠后有哪些阵容调整新闻？",
        raw,
    )

    assert answer.startswith("根据相关公开报道")
    assert "阵容调整聚焦内线深度和续约" in answer.split("补充线索", 1)[0]
    assert "4-1击败马刺" not in answer
    assert "篮网" not in answer
    assert "成立于1946年" not in answer


def test_structured_miss_lead_does_not_claim_public_search_found_nothing() -> None:
    lead = ChatUseCase._structured_miss_lead(
        "暂未找到 **尼克斯 对 马刺** 2025-26 赛季的公开交手记录。"
    )

    assert lead.startswith("已找到与问题相关的公开报道")
    assert "暂未找到" not in lead
    assert "结构化比赛数据" in lead


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["下周有比赛买", "下周有比赛吗"])
async def test_full_agent_uses_schedule_tool_and_explains_empty_scope(question: str) -> None:
    clock = FixedClock(datetime(2026, 8, 30, 10, 4, tzinfo=UTC))
    agent = FakeSmartAgent()
    usecase = ChatUseCase(
        FixtureProvider(), settings=settings(), agent_runtime=agent, clock=clock
    )
    result = await usecase.handle({"message": question, "intelligence_mode": "full"})
    assert result.status == "completed"
    assert "2026-08-31" in result.answer_markdown
    assert "2026-09-06" in result.answer_markdown
    assert "没有返回 NBA 比赛" in result.answer_markdown
    assert result.composition["mode"] == "agent"
    assert usecase.telemetry.latest().agent_tool_names == ["nba_schedule"]
    assert agent.turns[-1].sanitized_question == question


@pytest.mark.asyncio
async def test_full_agent_tool_calls_share_one_provider_budget() -> None:
    config = replace(settings(), max_provider_operations=1)
    agent = RepeatingScheduleAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=config, agent_runtime=agent)

    result = await usecase.handle(
        {"message": "今天有哪些 NBA 比赛？", "intelligence_mode": "full"}
    )

    assert result.status == "completed"
    # The Agent may plan up to four tools, but only one upstream operation is
    # allowed by the shared request budget.
    assert usecase.gateway.call_count == 1
    assert usecase.telemetry.latest().agent_tool_call_count == 4


@pytest.mark.asyncio
async def test_empty_schedule_rejects_abbreviated_dates_and_offseason_speculation() -> None:
    clock = FixedClock(datetime(2026, 8, 30, 10, 4, tzinfo=UTC))
    agent = FakeSmartAgent(
        answer_override=(
            "下周（北京时间 8月31日至9月6日）没有比赛，按惯例这是休赛期。"
        )
    )
    usecase = ChatUseCase(
        FixtureProvider(), settings=settings(), agent_runtime=agent, clock=clock
    )
    result = await usecase.handle(
        {"message": "下周有比赛吗", "intelligence_mode": "full"}
    )
    assert "2026-08-31 至 2026-09-06" in result.answer_markdown
    assert "休赛期" not in result.answer_markdown
    assert "按惯例" not in result.answer_markdown
    assert result.composition["mode"] == "agent"


@pytest.mark.asyncio
async def test_agent_unavailable_falls_back_to_deterministic_path() -> None:
    agent = FakeSmartAgent(unavailable=True)
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)
    result = await usecase.handle(
        {"message": "2025-26 总决赛 G4 比分", "intelligence_mode": "full"}
    )
    assert result.status == "completed"
    assert "108–104" in result.answer_markdown
    assert result.composition == {"mode": "fallback", "status": "fallback", "latency_ms": 1}


@pytest.mark.asyncio
async def test_model_quota_uses_deterministic_facts_with_a_public_notice() -> None:
    usecase = ChatUseCase(
        FixtureProvider(),
        settings=settings(),
        agent_runtime=QuotaWithoutObservationAgent(),
    )
    events: list[tuple[str, dict]] = []

    async def sink(name, payload):
        events.append((name, dict(payload)))

    result = await usecase.handle(
        {"message": "2025-26 总决赛 G4 比分", "intelligence_mode": "full"},
        event_sink=sink,
    )

    assert result.status == "completed"
    assert "108–104" in result.answer_markdown
    assert result.notices == [
        {
            "code": "INTELLIGENCE_QUOTA_EXHAUSTED",
            "message": "智能回答额度已用完，当前无法完成智能分析。",
            "retryable": False,
        }
    ]
    terminal = [payload for name, payload in events if name == "message.completed"][-1]
    assert terminal["notices"] == result.notices


@pytest.mark.asyncio
async def test_model_quota_without_any_facts_is_a_typed_technical_failure() -> None:
    usecase = ChatUseCase(
        EmptyGameProvider(),
        settings=settings(),
        agent_runtime=QuotaWithoutObservationAgent(),
    )
    events: list[tuple[str, dict]] = []

    async def sink(name, payload):
        events.append((name, dict(payload)))

    result = await usecase.handle(
        {"message": "2025-26 总决赛 G4 比分", "intelligence_mode": "full"},
        event_sink=sink,
    )

    assert result.status == "failed"
    assert result.error == {
        "code": "COMPOSER_UNAVAILABLE",
        "retryable": False,
        "message": "智能回答服务当前不可用，暂时无法完成本次回答。",
    }
    assert result.notices[0]["code"] == "INTELLIGENCE_QUOTA_EXHAUSTED"
    terminal = [payload for name, payload in events if name == "run.error"][-1]
    assert terminal["notices"] == result.notices


@pytest.mark.asyncio
async def test_search_quota_without_fallback_is_a_visible_nonretryable_failure() -> None:
    usecase = ChatUseCase(QuotaGameProvider(), settings=settings())
    events: list[tuple[str, dict]] = []

    async def sink(name, payload):
        events.append((name, dict(payload)))

    result = await usecase.handle(
        {"message": "今天有哪些 NBA 比赛？", "intelligence_mode": "hybrid"},
        event_sink=sink,
    )

    assert result.status == "failed"
    assert result.error == {
        "code": "UPSTREAM_RATE_LIMITED",
        "retryable": False,
        "message": "在线检索额度已用完，暂时无法完成本次查询。",
    }
    assert result.notices == [
        {
            "code": "SEARCH_QUOTA_EXHAUSTED",
            "message": "在线检索额度已用完，当前无法补充公开资料。",
            "retryable": False,
        }
    ]
    terminal = [payload for name, payload in events if name == "run.error"][-1]
    assert terminal["notices"] == result.notices


@pytest.mark.asyncio
async def test_cached_observation_survives_model_quota_with_public_notice() -> None:
    usecase = ChatUseCase(
        FixtureProvider(),
        settings=settings(),
        agent_runtime=QuotaAfterObservationAgent(),
    )
    events: list[tuple[str, dict]] = []

    async def sink(name, payload):
        events.append((name, dict(payload)))

    result = await usecase.handle(
        {"message": "2025-26 总决赛 G4 比分", "intelligence_mode": "full"},
        event_sink=sink,
    )

    assert result.status == "completed"
    assert "108–104" in result.answer_markdown
    assert result.notices[0]["code"] == "INTELLIGENCE_QUOTA_EXHAUSTED"
    completed = [payload for name, payload in events if name == "message.completed"][-1]
    assert completed["notices"] == result.notices


def test_completed_search_fallback_still_produces_quota_notice() -> None:
    result = AgentTurnResult(
        status=RuntimeStatus.OK,
        answer_markdown="已从备用公开资料取得相关内容。",
        evidence_state="partial",
        observations=[
            {
                "status": "completed",
                "answer_markdown": "已从备用公开资料取得相关内容。",
                "evidence_state": "partial",
            }
        ],
        tool_calls=[
            AgentToolCall(
                "nba_search",
                "hash",
                "completed",
                1,
                error_code="SEARCH_QUOTA_EXHAUSTED",
                retryable=False,
            )
        ],
        latency_ms=1,
    )

    assert ChatUseCase._agent_public_notices(result) == [
        {
            "code": "SEARCH_QUOTA_EXHAUSTED",
            "message": "在线检索额度已用完，当前无法补充公开资料。",
            "retryable": False,
        }
    ]


@pytest.mark.asyncio
async def test_nested_typed_agent_query_keeps_search_quota_notice() -> None:
    usecase = ChatUseCase(
        UsableFixtureWithSearchQuotaNotice(),
        settings=settings(),
        agent_runtime=NestedQueryNoticeAgent(),
    )
    events: list[tuple[str, dict]] = []

    async def sink(name, payload):
        events.append((name, dict(payload)))

    result = await usecase.handle(
        {"message": "2025-26 总决赛 G4 比分", "intelligence_mode": "full"},
        event_sink=sink,
    )

    assert result.status == "completed"
    assert "108–104" in result.answer_markdown
    assert result.notices == [
        {
            "code": "SEARCH_QUOTA_EXHAUSTED",
            "message": "在线检索额度已用完，当前无法补充公开资料。",
            "retryable": False,
        }
    ]
    terminal = [payload for name, payload in events if name == "message.completed"][-1]
    assert terminal["notices"] == result.notices


@pytest.mark.asyncio
async def test_full_mode_selected_game_uses_agent_with_verified_tool_scope() -> None:
    """A clicked card scopes the Agent tool; it must not disable planning."""

    agent = QueryingSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)
    result = await usecase.handle(
        {
            "message": "雷霆对凯尔特人谁得分最高？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert "杰伦·布朗" in result.answer_markdown
    assert "32 分" in result.answer_markdown
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
    assert len(agent.turns) == 1
    assert "2025-26 总决赛 G4" in (agent.turns[0].context_hint or "")
    assert usecase.telemetry.latest().agent_tool_names == ["nba_query"]


@pytest.mark.asyncio
async def test_full_mode_selected_game_venue_keeps_snapshot_origin() -> None:
    agent = QueryingSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    result = await usecase.handle(
        {
            "message": "这场比赛在哪儿进行的？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert "TD Garden" in result.answer_markdown
    assert "Boston" in result.answer_markdown
    assert "108–104" not in result.answer_markdown
    assert result.data_origin == "demo_snapshot"
    assert result.as_of_beijing is None
    assert result.composition["mode"] == "agent"


@pytest.mark.asyncio
async def test_full_mode_selected_game_missing_coaches_does_not_return_score_summary() -> None:
    agent = QueryingSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    result = await usecase.handle(
        {
            "message": "这场比赛双方教练都是谁？",
            "selected_game_id": "2026-demo-den-gsw",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert "教练" in result.answer_markdown
    assert "暂无" in result.answer_markdown or "无法核验" in result.answer_markdown
    assert "103–99" not in result.answer_markdown
    assert "分差" not in result.answer_markdown
    assert "总得分" not in result.answer_markdown
    assert result.composition["mode"] == "agent"


@pytest.mark.asyncio
async def test_full_mode_selected_game_coaches_are_grounded_when_available() -> None:
    agent = QueryingSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    result = await usecase.handle(
        {
            "message": "这场比赛双方教练都是谁？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert "乔·马祖拉" in result.answer_markdown
    assert "马克·戴格诺特" in result.answer_markdown
    assert "108–104" not in result.answer_markdown
    assert result.composition["mode"] == "agent"


@pytest.mark.asyncio
async def test_objective_agent_paraphrase_cannot_invert_selected_game_winner() -> None:
    agent = AdversarialQueryingAgent("雷霆以 108–104 战胜凯尔特人。")
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    result = await usecase.handle(
        {
            "message": "这场比赛谁赢了？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert "凯尔特人" in result.answer_markdown
    assert "雷霆以 108–104 战胜凯尔特人" not in result.answer_markdown
    assert "108–104" in result.answer_markdown
    assert result.composition["mode"] == "agent"


@pytest.mark.asyncio
async def test_objective_agent_paraphrase_cannot_turn_free_throw_into_field_goal() -> None:
    agent = AdversarialQueryingAgent(
        "最后一投是谢伊·吉尔杰斯-亚历山大在 5 秒时完成的上篮。"
    )
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    result = await usecase.handle(
        {
            "message": "这场比赛最后 5 秒发生了什么？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert "罚球" in result.answer_markdown
    assert "上篮" not in result.answer_markdown
    assert "终场" in result.answer_markdown
    assert result.composition["mode"] == "agent"


@pytest.mark.asyncio
async def test_invalid_model_prose_keeps_valid_observation_as_explicit_fallback() -> None:
    agent = AdversarialQueryingAgent(
        "Kevin Durant 得到 999 分，凯尔特人赢球。"
    )
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    result = await usecase.handle(
        {
            "message": "凯尔特人为什么能赢下这场比赛？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert result.composition["mode"] == "fallback"
    assert result.composition["status"] == "fallback"
    assert "凯尔特人" in result.answer_markdown
    assert "999" not in result.answer_markdown
    assert "Kevin Durant" not in result.answer_markdown


@pytest.mark.asyncio
async def test_tool_observation_survives_agent_boundary_without_clarification_fallback() -> None:
    agent = ObservationOnlyAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    result = await usecase.handle(
        {
            "message": "雷霆对凯尔特人谁得分最高？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert result.composition["mode"] == "fallback"
    assert result.composition["status"] == "fallback"
    assert "杰伦·布朗" in result.answer_markdown
    assert "请补充" not in result.answer_markdown


@pytest.mark.asyncio
async def test_explicit_public_reverification_resolves_snapshot_to_public_event() -> None:
    primary = PublicMirrorProvider()
    snapshot = FixtureProvider()
    gateway = ProviderGateway(primary, fallback=snapshot, max_retries=0)
    usecase = ChatUseCase(
        primary,
        gateway=gateway,
        settings=settings(),
        agent_runtime=QueryingSmartAgent(),
    )

    result = await usecase.handle(
        {
            "message": "你去联网实时查验下",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert result.data_origin == "public"
    assert "已通过公开赛事记录重新核验" in result.answer_markdown
    assert "TD Garden" in result.answer_markdown
    assert "无法实时联网" not in result.answer_markdown
    assert snapshot.calls == 0
    assert primary.operation_calls["search_games"] == 1
    assert primary.operation_calls["get_game_summary"] == 1


@pytest.mark.asyncio
async def test_explicit_public_reverification_never_falls_back_to_snapshot() -> None:
    primary = PublicMirrorProvider(scenario="empty")
    snapshot = FixtureProvider()
    gateway = ProviderGateway(primary, fallback=snapshot, max_retries=0)
    usecase = ChatUseCase(
        primary,
        gateway=gateway,
        settings=settings(),
        agent_runtime=QueryingSmartAgent(),
    )

    result = await usecase.handle(
        {
            "message": "请用公开数据联网重新核验",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert result.data_origin == "none"
    assert "没有找到与当前对阵唯一匹配的比赛" in result.answer_markdown
    assert "不能升级为实时公开核验" in result.answer_markdown
    assert "无法实时联网" not in result.answer_markdown
    assert snapshot.calls == 0


@pytest.mark.asyncio
async def test_full_mode_selected_game_tactical_echo_is_marked_as_fallback() -> None:
    agent = QueryingSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    result = await usecase.handle(
        {
            "message": "把整场的双方战术说一下",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert result.composition["mode"] == "fallback"
    assert result.composition["status"] == "fallback"
    assert "凯尔特人" in result.answer_markdown
    assert "雷霆" in result.answer_markdown


@pytest.mark.asyncio
async def test_hybrid_selected_game_stays_deterministic() -> None:
    agent = QueryingSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    result = await usecase.handle(
        {
            "message": "雷霆对凯尔特人谁得分最高？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "hybrid",
        }
    )

    assert result.status == "completed"
    assert result.composition["mode"] == "deterministic"
    assert agent.turns == []


@pytest.mark.asyncio
async def test_selected_game_agent_failure_falls_back_to_same_verified_game() -> None:
    agent = FakeSmartAgent(unavailable=True)
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    result = await usecase.handle(
        {
            "message": "雷霆对凯尔特人谁得分最高？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )

    assert result.status == "completed"
    assert result.composition["mode"] == "fallback"
    assert result.composition["status"] == "fallback"
    assert "杰伦·布朗" in result.answer_markdown
    assert "32 分" in result.answer_markdown


@pytest.mark.asyncio
async def test_agent_receives_bounded_multi_turn_hint() -> None:
    agent = FakeSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)
    first = await usecase.handle({"message": "nihao", "intelligence_mode": "full"})
    await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "hello",
            "intelligence_mode": "full",
        }
    )
    assert agent.turns[1].opaque_session_id == agent.turns[0].opaque_session_id
    assert [
        item.model_dump(mode="python")
        for item in agent.turns[1].conversation_history
    ] == [
        {"role": "user", "content": "nihao"},
        {
            "role": "assistant",
            "content": "您好！我可以帮您查询 NBA 赛程、比赛和球员表现。",
        },
    ]


@pytest.mark.asyncio
async def test_natural_session_history_questions_bypass_agent_and_provider() -> None:
    agent = QueryingSmartAgent()
    provider = FixtureProvider()
    usecase = ChatUseCase(provider, settings=settings(), agent_runtime=agent)

    first = await usecase.handle({"message": "你好", "intelligence_mode": "full"})
    await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "2025-26 总决赛 G4 谁得分最高？",
            "intelligence_mode": "full",
        }
    )
    calls_before_meta = provider.calls
    turns_before_meta = len(agent.turns)

    recalled = await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "我刚才第二个问题问的什么？",
            "intelligence_mode": "full",
        }
    )
    counted = await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "到现在我一共问了几个问题？",
            "intelligence_mode": "full",
        }
    )

    assert r"2025\-26 总决赛 G4 谁得分最高" in recalled.answer_markdown
    assert "第 **4** 个问题" in counted.answer_markdown
    assert len(agent.turns) == turns_before_meta
    assert provider.calls == calls_before_meta


@pytest.mark.asyncio
async def test_new_application_session_starts_new_hermes_history() -> None:
    agent = FakeSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    first = await usecase.handle({"message": "nihao", "intelligence_mode": "full"})
    second = await usecase.handle({"message": "hello", "intelligence_mode": "full"})

    assert first.session_id != second.session_id
    assert agent.turns[0].opaque_session_id != agent.turns[1].opaque_session_id
    assert agent.turns[0].conversation_history == []
    assert agent.turns[1].conversation_history == []


@pytest.mark.asyncio
async def test_new_full_session_does_not_inherit_selected_game() -> None:
    agent = QueryingSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    first = await usecase.handle(
        {
            "message": "雷霆对凯尔特人谁得分最高？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )
    fresh = await usecase.handle(
        {
            "message": "那场最后5秒发生了什么？",
            "intelligence_mode": "full",
        }
    )

    assert first.session_id != fresh.session_id
    assert agent.turns[0].opaque_session_id != agent.turns[1].opaque_session_id
    assert agent.turns[1].conversation_history == []
    assert "2025-26 总决赛 G4" not in (agent.turns[1].context_hint or "")
    assert "请补充具体比赛" in fresh.answer_markdown
    assert "谢伊·吉尔杰斯-亚历山大" not in fresh.answer_markdown


@pytest.mark.asyncio
async def test_full_agent_keeps_selected_game_across_three_turns() -> None:
    agent = QueryingSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    first = await usecase.handle(
        {
            "message": "雷霆对凯尔特人谁得分最高？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )
    second = await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "那场最后5秒发生了什么？",
            "intelligence_mode": "full",
        }
    )
    third = await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "最后那个球是谁？",
            "intelligence_mode": "full",
        }
    )

    assert all(result.composition["mode"] == "agent" for result in (first, second, third))
    assert "杰伦·布朗" in first.answer_markdown
    assert "谢伊·吉尔杰斯-亚历山大" in second.answer_markdown
    assert "谢伊·吉尔杰斯-亚历山大" in third.answer_markdown
    assert len(agent.turns[2].conversation_history) == 4
    assert {turn.opaque_session_id for turn in agent.turns} == {
        agent.turns[0].opaque_session_id
    }
    # Conversation history may resolve pronouns, but every factual turn still
    # re-enters the verified NBA query tool instead of trusting an old answer.
    assert agent.tool_names == ["nba_query", "nba_query", "nba_query"]


@pytest.mark.asyncio
async def test_full_agent_persists_explicit_game_for_follow_up_without_card_id() -> None:
    """An explicit first-turn G4 reference scopes later pronoun questions."""

    agent = QueryingSmartAgent()
    usecase = ChatUseCase(FixtureProvider(), settings=settings(), agent_runtime=agent)

    first = await usecase.handle(
        {"message": "2025-26 总决赛 G4 谁得分最高？", "intelligence_mode": "full"}
    )
    second = await usecase.handle(
        {
            "session_id": first.session_id,
            "message": "这场比赛最后 5 秒发生了什么？",
            "intelligence_mode": "full",
        }
    )

    assert first.status == "completed"
    assert second.status == "completed"
    assert "2 个回合" in second.answer_markdown
    assert "谢伊·吉尔杰斯-亚历山大" in second.answer_markdown
    assert second.composition["mode"] == "agent"
    assert "2025-26 总决赛 G4" in (agent.turns[1].context_hint or "")


@pytest.mark.asyncio
async def test_wrong_schedule_tool_for_recent_pbp_falls_back_to_verified_replay() -> None:
    usecase = ChatUseCase(
        FixtureProvider(), settings=settings(), agent_runtime=WrongToolAgent()
    )
    result = await usecase.handle(
        {"message": "最近一场比赛的关键回合是什么？", "intelligence_mode": "full"}
    )
    assert result.status == "completed"
    assert "2 个回合" in result.answer_markdown
    assert result.composition["mode"] == "fallback"
    assert usecase.telemetry.latest().fallback_reason == "agent_tool_mismatch"


@pytest.mark.asyncio
async def test_schedule_observation_from_generic_query_is_rejected_for_recap() -> None:
    usecase = ChatUseCase(
        FixtureProvider(), settings=settings(), agent_runtime=WrongIntentQueryAgent()
    )
    result = await usecase.handle(
        {
            "message": "2026-06-14 08:30 这场比赛是怎么个过程，能给我讲讲吗",
            "intelligence_mode": "full",
        }
    )
    assert result.composition["mode"] == "fallback"
    assert usecase.telemetry.latest().fallback_reason == "agent_tool_mismatch"
    assert "NBA 赛程" not in result.answer_markdown


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "杜兰特近期出场次数",
        "凯尔特人为什么能限制对手的挡拆？",
        "最近的 NBA 新闻是什么？",
    ],
)
async def test_wrong_schedule_tool_does_not_answer_unrelated_question(question: str) -> None:
    """A schedule observation must not be accepted for another query type."""

    usecase = ChatUseCase(
        FixtureProvider(), settings=settings(), agent_runtime=WrongToolAgent()
    )
    result = await usecase.handle({"message": question, "intelligence_mode": "full"})

    assert result.composition["mode"] == "fallback"
    assert usecase.telemetry.latest().fallback_reason == "agent_tool_mismatch"
    assert "2026-09-07 至 2026-09-13" not in result.answer_markdown


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "乔丹和詹姆斯谁更伟大？请说出判断依据",
        "凯尔特人为什么能限制对手的挡拆？",
    ],
)
async def test_wrong_tool_open_analysis_never_falls_into_unrelated_fixture(
    question: str,
) -> None:
    usecase = ChatUseCase(
        FixtureProvider(), settings=settings(), agent_runtime=WrongToolAgent()
    )

    result = await usecase.handle({"message": question, "intelligence_mode": "full"})

    assert result.status == "no_data"
    assert result.composition == {
        "mode": "fallback",
        "status": "fallback",
        "latency_ms": 1,
    }
    assert "请补充查询对象" not in result.answer_markdown
    assert "雷霆" not in result.answer_markdown
    assert "凯尔特人以" not in result.answer_markdown
    assert "108–104" not in result.answer_markdown
