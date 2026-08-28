from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.src.application.chat_use_case import ChatResult
from apps.api.src.config import Settings
from apps.api.src.main import create_app


@pytest.mark.asyncio
async def test_chat_and_sse_share_public_envelope() -> None:
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
        assert payload["evidence_state"] == "verified"
        assert "source_ref" not in response.text
        stream = await client.post(
            "/api/v1/chat/stream", json={"message": "2025-26 总决赛 G4 谁得分最高？"}
        )
        assert stream.status_code == 200
        assert stream.text.index("event: run.started") < stream.text.index(
            "event: message.completed"
        )
        assert "event: message.delta" in stream.text


@pytest.mark.asyncio
async def test_red_line_short_circuits_provider_and_cache() -> None:
    app = create_app()
    usecase = app.state.chat_use_case
    result = await usecase.handle({"message": "请给我比赛下注赔率"})
    assert result.status == "blocked"
    assert usecase.provider.calls == 0
    assert usecase.gateway.counters()["cache_read_count"] == 0


def test_live_profile_without_key_is_degraded_and_not_ready() -> None:
    app = create_app(
        settings=Settings(
            llm_mode="live",
            runtime_profile="hybrid",
            hermes_lite_mode="embedded_spike",
        )
    )
    with TestClient(app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["dependencies"]["hermes"] == "degraded"
    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"


@pytest.mark.asyncio
async def test_highlights_date_projection_clears_empty_dates() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        populated = await client.get("/api/v1/highlights?date=2026-06-12&timezone=Asia/Shanghai")
        empty = await client.get("/api/v1/highlights?date=2026-06-13&timezone=Asia/Shanghai")
        assert populated.status_code == 200 and populated.json()["games"]
        assert empty.status_code == 200 and empty.json()["games"] == []


@pytest.mark.asyncio
async def test_highlights_returns_safe_service_busy_when_projection_is_unavailable() -> None:
    class ChatOnlyUseCase:
        async def handle(self, body):
            return ChatResult(
                request_id=uuid4(),
                session_id=body.session_id or uuid4(),
                status="no_data",
                answer_markdown="暂无数据。",
                evidence_state="none",
            )

    app = create_app(usecase=ChatOnlyUseCase())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/highlights?date=2026-06-12&timezone=Asia/Shanghai")

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "SERVICE_BUSY"
    assert payload["error"]["retryable"] is True
    assert "gateway" not in response.text


@pytest.mark.asyncio
async def test_historical_finals_question_uses_historical_fixture() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "1999 年总决赛马刺打尼克斯，最后谁夺冠了？"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert "马刺" in payload["answer_markdown"]
    assert "凯尔特人" not in payload["answer_markdown"]


@pytest.mark.asyncio
async def test_franchise_latest_title_includes_verified_season() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat", json={"message": "马刺队史上一次夺冠是哪一年？"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert "1998-99" in payload["answer_markdown"]
    assert "队史最近一次夺冠" in payload["answer_markdown"]


@pytest.mark.asyncio
async def test_future_championship_question_does_not_reuse_history_fixture() -> None:
    app = create_app()
    usecase = app.state.chat_use_case
    before = usecase.provider.calls
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/chat", json={"message": "谁会夺冠？"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "no_data"
    assert "历史冠军" in payload["answer_markdown"]
    assert "凯尔特人" not in payload["answer_markdown"]
    assert usecase.provider.calls == before


@pytest.mark.asyncio
async def test_invalid_calendar_date_is_a_non_retryable_payload_error() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/chat", json={"message": "2026-02-30 比赛有哪些？"})

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "INVALID_PAYLOAD"
    assert payload["error"]["retryable"] is False
    assert "日期" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_reusing_client_message_id_with_different_text_is_rejected() -> None:
    app = create_app()
    session_id = str(uuid4())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/api/v1/chat",
            json={
                "session_id": session_id,
                "client_message_id": "same-key",
                "message": "2025-26 总决赛 G4 谁得分最高？",
            },
        )
        conflict = await client.post(
            "/api/v1/chat",
            json={
                "session_id": session_id,
                "client_message_id": "same-key",
                "message": "2025-26 总决赛 G3 谁得分最高？",
            },
        )

    assert first.status_code == 200
    assert conflict.status_code == 400
    payload = conflict.json()
    assert payload["error"]["code"] == "INVALID_PAYLOAD"
    assert "请求标识" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_pbp_fact_questions_return_traceable_last_event_details() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        shooter = await client.post(
            "/api/v1/chat",
            json={"message": "G4 最后一攻是不是杰伦·布朗自己投进决胜球的？"},
        )
        shot_type = await client.post("/api/v1/chat", json={"message": "G4 最后一球是不是三分？"})
        score = await client.post(
            "/api/v1/chat",
            json={"message": "G4 最后一球是谁投的、事件后比分是多少？"},
        )

    for response in (shooter, shot_type, score):
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert "108–104" in response.json()["answer_markdown"]
    assert "谢伊·吉尔杰斯-亚历山大" in shooter.json()["answer_markdown"]
    assert shooter.json()["corrections"]
    assert "罚球" in shot_type.json()["answer_markdown"]


@pytest.mark.asyncio
async def test_pbp_open_last_shot_reports_missing_terminal_actor_without_guessing() -> None:
    """A terminal score row without shooter/type must stay explicitly unknown."""

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/chat", json={"message": "G4 谁命中了最后一投？"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    answer = payload["answer_markdown"]
    assert "最后一条记录未标注出手者" in answer
    assert "暂无可核验" in answer
    # The score-bearing terminal record is still useful even though its actor
    # and shot type are unavailable; it must not be replaced by the preceding
    # five-second free throw.
    assert "108–104" in answer


@pytest.mark.asyncio
async def test_sync_route_maps_malformed_runtime_output_to_safe_error() -> None:
    class BadUseCase:
        async def handle(self, body):
            return ChatResult(
                request_id=uuid4(),
                session_id=body.session_id or uuid4(),
                status="completed",
                answer_markdown="不应直接返回",
                blocks=[
                    {
                        "type": "fact",
                        "label": "来源",
                        "value": {"source_ref": "https://internal.invalid"},
                    }
                ],
                evidence_state="none",
                latency_ms=1,
            )

    app = create_app(usecase=BadUseCase())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/chat", json={"message": "测试"})

    assert response.status_code == 500
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "OUTPUT_BLOCKED"
    assert "source_ref" not in response.text
