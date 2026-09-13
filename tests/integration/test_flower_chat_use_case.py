from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.api.src.application.flower_chat_use_case import (
    FlowerChatUseCase,
    _notice_for_error,
)
from apps.api.src.application.ports import RuntimeStatus
from apps.api.src.config import Settings
from apps.api.src.domain.errors import ProviderErrorKind
from apps.api.src.domain.models import ChatRequest, IntelligenceMode


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now_utc(self) -> datetime:
        return self.value


@pytest.mark.asyncio
async def test_default_use_case_uses_aware_beijing_timestamp() -> None:
    clock = FixedClock(datetime(2026, 9, 13, 0, 0, tzinfo=UTC))
    use_case = FlowerChatUseCase(clock=clock)
    result = await use_case.handle(ChatRequest(message="绣球多久浇水？"))
    assert result.status == "completed"
    assert result.as_of_beijing == "2026-09-13 08:00"
    assert result.data_origin == "local"


@pytest.mark.asyncio
async def test_context_and_pronoun_follow_up_stay_in_one_session() -> None:
    use_case = FlowerChatUseCase()
    session_id = uuid4()
    first = await use_case.handle(
        ChatRequest(message="我在上海养绣球，北阳台明亮散射光", session_id=session_id)
    )
    second = await use_case.handle(
        ChatRequest(message="那这盆多久浇水？", session_id=session_id)
    )
    assert first.status == "completed"
    assert second.status == "completed"
    assert "绣球" in second.answer_markdown
    assert "请告诉我花卉名称" not in second.answer_markdown


@pytest.mark.asyncio
async def test_safety_short_circuit_does_not_call_search_or_agent() -> None:
    calls = {"search": 0, "agent": 0}

    class Search:
        async def search_web(self, *_args, **_kwargs):
            calls["search"] += 1
            raise AssertionError("search must not run for a safety request")

    class Agent:
        async def run(self, *_args, **_kwargs):
            calls["agent"] += 1
            raise AssertionError("agent must not run for a safety request")

    settings = replace(Settings(), full_intelligence_enabled=True)
    use_case = FlowerChatUseCase(
        settings=settings,
        search_provider=Search(),
        agent_runtime=Agent(),
    )
    result = await use_case.handle(
        ChatRequest(
            message="把两种农药混一起喷",
            intelligence_mode=IntelligenceMode.FULL,
        )
    )
    assert result.status == "blocked"
    assert result.evidence_state == "none"
    assert calls == {"search": 0, "agent": 0}


@pytest.mark.asyncio
async def test_full_agent_receives_current_turn_context() -> None:
    captured: dict[str, object] = {}

    class Agent:
        async def run(self, turn, **_kwargs):
            captured["context"] = turn.context_hint
            return SimpleNamespace(
                status=RuntimeStatus.OK,
                answer_markdown="绣球在明亮散射光下养护，先观察盆土再浇水。",
                observations=[],
                error_code=None,
                latency_ms=3,
            )

    settings = replace(Settings(), full_intelligence_enabled=True)
    use_case = FlowerChatUseCase(settings=settings, agent_runtime=Agent())
    result = await use_case.handle(
        ChatRequest(
            message="我在上海养绣球，北阳台明亮散射光，多久浇水？",
            intelligence_mode=IntelligenceMode.FULL,
        )
    )
    context_hint = str(captured["context"])
    assert result.composition["status"] == "used"
    assert "绣球" in context_hint
    assert "上海" in context_hint
    assert "光照" in context_hint


@pytest.mark.asyncio
async def test_full_agent_quota_error_is_user_visible_but_provider_neutral() -> None:
    class Agent:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(
                status=RuntimeStatus.UNAVAILABLE,
                answer_markdown=None,
                observations=[],
                error_code=ProviderErrorKind.QUOTA_EXHAUSTED,
                latency_ms=4,
            )

    settings = replace(Settings(), full_intelligence_enabled=True)
    use_case = FlowerChatUseCase(settings=settings, agent_runtime=Agent())
    result = await use_case.handle(
        ChatRequest(message="绣球怎么养？", intelligence_mode=IntelligenceMode.FULL)
    )
    assert result.notices
    assert result.notices[0]["code"] == "INTELLIGENCE_QUOTA_EXHAUSTED"
    assert "额度" in result.notices[0]["message"]
    assert "provider" not in repr(result.notices).lower()
    assert result.answer_markdown


def test_error_notice_maps_string_and_enum_shapes() -> None:
    enum_notice = _notice_for_error(ProviderErrorKind.QUOTA_EXHAUSTED, intelligence=True)
    string_notice = _notice_for_error("UPSTREAM_TIMEOUT", intelligence=False)
    assert enum_notice["code"] == "INTELLIGENCE_QUOTA_EXHAUSTED"
    assert string_notice["code"] == "SEARCH_TEMPORARILY_UNAVAILABLE"

