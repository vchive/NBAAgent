from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.application.ports import ProviderResult, RuntimeStatus
from apps.api.src.config import Settings
from apps.api.src.domain.models import Evidence, Freshness, NewsItem, SourceClass, TrustLevel
from apps.api.src.infrastructure.agent_tools import AgentToolCall
from apps.api.src.infrastructure.hermes_agent_runtime import AgentTurnResult
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway
from apps.api.src.providers.search_augmented_provider import SearchAugmentedProvider


class ComparisonSearchProvider:
    """Return mixed candidates so the application must retain both subjects."""

    def __init__(self) -> None:
        self.queries = []

    async def search_web(self, query, budget):
        self.queries.append(query)
        now = datetime.now(UTC)
        evidence = Evidence(
            evidence_id="search:comparison",
            source_class=SourceClass.SEARCH,
            source_ref="search.comparison",
            url="https://example.test/nba-comparison",
            fetched_at_utc=now,
            trust=TrustLevel.MEDIUM,
            freshness=Freshness.UNKNOWN,
        )
        player_ids = {item.canonical_id for item in query.subject_refs}
        if "michael-jordan" in player_ids:
            rows = [
                (
                    "库里投射影响力回顾",
                    "斯蒂芬·库里的三分投射改变了现代进攻空间。",
                ),
                (
                    "迈克尔·乔丹生涯回顾",
                    "乔丹的荣誉、冠军成绩与个人峰值构成历史地位的重要依据。",
                ),
                (
                    "勒布朗·詹姆斯生涯回顾",
                    "詹姆斯的生涯长度、稳定性、组织能力与多角色适应性突出。",
                ),
                (
                    "乔丹与詹姆斯历史地位比较",
                    "乔丹和詹姆斯的比较通常涉及团队成绩、个人峰值、生涯长度及时代角色。",
                ),
            ]
        else:
            rows = [
                (
                    "约基奇组织能力回顾",
                    "约基奇以中锋位置的传球和组织能力著称。",
                ),
                (
                    "文班亚马与邓肯历史地位比较",
                    "文班亚马和邓肯所处生涯阶段不同，应比较荣誉、峰值、稳定性和时代角色。",
                ),
                (
                    "维克托·文班亚马生涯观察",
                    "文班亚马仍处生涯积累阶段，个人上限与攻防潜力受到关注。",
                ),
                (
                    "蒂姆·邓肯生涯回顾",
                    "邓肯拥有完整而稳定的长期履历，并长期承担球队核心角色。",
                ),
            ]
        items = [
            NewsItem(
                news_id=f"comparison:{index}",
                title=title,
                summary=summary,
                evidence_id=evidence.evidence_id,
            )
            for index, (title, summary) in enumerate(rows)
        ]
        return ProviderResult(
            data=items,
            evidence=[evidence],
            partial=True,
            retrieved_at_utc=now,
        )


class OneSidedComparisonSearchProvider:
    """Simulate a provider that finds useful material for only one subject."""

    def __init__(self) -> None:
        self.queries = []

    async def search_web(self, query, budget):
        self.queries.append(query)
        now = datetime.now(UTC)
        evidence = Evidence(
            evidence_id="search:one-sided-comparison",
            source_class=SourceClass.SEARCH,
            source_ref="search.one-sided-comparison",
            url="https://example.test/jordan-profile",
            fetched_at_utc=now,
            trust=TrustLevel.MEDIUM,
            freshness=Freshness.UNKNOWN,
        )
        return ProviderResult(
            data=[
                NewsItem(
                    news_id="comparison:jordan-only",
                    title="迈克尔·乔丹生涯回顾",
                    summary="乔丹的球队成绩与个人峰值是评价历史地位的常见维度。",
                    evidence_id=evidence.evidence_id,
                )
            ],
            evidence=[evidence],
            partial=True,
            retrieved_at_utc=now,
        )


class ComparisonSynthesisAgent:
    """Exercise search grounding while returning an actual comparative judgment."""

    mode = "embedded_agent"
    model = "test-model"

    def __init__(self) -> None:
        self.turns = []
        self.observations = []

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        observation = dict(
            await tool_runner("nba_search", {"query": turn.sanitized_question})
        )
        self.observations.append(observation)
        if "乔丹" in turn.sanitized_question:
            answer = (
                "如果把巅峰统治力和冠军舞台表现放在首位，我会选 **乔丹**；"
                "若更看重漫长生涯里的稳定输出和全面组织，**詹姆斯**更有优势。\n\n"
                "- **荣誉与团队成绩**：乔丹的优势更集中，詹姆斯的竞争力延续更久。\n"
                "- **个人峰值**：乔丹的得分压迫和攻防统治力更突出。\n"
                "- **生涯长度与稳定性**：詹姆斯更占优势。\n"
                "- **时代与角色差异**：乔丹更偏终结核心，詹姆斯还长期承担组织职责。\n\n"
                "所以我的选择是乔丹，但“更伟大”没有唯一客观口径；改变权重，结论也会变。"
            )
        else:
            answer = (
                "以已经完成的生涯履历来评，我目前会选 **邓肯**；"
                "**文班亚马**仍在积累，现阶段更适合比较发展轨迹而非直接下历史定论。\n\n"
                "- **荣誉与团队成绩**：邓肯已有完整的球队核心履历。\n"
                "- **个人峰值**：文班亚马展现出罕见上限，邓肯的峰值则已经被长期成绩验证。\n"
                "- **生涯长度与稳定性**：这一维度目前明显有利于邓肯。\n"
                "- **时代与角色差异**：两人的进攻空间和防守任务并不相同。\n\n"
                "因此现阶段选邓肯更稳妥，但“更伟大”没有唯一客观口径。"
            )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=answer,
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall("nba_search", "comparison", observation["status"], 1)
            ],
            latency_ms=2,
            iteration_count=2,
        )


class ExplicitStandardComparisonAgent:
    """Respect a user-selected criterion without padding the answer."""

    mode = "embedded_agent"
    model = "test-model"

    async def run(self, turn, *, tool_runner, cancel):
        observation = dict(
            await tool_runner("nba_search", {"query": turn.sanitized_question})
        )
        answer = (
            "只按得分能力，我会选 **乔丹**：他的个人峰值和得分压迫更突出。"
            "**詹姆斯**的全面组织与生涯长度是另一套评价标准，"
            "但不改变这次按得分能力作出的选择。"
        )
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown=answer,
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall("nba_search", "comparison", observation["status"], 1)
            ],
            latency_ms=2,
            iteration_count=2,
        )


class TimeoutAfterComparisonSearchAgent:
    """Stop after one useful search observation without producing final prose."""

    mode = "embedded_agent"
    model = "test-model"

    def __init__(self) -> None:
        self.turns = []

    async def run(self, turn, *, tool_runner, cancel):
        self.turns.append(turn)
        observation = dict(
            await tool_runner("nba_search", {"query": turn.sanitized_question})
        )
        return AgentTurnResult(
            status=RuntimeStatus.TIMEOUT,
            answer_markdown=None,
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall("nba_search", "comparison-timeout", "completed", 1)
            ],
            latency_ms=2,
            iteration_count=turn.max_iterations,
            finish_reason="timeout_after_search",
        )


class IncompleteOneSidedComparisonAgent:
    """Return an incomplete comparison after a one-sided search observation."""

    mode = "embedded_agent"
    model = "test-model"

    def __init__(self) -> None:
        self.observations = []

    async def run(self, turn, *, tool_runner, cancel):
        observation = dict(
            await tool_runner("nba_search", {"query": turn.sanitized_question})
        )
        self.observations.append(observation)
        return AgentTurnResult(
            status=RuntimeStatus.OK,
            answer_markdown="目前只查到乔丹的资料，无法比较，请补充查询对象。",
            evidence_state=observation["evidence_state"],
            observations=[observation],
            tool_calls=[
                AgentToolCall("nba_search", "one-sided-comparison", observation["status"], 1)
            ],
            latency_ms=2,
            iteration_count=2,
        )


def _settings() -> Settings:
    return Settings(
        public_data_mode="hybrid",
        full_intelligence_enabled=True,
        default_intelligence_mode="full",
        llm_mode="live",
        runtime_profile="hybrid",
        hermes_lite_mode="embedded_agent",
        siliconflow_api_key="test-key",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "player_ids", "names", "unrelated"),
    [
        (
            "乔丹和詹姆斯谁更伟大？请说出判断依据",
            {"michael-jordan", "lebron-james"},
            ("乔丹", "詹姆斯"),
            "库里",
        ),
        (
            "文班亚马和邓肯谁更伟大？",
            {"victor-wembanyama", "tim-duncan"},
            ("文班亚马", "邓肯"),
            "约基奇",
        ),
    ],
)
async def test_full_agent_synthesizes_two_player_greatness_comparison(
    question: str,
    player_ids: set[str],
    names: tuple[str, str],
    unrelated: str,
) -> None:
    search = ComparisonSearchProvider()
    provider = SearchAugmentedProvider(FixtureProvider(), search)
    agent = ComparisonSynthesisAgent()
    usecase = ChatUseCase(
        provider,
        gateway=ProviderGateway(provider, max_retries=0),
        settings=_settings(),
        agent_runtime=agent,
    )

    result = await usecase.handle(
        {"message": question, "intelligence_mode": "full"}
    )

    assert result.status == "completed"
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
    assert agent.turns[0].sanitized_question == question
    assert agent.turns[0].max_tool_calls == 1
    assert agent.turns[0].max_iterations == 3
    assert len(search.queries) == 1
    assert {
        item.canonical_id for item in search.queries[0].subject_refs
    } == player_ids
    assert agent.observations[0]["status"] == "completed"
    first_grounding_item = agent.observations[0]["answer_markdown"].splitlines()[1]
    for name in names:
        # A direct head-to-head source is ranked before one-player profiles,
        # so the bounded observation cannot spend all three slots on one side.
        assert name in first_grounding_item
        assert name in agent.observations[0]["answer_markdown"]
        assert name in result.answer_markdown
    assert unrelated not in agent.observations[0]["answer_markdown"]
    assert unrelated not in result.answer_markdown
    for dimension in (
        "荣誉与团队成绩",
        "个人峰值",
        "生涯长度与稳定性",
        "时代与角色差异",
    ):
        assert dimension in result.answer_markdown
    assert "没有唯一客观口径" in result.answer_markdown
    for leaked in (
        "请补充查询对象",
        "公开资料线索",
        "补充线索",
        "待交叉核验",
        "生涯回顾",
        "历史地位比较",
    ):
        assert leaked not in result.answer_markdown


@pytest.mark.asyncio
async def test_full_agent_respects_user_supplied_comparison_standard() -> None:
    question = "乔丹和詹姆斯只按得分能力谁更强？"
    search = ComparisonSearchProvider()
    provider = SearchAugmentedProvider(FixtureProvider(), search)
    result = await ChatUseCase(
        provider,
        gateway=ProviderGateway(provider, max_retries=0),
        settings=_settings(),
        agent_runtime=ExplicitStandardComparisonAgent(),
    ).handle({"message": question, "intelligence_mode": "full"})

    assert result.status == "completed"
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
    assert "乔丹" in result.answer_markdown
    assert "詹姆斯" in result.answer_markdown
    assert "得分能力" in result.answer_markdown
    assert "请补充" not in result.answer_markdown


@pytest.mark.asyncio
async def test_timed_out_player_comparison_recovers_as_bounded_dimension_framework() -> None:
    question = "乔丹和詹姆斯谁更伟大？请说出判断依据"
    search = ComparisonSearchProvider()
    provider = SearchAugmentedProvider(FixtureProvider(), search)
    agent = TimeoutAfterComparisonSearchAgent()
    result = await ChatUseCase(
        provider,
        gateway=ProviderGateway(provider, max_retries=0),
        settings=_settings(),
        agent_runtime=agent,
    ).handle({"message": question, "intelligence_mode": "full"})

    assert result.status == "completed"
    assert result.composition["mode"] == "fallback"
    assert result.composition["status"] == "fallback"
    assert len(search.queries) == 1
    assert agent.turns[0].max_tool_calls == 1
    assert agent.turns[0].max_iterations == 3
    for expected in (
        "乔丹",
        "詹姆斯",
        "没有唯一客观口径",
        "荣誉与团队成绩",
        "冠军",
        "季后赛",
        "个人峰值与数据",
        "得分",
        "生涯长度与稳定性",
        "时代与角色差异",
        "影响力",
    ):
        assert expected in result.answer_markdown
    for leaked_or_raw in (
        "公开资料线索",
        "待交叉核验",
        "另外，",
        "永恒的辩论",
        "生涯回顾",
        "历史地位比较",
    ):
        assert leaked_or_raw not in result.answer_markdown


@pytest.mark.asyncio
async def test_full_agent_repairs_one_sided_player_comparison_without_downgrade() -> None:
    question = "乔丹和詹姆斯谁更伟大？请说出判断依据"
    search = OneSidedComparisonSearchProvider()
    provider = SearchAugmentedProvider(FixtureProvider(), search)
    agent = IncompleteOneSidedComparisonAgent()
    result = await ChatUseCase(
        provider,
        gateway=ProviderGateway(provider, max_retries=0),
        settings=_settings(),
        agent_runtime=agent,
    ).handle({"message": question, "intelligence_mode": "full"})

    assert len(search.queries) == 1
    # Search projection intentionally refuses to present one player's profile
    # as balanced evidence for a head-to-head judgment. The completed Agent
    # turn must still answer with a fact-free evaluation framework.
    assert agent.observations[0]["status"] == "no_data"
    assert result.status == "completed"
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
    for expected in (
        "乔丹",
        "詹姆斯",
        "没有唯一客观口径",
        "荣誉与团队成绩",
        "个人峰值与数据",
        "生涯长度与稳定性",
        "时代与角色差异",
    ):
        assert expected in result.answer_markdown
    for leaked_or_unsupported in (
        "请补充查询对象",
        "公开资料线索",
        "待交叉核验",
        "总冠军数",
        "场均",
    ):
        assert leaked_or_unsupported not in result.answer_markdown
