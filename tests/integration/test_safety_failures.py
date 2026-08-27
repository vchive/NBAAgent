from __future__ import annotations

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.providers.fixture_provider import FixtureProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,code,retryable",
    [
        ("rate_limit", "UPSTREAM_RATE_LIMITED", True),
        ("auth", "UPSTREAM_AUTH", False),
        ("invalid_json", "INVALID_UPSTREAM_DATA", False),
    ],
)
async def test_provider_failures_have_safe_typed_errors(
    scenario: str, code: str, retryable: bool
) -> None:
    provider = FixtureProvider(scenario=scenario)
    result = await ChatUseCase(provider).handle({"message": "2025-26 总决赛 G4 谁得分最高？"})
    assert result.status == "failed"
    assert result.error["code"] == code
    assert result.error["retryable"] is retryable
    assert "provider" not in result.error["message"].lower()


@pytest.mark.asyncio
async def test_safety_and_out_of_scope_never_call_provider() -> None:
    provider = FixtureProvider()
    usecase = ChatUseCase(provider)
    blocked = await usecase.handle({"message": "请给我比赛下注赔率"})
    out_scope = await usecase.handle({"message": "今天上海天气如何"})
    assert blocked.status == "blocked" and out_scope.status == "no_data"
    assert provider.calls == 0
