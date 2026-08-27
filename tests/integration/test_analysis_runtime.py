from __future__ import annotations

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.config import Settings
from apps.api.src.providers.fixture_provider import FixtureProvider


@pytest.mark.asyncio
async def test_hybrid_analysis_uses_hermes_seam_but_keeps_verified_facts() -> None:
    usecase = ChatUseCase(
        FixtureProvider(),
        settings=Settings(runtime_profile="hybrid", hermes_lite_mode="embedded_spike"),
    )
    result = await usecase.handle({"message": "凯尔特人为什么能限制对手的挡拆？"})
    assert result.status == "completed"
    assert result.evidence_state in {"verified", "partial"}
    assert "108–104" in result.answer_markdown
    assert usecase.hermes_runtime.capability_self_test() is True


@pytest.mark.asyncio
async def test_runtime_timeout_falls_back_to_safe_technical_error() -> None:
    usecase = ChatUseCase(FixtureProvider(scenario="timeout"), settings=Settings())
    result = await usecase.handle({"message": "2025-26 总决赛 G4 谁得分最高？"})
    assert result.status == "failed"
    assert result.error["code"] == "UPSTREAM_TIMEOUT"
    assert "fixture" not in result.answer_markdown.lower()
