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
        if turn.sanitized_question.lower() in {"nihao", "hello"}:
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
    assert agent.turns[1].context_hint is not None
    assert "上轮摘要" in agent.turns[1].context_hint
