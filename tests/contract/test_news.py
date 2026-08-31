"""Public contract checks for news retrieval and rendering."""

from __future__ import annotations

import httpx
import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.config import Settings
from apps.api.src.main import create_app
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway


@pytest.mark.asyncio
async def test_chat_news_returns_title_summary_without_provider_metadata() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/chat", json={"message": "凯尔特人新闻"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["evidence_state"] in {"verified", "partial"}
    assert "总决赛" in payload["answer_markdown"]
    assert "轮换" in payload["answer_markdown"]
    # Evidence/provider fields are internal-only and must not cross the HTTP
    # boundary, including nested answer blocks.
    for forbidden in ("evidence_id", "source_ref", "canonical_id", "fixture:news"):
        assert forbidden not in response.text


@pytest.mark.asyncio
async def test_broad_news_query_is_valid_without_an_entity_clarification() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/chat", json={"message": "NBA新闻"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["answer_markdown"]
    assert "暂无匹配" not in payload["answer_markdown"]


@pytest.mark.asyncio
async def test_hybrid_news_empty_live_archive_uses_bounded_snapshot() -> None:
    """A missing live news archive should not regress to a stats message."""

    primary = FixtureProvider(scenario="empty")
    fallback = FixtureProvider()
    gateway = ProviderGateway(primary, fallback=fallback, max_retries=0)
    usecase = ChatUseCase(
        primary,
        gateway=gateway,
        settings=Settings(public_data_mode="hybrid"),
    )
    app = create_app(settings=Settings(public_data_mode="hybrid"), usecase=usecase)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/chat", json={"message": "凯尔特人最近有什么新闻？"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["evidence_state"] == "partial"
    assert "总决赛" in payload["answer_markdown"]
