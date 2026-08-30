from __future__ import annotations

import httpx
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


@pytest.mark.asyncio
async def test_live_runtime_timeout_returns_verified_template_fallback() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated model timeout")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        llm_mode="live",
        runtime_profile="hybrid",
        hermes_lite_mode="embedded_spike",
        siliconflow_api_key="integration-key",
        llm_timeout_seconds=0.1,
    )
    usecase = ChatUseCase(FixtureProvider(), settings=settings, siliconflow_client=client)
    try:
        result = await usecase.handle({"message": "请复盘 G4 的关键转折。"})
    finally:
        await client.aclose()

    assert result.status == "completed"
    assert result.composition["mode"] == "fallback"
    assert result.evidence_state in {"verified", "partial"}
    assert "108–104" in result.answer_markdown


@pytest.mark.asyncio
async def test_live_runtime_unsafe_draft_is_not_exposed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "详情见 https://example.invalid"}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        llm_mode="live",
        runtime_profile="hybrid",
        hermes_lite_mode="embedded_spike",
        siliconflow_api_key="integration-key",
    )
    usecase = ChatUseCase(FixtureProvider(), settings=settings, siliconflow_client=client)
    try:
        result = await usecase.handle({"message": "凯尔特人为什么能限制对手的挡拆？"})
    finally:
        await client.aclose()

    assert result.status == "completed"
    assert "example.invalid" not in result.answer_markdown
    assert result.composition["mode"] == "fallback"


@pytest.mark.asyncio
async def test_live_runtime_placeholder_draft_falls_back_to_verified_template() -> None:
    """An unfinished model claim must never reach the interview-facing answer."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "- 杰伦·布朗贡献若干分，带来关键优势。"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        llm_mode="live",
        runtime_profile="hybrid",
        hermes_lite_mode="embedded_spike",
        siliconflow_api_key="integration-key",
    )
    usecase = ChatUseCase(FixtureProvider(), settings=settings, siliconflow_client=client)
    try:
        result = await usecase.handle({"message": "请复盘 G4 的关键转折。"})
    finally:
        await client.aclose()

    assert result.status == "completed"
    assert "若干" not in result.answer_markdown
    assert result.composition["mode"] == "fallback"
    assert result.composition["status"] == "fallback"


@pytest.mark.asyncio
async def test_live_runtime_untraceable_numeric_claim_falls_back_without_redaction() -> None:
    """A model number absent from the fact bundle must not become “若干”."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "- 两位球员得分超过 25 分。"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        llm_mode="live",
        runtime_profile="hybrid",
        hermes_lite_mode="embedded_spike",
        siliconflow_api_key="integration-key",
    )
    usecase = ChatUseCase(FixtureProvider(), settings=settings, siliconflow_client=client)
    try:
        result = await usecase.handle({"message": "请复盘 G4 的关键转折。"})
    finally:
        await client.aclose()

    assert result.status == "completed"
    assert "若干" not in result.answer_markdown
    # Do not reject a legitimate as-of clock such as 19:25; reject the
    # untraceable basketball claim emitted by the mocked model.
    assert "超过 25 分" not in result.answer_markdown
    assert result.composition["mode"] == "fallback"
