from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.application.ports import RuntimeStatus
from apps.api.src.config import Settings
from apps.api.src.domain.time_policy import FixedClock
from apps.api.src.infrastructure.agent_tools import AgentToolCall
from apps.api.src.infrastructure.hermes_agent_runtime import AgentTurnResult
from apps.api.src.providers.fixture_provider import FixtureProvider


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
    assert result.status == "completed"
    assert "您好" in result.answer_markdown
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"


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


def test_obvious_schedule_typo_is_corrected_only_for_hermes() -> None:
    assert ChatUseCase._agent_question("下周有NBA的比赛买") == "下周有NBA的比赛吗"
    assert ChatUseCase._agent_question("买球赔率") == "买球赔率"


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
    assert agent.turns[-1].sanitized_question.endswith("吗")


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
async def test_full_mode_selected_game_tactical_question_uses_agent() -> None:
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
    assert result.composition["mode"] == "agent"
    assert result.composition["status"] == "used"
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
