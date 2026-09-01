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


@pytest.mark.asyncio
async def test_plain_arithmetic_is_redirected_before_nba_parser_or_provider() -> None:
    provider = FixtureProvider()
    usecase = ChatUseCase(provider)

    result = await usecase.handle({"message": "1+1等于几"})

    assert result.status == "no_data"
    assert "专注于 NBA" in result.answer_markdown
    assert "请补充查询对象" not in result.answer_markdown
    assert provider.calls == 0
    telemetry = usecase.telemetry.latest()
    assert telemetry is not None
    assert telemetry.provider_call_count == 0
    assert telemetry.cache_read_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "请讨论这场比赛的政治争议",
        "球员场外绯闻是真的吗？",
        "这场比赛是不是假球？",
        "某球员是否涉嫌犯罪？",
        "请给我这场比赛的赌球盘口",
        "请给球员起一个侮辱性绰号",
    ],
)
async def test_every_pdf_red_line_short_circuits_provider_cache_and_runtime(message: str) -> None:
    provider = FixtureProvider()
    usecase = ChatUseCase(provider)

    result = await usecase.handle({"message": message})

    assert result.status == "blocked"
    assert provider.calls == 0
    assert usecase.gateway.counters()["provider_call_count"] == 0
    assert usecase.gateway.counters()["cache_read_count"] == 0
    assert usecase.gateway.counters()["cache_write_count"] == 0
    telemetry = usecase.telemetry.latest()
    assert telemetry is not None
    assert telemetry.provider_call_count == 0
    assert telemetry.cache_read_count == 0
    # A blocked request is short-circuited before runtime selection, so the
    # telemetry field may remain unset; either way no model call is allowed.
    assert telemetry.hermes_status in {None, "not_requested", "blocked", "unavailable"}


@pytest.mark.asyncio
async def test_provider_empty_result_is_truthful_and_retryable_boundary_is_typed() -> None:
    provider = FixtureProvider(scenario="empty")
    usecase = ChatUseCase(provider)

    result = await usecase.handle({"message": "2025-26 总决赛 G4 谁得分最高？"})

    assert result.status in {"no_data", "needs_clarification"}
    assert result.evidence_state in {"none", "partial"}
    assert "32" not in result.answer_markdown
