"""Concurrency and session-isolation checks for the flower HTTP boundary.

All requests use an in-process ASGI transport and the deterministic flower
core. No listener, external search service, or model quota is involved. The
tests intentionally use enough simultaneous streams to catch accidental
per-request globals and an overly small SSE admission limit.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest

from apps.api.src.application.flower_chat_use_case import FlowerChatUseCase
from apps.api.src.config import Settings
from apps.api.src.main import create_app


def _settings() -> Settings:
    return Settings(
        agent_domain="flower",
        full_intelligence_enabled=False,
        default_intelligence_mode="hybrid",
        # The test deliberately exceeds the normal per-process stream count;
        # admission should remain bounded but must not reject this batch.
        max_sse_connections=128,
        max_inflight_requests=128,
        queue_max_depth=16,
        allowed_origins=(),
    )


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for frame in text.split("\n\n"):
        lines = frame.splitlines()
        event_line = next(
            (line for line in lines if line.startswith("event: ")),
            None,
        )
        data_line = next(
            (line for line in lines if line.startswith("data: ")),
            None,
        )
        if event_line is None or data_line is None:
            continue
        events.append((event_line[7:], json.loads(data_line[6:])))
    return events


def _assert_ids(payload: dict, session_id: UUID) -> None:
    assert payload["session_id"] == str(session_id)
    UUID(str(payload["request_id"]))


@pytest.mark.asyncio
async def test_32_concurrent_http_requests_keep_sessions_and_answers_isolated() -> None:
    settings = _settings()
    app = create_app(settings=settings, usecase=FlowerChatUseCase(settings=settings))
    plants = ("绣球", "月季", "蝴蝶兰", "栀子花", "茉莉", "长寿花")
    requests = [(uuid4(), plants[index % len(plants)]) for index in range(32)]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/api/v1/chat",
                    json={
                        "session_id": str(session_id),
                        "message": f"{plant}怎么浇水？",
                    },
                )
                for session_id, plant in requests
            )
        )

    assert len(responses) == 32
    for response, (session_id, plant) in zip(responses, requests, strict=True):
        assert response.status_code == 200, response.text
        body = response.json()
        _assert_ids(body, session_id)
        assert body["status"] == "completed"
        assert plant in body["answer_markdown"]
        # A response for one session must not inherit a different plant's
        # context. The local answer is deliberately scoped to one plant.
        other_plants = set(plants) - {plant}
        assert not any(other in body["answer_markdown"] for other in other_plants)


@pytest.mark.asyncio
async def test_100_concurrent_sse_streams_complete_without_cross_session_leaks() -> None:
    settings = _settings()
    usecase = FlowerChatUseCase(settings=settings)
    app = create_app(settings=settings, usecase=usecase)
    plants = ("绣球", "月季", "蝴蝶兰", "栀子花", "茉莉", "长寿花")
    requests = [(uuid4(), plants[index % len(plants)]) for index in range(100)]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/api/v1/chat/stream",
                    json={
                        "session_id": str(session_id),
                        "message": f"{plant}怎么养？",
                    },
                    headers={"accept": "text/event-stream"},
                )
                for session_id, plant in requests
            )
        )

    assert len(responses) == 100
    for response, (session_id, plant) in zip(responses, requests, strict=True):
        assert response.status_code == 200, response.text
        events = _parse_sse(response.text)
        names = [name for name, _payload in events]
        assert names[0] == "run.started"
        assert names[-1] == "message.completed"
        assert "run.error" not in names
        assert names.count("message.completed") == 1
        started = events[0][1]
        completed = events[-1][1]
        _assert_ids(started, session_id)
        _assert_ids(completed, session_id)
        assert completed["status"] == "completed"
        assert plant in completed["answer_markdown"]
        assert "hermes" not in response.text.lower()


@pytest.mark.asyncio
async def test_same_session_concurrent_updates_are_serialised_without_lost_context() -> None:
    settings = _settings()
    usecase = FlowerChatUseCase(settings=settings)
    session_id = uuid4()
    first = await usecase.handle(
        {
            "session_id": session_id,
            "message": "我在上海养绣球，北阳台明亮散射光",
        }
    )
    assert first.status == "completed"

    results = await asyncio.gather(
        *(
            usecase.handle(
                {
                    "session_id": session_id,
                    "message": "那这盆多久浇水？",
                }
            )
            for _ in range(16)
        )
    )
    assert all(result.status == "completed" for result in results)
    assert all("绣球浇水" in result.answer_markdown for result in results)
    stored = await usecase.session_store.load(session_id)
    assert stored is not None
    assert stored.completed_user_turn_count == 17
    assert stored.plant_name == "绣球"
    assert all("多久浇水" in question for question in stored.recent_questions)
    assert len(stored.recent_questions) <= 8


def test_sse_parser_ignores_heartbeats_and_requires_json_events() -> None:
    text = ': heartbeat\n\nevent: run.started\ndata: {"request_id": "x"}\n\n'
    events = _parse_sse(text)
    assert events == [("run.started", {"request_id": "x"})]
