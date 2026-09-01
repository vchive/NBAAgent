from __future__ import annotations

from uuid import uuid4

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.config import Settings
from apps.api.src.providers.fixture_provider import FixtureProvider


class CountingAgent:
    mode = "embedded_agent"
    model = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("session metadata must not invoke the Agent")


def full_settings() -> Settings:
    return Settings(
        full_intelligence_enabled=True,
        default_intelligence_mode="hybrid",
        llm_mode="live",
        runtime_profile="hybrid",
        hermes_lite_mode="embedded_agent",
        siliconflow_api_key="test-key",
    )


@pytest.mark.asyncio
async def test_fresh_session_count_is_explicit_and_committed() -> None:
    usecase = ChatUseCase(FixtureProvider())

    result = await usecase.handle({"message": "我问了你几个问题？"})

    assert result.status == "completed"
    assert "此前问了 **0** 个问题" in result.answer_markdown
    assert "第 **1** 个问题" in result.answer_markdown
    context = await usecase.context_manager.load(result.session_id)
    assert context is not None
    assert context.completed_user_turn_count == 1
    assert usecase.gateway.counters()["provider_call_count"] == 0


@pytest.mark.asyncio
async def test_count_remains_accurate_beyond_bounded_history_window() -> None:
    usecase = ChatUseCase(FixtureProvider())
    session_id = uuid4()

    for _ in range(10):
        result = await usecase.handle({"session_id": session_id, "message": "你好"})
        assert result.status == "completed"

    counted = await usecase.handle(
        {"session_id": session_id, "message": "这是我第几个问题？"}
    )

    assert "此前问了 **10** 个问题" in counted.answer_markdown
    assert "第 **11** 个问题" in counted.answer_markdown
    context = await usecase.context_manager.load(session_id)
    assert context is not None
    assert context.completed_user_turn_count == 11
    assert context.turn_count == 8
    assert len(context.recent_turn_summaries) == 8
    assert context.recent_turn_summaries[-1].turn_index == 11


@pytest.mark.asyncio
async def test_idempotency_replay_does_not_increment_turn_count() -> None:
    usecase = ChatUseCase(FixtureProvider())
    session_id = uuid4()
    payload = {
        "session_id": session_id,
        "message": "你好",
        "client_message_id": "same-message",
    }

    first = await usecase.handle(payload)
    replay = await usecase.handle(payload)
    assert replay.request_id == first.request_id

    counted = await usecase.handle(
        {"session_id": session_id, "message": "我问了你几个问题？"}
    )
    assert "此前问了 **1** 个问题" in counted.answer_markdown


@pytest.mark.asyncio
async def test_last_question_last_answer_and_summary_use_only_current_session() -> None:
    usecase = ChatUseCase(FixtureProvider())
    first = await usecase.handle({"message": "你好"})

    last_question = await usecase.handle(
        {"session_id": first.session_id, "message": "我刚才问了什么？"}
    )
    assert "你好" in last_question.answer_markdown

    separate = await usecase.handle({"message": "你好"})
    last_answer = await usecase.handle(
        {"session_id": separate.session_id, "message": "你刚才回答了什么？"}
    )
    assert "COURTSIDE" in last_answer.answer_markdown

    summary = await usecase.handle(
        {"session_id": first.session_id, "message": "总结一下我们刚才聊了什么"}
    )
    assert "你好" in summary.answer_markdown
    assert "我刚才问了什么" in summary.answer_markdown
    assert separate.session_id != first.session_id


@pytest.mark.asyncio
async def test_indexed_question_reads_the_requested_retained_turn_without_agent() -> None:
    agent = CountingAgent()
    usecase = ChatUseCase(
        FixtureProvider(), settings=full_settings(), agent_runtime=agent
    )
    session_id = uuid4()
    questions = [
        "你好",
        "你是谁",
        "2025-26 总决赛 G4 最后 5 秒发生了什么？",
        "当前选中的比赛是什么",
    ]
    for question in questions:
        result = await usecase.handle(
            {
                "session_id": session_id,
                "message": question,
                "intelligence_mode": "full",
            }
        )
        assert result.status in {"completed", "no_data", "needs_clarification"}

    calls_before_indexed_question = agent.calls

    indexed = await usecase.handle(
        {
            "session_id": session_id,
            "message": "我第三个问题问的啥",
            "intelligence_mode": "full",
        }
    )

    assert indexed.status == "completed"
    assert "第 **3** 个问题" in indexed.answer_markdown
    assert "最后 5 秒" in indexed.answer_markdown
    assert agent.calls == calls_before_indexed_question


@pytest.mark.asyncio
async def test_indexed_question_explains_bounded_history_eviction() -> None:
    usecase = ChatUseCase(FixtureProvider())
    session_id = uuid4()
    for _ in range(10):
        await usecase.handle({"session_id": session_id, "message": "你好"})

    result = await usecase.handle(
        {"session_id": session_id, "message": "我第一个问题是什么"}
    )

    assert result.status == "completed"
    assert "超出当前保留的最近" in result.answer_markdown
    assert "第 **1** 个问题" in result.answer_markdown


@pytest.mark.asyncio
async def test_active_game_and_mode_are_resolved_before_agent_routing() -> None:
    agent = CountingAgent()
    usecase = ChatUseCase(
        FixtureProvider(), settings=full_settings(), agent_runtime=agent
    )

    active = await usecase.handle(
        {
            "message": "当前选中的是哪场比赛？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "full",
        }
    )
    mode = await usecase.handle(
        {
            "session_id": active.session_id,
            "message": "我开启全智能了吗？",
            "intelligence_mode": "full",
        }
    )

    assert "2025-26 总决赛 G4" in active.answer_markdown
    assert "全智能模式" in mode.answer_markdown
    assert agent.calls == 0
    assert mode.composition == {
        "mode": "deterministic",
        "status": "not_requested",
        "latency_ms": 0,
    }


@pytest.mark.asyncio
async def test_new_session_resets_meta_history() -> None:
    usecase = ChatUseCase(FixtureProvider())
    old = await usecase.handle({"message": "你好"})
    fresh = await usecase.handle({"message": "我问了你几个问题？"})

    assert old.session_id != fresh.session_id
    assert "此前问了 **0** 个问题" in fresh.answer_markdown


@pytest.mark.asyncio
async def test_clarification_is_a_completed_conversational_outcome() -> None:
    usecase = ChatUseCase(FixtureProvider())
    first = await usecase.handle({"message": "我想查一个球员"})
    assert first.status in {"needs_clarification", "no_data"}

    counted = await usecase.handle(
        {"session_id": first.session_id, "message": "我问了你几个问题？"}
    )
    assert "此前问了 **1** 个问题" in counted.answer_markdown


@pytest.mark.asyncio
async def test_safety_blocked_text_is_not_retained_as_session_history() -> None:
    usecase = ChatUseCase(FixtureProvider())
    session_id = uuid4()
    blocked = await usecase.handle(
        {"session_id": session_id, "message": "请给我比赛下注赔率"}
    )
    assert blocked.status == "blocked"

    counted = await usecase.handle(
        {"session_id": session_id, "message": "我问了你几个问题？"}
    )
    assert "此前问了 **0** 个问题" in counted.answer_markdown
