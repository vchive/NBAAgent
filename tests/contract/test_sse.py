"""SSE and idempotency boundary tests."""

import asyncio
from asyncio import CancelledError
from uuid import uuid4

import httpx
import pytest

from apps.api.src.api.schemas import ChatRequest
from apps.api.src.api.sse import SSEEventStream, SSESerializationError, serialize_event
from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.main import create_app
from apps.api.src.providers.fixture_provider import FixtureProvider


class _Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, name: str, payload: dict) -> None:
        self.events.append((name, payload))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_branch"),
    [
        ("总决赛 G4 谁得分最高？", None),
        ("请给我比赛下注赔率", "safety.blocked"),
        ("帮我查一下", "clarification.required"),
    ],
)
async def test_duplicate_request_replays_complete_sse_sequence(
    message: str, expected_branch: str | None
) -> None:
    usecase = ChatUseCase(FixtureProvider())
    session_id = uuid4()
    client_message_id = f"id-{uuid4()}"
    request = ChatRequest(
        session_id=session_id, message=message, client_message_id=client_message_id
    )

    first = await usecase.handle(request)
    sink = _Sink()
    replay = await usecase.handle(request, event_sink=sink)

    assert replay.request_id == first.request_id
    assert sink.events[0][0] == "run.started"
    assert sink.events[-1][0] == "message.completed"
    if expected_branch is None:
        assert [name for name, _payload in sink.events] == [
            "run.started",
            "message.completed",
        ]
    else:
        assert expected_branch in [name for name, _payload in sink.events]


@pytest.mark.asyncio
async def test_same_idempotency_key_is_isolated_by_session() -> None:
    provider = FixtureProvider()
    usecase = ChatUseCase(provider)
    first = await usecase.handle(
        ChatRequest(session_id=uuid4(), message="总决赛 G4 谁得分最高？", client_message_id="same")
    )
    second = await usecase.handle(
        ChatRequest(session_id=uuid4(), message="总决赛 G3 谁得分最高？", client_message_id="same")
    )

    assert first.request_id != second.request_id
    assert provider.calls == 2


class _SlowProvider(FixtureProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def get_game_summary(self, game_id: str, budget):
        self.started.set()
        try:
            await asyncio.sleep(60)
        except CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("slow provider unexpectedly completed")


@pytest.mark.asyncio
async def test_owner_task_cancellation_cancels_downstream_provider() -> None:
    provider = _SlowProvider()
    usecase = ChatUseCase(provider)
    session_id = uuid4()
    request = ChatRequest(
        session_id=session_id,
        message="总决赛 G4 谁得分最高？",
        client_message_id="cancel-me",
    )
    task = asyncio.create_task(usecase.handle(request))
    await asyncio.wait_for(provider.started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.cancelled.is_set()
    assert await usecase.session_store.replay_or_wait(session_id, "cancel-me", timeout=0.01) is None


def test_sse_state_machine_requires_start_and_terminal_completion() -> None:
    stream = SSEEventStream()
    with pytest.raises(ValueError):
        stream.append("message.completed", {})


def test_sse_state_machine_can_bind_transport_identifiers() -> None:
    request_id, session_id = uuid4(), uuid4()
    stream = SSEEventStream(
        expected_request_id=request_id,
        expected_session_id=session_id,
    )
    with pytest.raises(SSESerializationError):
        stream.append(
            "run.started",
            {"request_id": uuid4(), "session_id": session_id},
        )
    stream.append(
        "run.started",
        {"request_id": request_id, "session_id": session_id},
    )
    stream.append(
        "message.completed",
        {
            "request_id": request_id,
            "session_id": session_id,
            "status": "no_data",
            "answer_markdown": "暂无数据",
            "evidence_state": "none",
            "latency_ms": 1,
        },
    )


def test_sse_state_machine_rejects_events_after_delta_or_terminal() -> None:
    request_id, session_id = uuid4(), uuid4()
    stream = SSEEventStream()
    stream.append("run.started", {"request_id": request_id, "session_id": session_id})
    stream.append("message.delta", {"text": "已核验"})
    with pytest.raises(SSESerializationError):
        stream.append("run.status", {"stage": "late", "text": "不应出现"})
    stream.append(
        "message.completed",
        {
            "request_id": request_id,
            "session_id": session_id,
            "status": "completed",
            "answer_markdown": "已核验",
            "evidence_state": "none",
            "latency_ms": 1,
        },
    )
    with pytest.raises(SSESerializationError):
        stream.append("run.error", {})


def test_sse_state_machine_rejects_run_error_after_delta() -> None:
    request_id, session_id = uuid4(), uuid4()
    stream = SSEEventStream()
    stream.append("run.started", {"request_id": request_id, "session_id": session_id})
    stream.append("message.delta", {"text": "已核验"})
    with pytest.raises(SSESerializationError):
        stream.append(
            "run.error",
            {
                "request_id": request_id,
                "session_id": session_id,
                "status": "failed",
                "error": {
                    "code": "SERVICE_BUSY",
                    "retryable": True,
                    "message": "请稍后重试",
                },
            },
        )


def test_sse_public_text_rejects_provider_metadata() -> None:
    with pytest.raises(SSESerializationError):
        serialize_event("message.delta", {"text": "见 https://internal.invalid"})
    with pytest.raises(SSESerializationError):
        serialize_event("run.status", {"stage": "retrieving", "text": "已得到 108 分"})


@pytest.mark.parametrize("event", ["clarification.required", "safety.blocked"])
def test_sse_branch_payload_rejects_control_characters(event: str) -> None:
    key = "question" if event == "clarification.required" else "message"
    with pytest.raises(SSESerializationError):
        serialize_event(event, {key: "bad\x01text"})


@pytest.mark.asyncio
async def test_sse_serializer_failure_emits_safe_terminal_error() -> None:
    class BadUseCase:
        async def handle(self, body, *, event_sink, request_id):
            await event_sink.emit(
                "run.started",
                {"request_id": request_id, "session_id": body.session_id},
            )
            # Deliberately violate the completion schema.  The route must not
            # leave the client with a stream that ends without a terminal event.
            await event_sink.emit("message.completed", {"invalid": True})

    app = create_app(usecase=BadUseCase())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat/stream",
            json={
                "session_id": str(uuid4()),
                "message": "测试",
            },
        )
    assert response.status_code == 200
    assert "event: run.error" in response.text
    assert "message.completed" not in response.text


@pytest.mark.asyncio
async def test_sse_malformed_event_after_delta_finishes_conversational_branch() -> None:
    class BadAfterDelta:
        async def handle(self, body, *, event_sink, request_id):
            await event_sink.emit(
                "run.started",
                {"request_id": request_id, "session_id": body.session_id},
            )
            await event_sink.emit("message.delta", {"text": "部分回答"})
            # A status after a delta violates the producer contract.  The
            # transport must not append run.error after the conversational
            # branch has started.
            await event_sink.emit("run.status", {"stage": "late", "text": "不应出现"})

    app = create_app(usecase=BadAfterDelta())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/chat/stream",
            json={"session_id": str(uuid4()), "message": "测试"},
        )

    assert response.status_code == 200
    assert "event: message.delta" in response.text
    assert "event: message.completed" in response.text
    assert "event: run.error" not in response.text
