"""Focused integration checks for flower/Hermes routing semantics.

These tests stay at the application boundary and use in-process doubles.  They
make it explicit that session-state questions are deterministic, ordinary full
mode questions reach the configured runtime with the user's wording intact,
and a failed online search remains visible as an actionable notice even when
the model can still compose an answer.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.api.src.application.flower_chat_use_case import FlowerChatUseCase
from apps.api.src.application.ports import RuntimeStatus
from apps.api.src.config import Settings
from apps.api.src.domain.models import ChatRequest, IntelligenceMode


def _full_settings() -> Settings:
    return Settings(full_intelligence_enabled=True, default_intelligence_mode="full")


@pytest.mark.asyncio
async def test_session_meta_bypasses_search_and_agent_and_commits_once() -> None:
    calls = {"search": 0, "agent": 0}

    class Search:
        async def search_web(self, *_args, **_kwargs):
            calls["search"] += 1
            raise AssertionError("session metadata must not search")

    class Agent:
        async def run(self, *_args, **_kwargs):
            calls["agent"] += 1
            raise AssertionError("session metadata must not call Hermes")

    usecase = FlowerChatUseCase(
        settings=_full_settings(), search_provider=Search(), agent_runtime=Agent()
    )
    session_id = uuid4()

    first = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="我问了你几个问题？",
            intelligence_mode=IntelligenceMode.FULL,
        )
    )
    second = await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message="我刚才问了什么？",
            intelligence_mode=IntelligenceMode.FULL,
        )
    )
    context = await usecase.session_store.load(session_id)

    assert first.status == second.status == "completed"
    assert "此前问了 **0** 个问题" in first.answer_markdown
    assert "第 **1** 个问题" in first.answer_markdown
    assert "我问了你几个问题" in second.answer_markdown
    assert calls == {"search": 0, "agent": 0}
    assert context is not None
    assert context.completed_user_turn_count == 2
    assert len(context.recent_answers) == 2


@pytest.mark.asyncio
async def test_full_agent_receives_original_question_and_bounded_history() -> None:
    captured: list[object] = []

    class Agent:
        async def run(self, turn, **_kwargs):
            captured.append(turn)
            return SimpleNamespace(
                status=RuntimeStatus.OK,
                answer_markdown="根据你的环境，先观察盆土再浇透。",
                observations=[],
                tool_calls=[],
                error_code=None,
                latency_ms=2,
            )

    usecase = FlowerChatUseCase(settings=_full_settings(), agent_runtime=Agent())
    session_id = uuid4()
    question = "我在上海养绣球，北阳台明亮散射光，最近怎么浇水？"
    await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message=question,
            intelligence_mode=IntelligenceMode.FULL,
        )
    )
    follow_up = "那施肥要注意什么？"
    await usecase.handle(
        ChatRequest(
            session_id=session_id,
            message=follow_up,
            intelligence_mode=IntelligenceMode.FULL,
        )
    )

    assert len(captured) == 2
    assert captured[0].sanitized_question == question
    assert captured[1].sanitized_question == follow_up
    assert [item.role for item in captured[1].conversation_history] == [
        "user",
        "assistant",
    ]
    assert question in captured[1].conversation_history[0].content


@pytest.mark.asyncio
async def test_search_quota_notice_survives_a_successful_agent_answer_and_is_deduped() -> None:
    class Agent:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(
                status=RuntimeStatus.OK,
                answer_markdown="先按盆土干湿调整浇水。",
                observations=[],
                tool_calls=[
                    SimpleNamespace(
                        tool_name="flower_search",
                        error_code="SEARCH_QUOTA_EXHAUSTED",
                    ),
                    SimpleNamespace(
                        tool_name="flower_search",
                        error_code="SEARCH_QUOTA_EXHAUSTED",
                    ),
                ],
                error_code=None,
                latency_ms=3,
            )

    usecase = FlowerChatUseCase(settings=_full_settings(), agent_runtime=Agent())
    result = await usecase.handle(
        ChatRequest(
            message="上海九月绣球最近天气怎么样？",
            intelligence_mode=IntelligenceMode.FULL,
        )
    )

    codes = [item["code"] for item in result.notices]
    assert result.composition["status"] == "used"
    assert codes == ["SEARCH_QUOTA_EXHAUSTED"]
    assert "额度" in result.notices[0]["message"]
    assert "智能回答服务暂时不可用" not in result.notices[0]["message"]


@pytest.mark.asyncio
async def test_result_projects_only_coarse_garden_context() -> None:
    usecase = FlowerChatUseCase()
    result = await usecase.handle(
        ChatRequest(message="我在上海市浦东新区花木路88号养绣球，北阳台明亮散射光")
    )

    projection = result.to_dict().get("garden_context")
    assert projection is not None
    assert projection["plant_name"] == "绣球"
    assert projection["location"] == "上海"
    assert projection["light"] == "bright_indirect"
    assert "花木路" not in repr(projection)
    assert "recent_questions" not in projection
    assert "recent_answers" not in projection
