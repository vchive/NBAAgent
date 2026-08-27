from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from apps.api.src.main import create_app


@pytest.mark.asyncio
async def test_versioned_objective_envelope_expectations() -> None:
    """Keep the public envelope assertions in a reviewable, versioned snapshot."""

    fixture = Path(__file__).parents[1] / "fixtures/objective_envelopes.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for case in cases:
            response = await client.post("/api/v1/chat", json={"message": case["prompt"]})
            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == case["status"]
            assert payload["evidence_state"] == case["evidence_state"]
            for token in case["contains"]:
                assert token in payload["answer_markdown"]


@pytest.mark.asyncio
async def test_objective_chat_creates_session_and_returns_traceable_fact() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat", json={"message": "2025-26 总决赛 G4 谁得分最高？"}
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["session_id"]
    assert payload["request_id"]
    assert payload["evidence_state"] == "verified"
    assert "杰伦·布朗" in payload["answer_markdown"]
    assert "32" in payload["answer_markdown"]
    assert "provider" not in response.text.lower()


@pytest.mark.asyncio
async def test_complex_series_pbp_and_correction_paths_are_distinct() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        series = await client.post("/api/v1/chat", json={"message": "这轮系列赛目前大比分是多少？"})
        pbp = await client.post("/api/v1/chat", json={"message": "G4 最后五秒发生了什么？"})
        correction = await client.post(
            "/api/v1/chat", json={"message": "我记得雷霆在 G4 赢了，帮我核验一下。"}
        )
    assert series.status_code == pbp.status_code == correction.status_code == 200
    assert "凯尔特人" in series.json()["answer_markdown"]
    assert "个回合" in pbp.json()["answer_markdown"]
    assert correction.json()["corrections"]
    assert correction.json()["corrections"][0]["status"] == "corrected"
