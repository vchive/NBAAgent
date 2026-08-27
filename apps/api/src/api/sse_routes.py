"""POST SSE chat route using the same ChatUseCase as sync HTTP."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from apps.api.src.api.schemas import ChatRequest
from apps.api.src.api.sse import (
    EVENT_MESSAGE_COMPLETED,
    EVENT_MESSAGE_DELTA,
    EVENT_RUN_ERROR,
    EVENT_RUN_STARTED,
    SSEEventStream,
    serialize_event,
)
from apps.api.src.application.chat_use_case import ChatUseCase

router = APIRouter()


class SSEConnectionLimiter:
    """Small non-blocking limiter scoped to one FastAPI application.

    asyncio.Semaphore has no public acquire_nowait operation. A lock-protected
    counter keeps admission deterministic: an SSE request is either admitted
    immediately or receives the normal SERVICE_BUSY envelope; it never
    occupies an HTTP worker while waiting for another stream.
    """

    def __init__(self, maximum: int) -> None:
        if maximum <= 0:
            raise ValueError("maximum SSE connections must be positive")
        self.maximum = maximum
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        return self._active

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self._active >= self.maximum:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active <= 0:
                raise RuntimeError("SSE connection limiter released without acquisition")
            self._active -= 1


class QueueSink:
    def __init__(
        self,
        queue: asyncio.Queue[tuple[str, Mapping[str, Any]]],
        max_bytes: int = 16_384,
    ) -> None:
        self.queue = queue
        self.max_bytes = max_bytes

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        # Validate and size the event before it occupies a queue slot. The
        # consumer validates ordering as well, but ingress validation prevents
        # an unbounded mapping from parking in a bounded-by-count queue.
        serialize_event(event_name, payload, max_bytes=self.max_bytes)
        await self.queue.put((event_name, payload))


def _payload_uuid(payload: Any, key: str, fallback: UUID) -> UUID:
    """Read a UUID from an internal event without trusting arbitrary values."""

    if isinstance(payload, Mapping):
        value = payload.get(key)
        try:
            if value is not None:
                return UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            pass
    return fallback


def _safe_error_frame(
    request_id: UUID,
    session_id: UUID,
    *,
    max_bytes: int,
) -> str | None:
    """Build a generic terminal frame for an invalid internal event.

    Internal serializer failures must never expose exception text, provider
    metadata, or a stack trace.  Returning ``None`` is reserved for the
    pathological case where the configured frame limit is too small even for
    the mandatory UUID/error envelope.
    """

    payload = {
        "request_id": request_id,
        "session_id": session_id,
        "status": "failed",
        "error": {
            "code": "SERVICE_BUSY",
            "retryable": True,
            "message": "流式响应暂时不可用，请稍后重试。",
        },
    }
    try:
        return serialize_event("run.error", payload, max_bytes=max_bytes)
    except Exception:
        return None


@router.post("/api/v1/chat/stream")
async def chat_stream(request: Request, body: ChatRequest):
    usecase: ChatUseCase = request.app.state.chat_use_case
    settings = request.app.state.settings
    # Allocate the session once at the transport boundary.  This keeps the
    # header and every event aligned even when the first producer event is
    # delayed (or a custom use case fails before emitting ``run.started``).
    if body.session_id is None:
        body = body.model_copy(update={"session_id": uuid4()})
    request_id = uuid4()
    limiter: SSEConnectionLimiter = request.app.state.sse_connection_limiter
    if not await limiter.try_acquire():
        return JSONResponse(
            status_code=503,
            content={
                "request_id": str(request_id),
                "session_id": str(body.session_id),
                "status": "failed",
                "error": {
                    "code": "SERVICE_BUSY",
                    "retryable": True,
                    "message": "流式连接已满，请稍后重试。",
                },
            },
            headers={"Retry-After": "1", "X-Request-Id": str(request_id)},
        )

    queue_depth = int(getattr(settings, "queue_max_depth", 64))
    max_event_bytes = int(getattr(settings, "max_event_bytes", 16_384))
    max_response_bytes = int(getattr(settings, "max_response_bytes", 262_144))
    queue: asyncio.Queue[tuple[str, Mapping[str, Any]] | None] = asyncio.Queue(maxsize=queue_depth)
    sink = QueueSink(queue, max_event_bytes)
    task = asyncio.create_task(usecase.handle(body, event_sink=sink, request_id=request_id))

    # ``StreamingResponse`` sends headers before the async generator's first
    # body.  Pull the first event before constructing it so an idempotent
    # replay can advertise the original request id (rather than the newly
    # generated retry id) in ``X-Request-Id``.  Normal use-case paths emit
    # ``run.started`` immediately; the bounded timeout keeps a malformed/custom
    # runtime from holding the HTTP request forever.
    # Keep an explicit sentinel so an invalid first queue item is not silently
    # discarded.  It will be handled by the same fail-closed path as any later
    # malformed event.
    _missing = object()
    prefetched: Any = _missing
    try:
        first = await asyncio.wait_for(queue.get(), timeout=0.25)
        prefetched = first
    except TimeoutError:
        pass

    header_request_id = request_id
    header_session_id = body.session_id
    if (
        isinstance(prefetched, tuple)
        and len(prefetched) == 2
        and isinstance(prefetched[0], str)
        and prefetched[0] == EVENT_RUN_STARTED
        and isinstance(prefetched[1], Mapping)
    ):
        header_request_id = _payload_uuid(prefetched[1], "request_id", request_id)
        header_session_id = _payload_uuid(prefetched[1], "session_id", header_session_id)

    async def generate():
        nonlocal prefetched
        terminal_emitted = False
        disconnected = False
        response_bytes = 0
        # Keep only a bounded marker that a conversational delta was sent.
        # If an untrusted producer fails after that point, the SSE contract
        # forbids switching to ``run.error``; we finish with a safe
        # ``message.completed`` envelope instead.
        saw_delta = False
        pending = prefetched
        prefetched = _missing
        # Bind event validation to the IDs advertised in the response headers.
        # This protects the delayed-first-frame path: a producer that emits a
        # ``run.started`` for another request/session is rejected and replaced
        # with a safe, header-consistent error stream.
        state = SSEEventStream(
            max_event_bytes=max_event_bytes,
            expected_request_id=header_request_id,
            expected_session_id=header_session_id,
        )
        terminal_frame = _safe_error_frame(
            header_request_id,
            header_session_id,
            max_bytes=max_event_bytes,
        )
        terminal_reserve = len(terminal_frame.encode("utf-8")) if terminal_frame is not None else 0

        def take_frame(frame: str, *, terminal: bool) -> str | None:
            """Charge one frame against the cumulative response budget.

            Non-terminal events preserve enough space for a contract-shaped
            run.error. This makes cumulative overflow fail closed rather than
            ending the stream after an arbitrary progress/delta frame.
            """

            nonlocal response_bytes
            size = len(frame.encode("utf-8"))
            reserve = 0 if terminal else terminal_reserve
            if response_bytes + size + reserve > max_response_bytes:
                return None
            response_bytes += size
            return frame

        async def fail_closed(payload: Any = None):
            """Yield a contract-shaped terminal error, including a start event.

            A broken/custom producer can fail before emitting ``run.started``.
            The public contract still requires that event to be first, so emit
            a synthetic one using server-owned identifiers before the generic
            ``run.error`` envelope.  The helper intentionally never includes
            the original exception or payload text.
            """

            nonlocal terminal_emitted
            if not state._started:  # noqa: SLF001 - route owns this state machine
                try:
                    started_item = state.append(
                        EVENT_RUN_STARTED,
                        {"request_id": header_request_id, "session_id": header_session_id},
                    )
                    bounded = take_frame(started_item.encode(), terminal=False)
                    if bounded is None:
                        return
                    yield bounded
                except Exception:
                    # If even the mandatory start envelope cannot fit the
                    # configured limit, there is no valid frame to send.
                    return
            # Once a delta has reached the client, ``run.error`` is no longer
            # legal (the client is already on the conversational branch). Use
            # a minimal, schema-valid completion so consumers always receive a
            # terminal event without exposing the producer exception/payload.
            if saw_delta:
                completion_payload = {
                    "request_id": header_request_id,
                    "session_id": header_session_id,
                    "status": "completed",
                    "answer_markdown": "回答连接中断，请重试。",
                    "blocks": [
                        {
                            "type": "text",
                            "content": "回答连接中断，请重试。",
                        }
                    ],
                    "as_of_beijing": None,
                    "evidence_state": "none",
                    "corrections": [],
                    "follow_up": "请重试刚才的问题。",
                    "latency_ms": 0,
                }
                try:
                    frame = state.append(
                        EVENT_MESSAGE_COMPLETED,
                        completion_payload,
                    ).encode()
                except Exception:
                    return
            else:
                # Use the server/header identifiers for every fallback.  In
                # particular, a producer that sends a terminal envelope for a
                # different stream must not be allowed to rewrite the IDs in
                # the replacement error frame.  Append through the state
                # machine rather than serialising around it, so ordering and
                # terminal semantics remain enforced.
                error_payload = {
                    "request_id": header_request_id,
                    "session_id": header_session_id,
                    "status": "failed",
                    "error": {
                        "code": "SERVICE_BUSY",
                        "retryable": True,
                        "message": "流式响应暂时不可用，请稍后重试。",
                    },
                }
                try:
                    frame = state.append(EVENT_RUN_ERROR, error_payload).encode()
                except Exception:
                    return
            bounded = take_frame(frame, terminal=True)
            if bounded is not None:
                terminal_emitted = True
                yield bounded

        try:
            while True:
                if pending is not _missing:
                    item = pending
                    pending = _missing
                else:
                    if await request.is_disconnected():
                        disconnected = True
                        task.cancel()
                        break
                    if task.done() and queue.empty():
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=0.5)
                    except TimeoutError:
                        if task.done() and queue.empty():
                            break
                        heartbeat = take_frame(": heartbeat\n\n", terminal=False)
                        if heartbeat is None:
                            task.cancel()
                            async for safe_frame in fail_closed():
                                yield safe_frame
                            break
                        yield heartbeat
                        continue

                if item is None:
                    break
                event_payload: Any = None
                try:
                    if (
                        not isinstance(item, tuple)
                        or len(item) != 2
                        or not isinstance(item[0], str)
                        or not isinstance(item[1], Mapping)
                    ):
                        raise ValueError("invalid internal SSE event")
                    event_name, event_payload = item
                    # Validate both payload shape and strict ordering.  The
                    # returned typed event is encoded once for the wire.
                    event = state.append(event_name, event_payload)
                    frame = event.encode()
                except Exception:
                    # A malformed internal event must still terminate the
                    # public stream with a safe, schema-valid error envelope.
                    task.cancel()
                    async for frame in fail_closed(event_payload):
                        yield frame
                    break

                is_terminal = event_name in {
                    EVENT_MESSAGE_COMPLETED,
                    EVENT_RUN_ERROR,
                }
                bounded = take_frame(frame, terminal=is_terminal)
                if bounded is None:
                    task.cancel()
                    async for safe_frame in fail_closed(event_payload):
                        yield safe_frame
                    break
                if is_terminal:
                    terminal_emitted = True
                if event_name == EVENT_MESSAGE_DELTA:
                    saw_delta = True
                yield bounded

                # A terminal event is sufficient for the client.  Stop
                # consuming any accidental post-terminal events and ensure the
                # producer cannot remain orphaned.
                if terminal_emitted:
                    if not task.done():
                        task.cancel()
                    break

            if not disconnected:
                try:
                    if not task.done():
                        await task
                    else:
                        # Retrieve an exception from a custom use-case task so
                        # it is not reported as an unhandled background error.
                        task.result()
                except asyncio.CancelledError:
                    if not disconnected:
                        if not terminal_emitted:
                            async for frame in fail_closed():
                                yield frame
                except Exception:
                    if not terminal_emitted:
                        async for frame in fail_closed():
                            yield frame

                # A producer that returned without a terminal event violates
                # the stream contract; fail closed with a generic terminal
                # error rather than leaving the browser hanging.
                if not terminal_emitted:
                    async for frame in fail_closed():
                        yield frame
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                # The public stream has already been closed (or a generic
                # terminal frame was emitted); never leak producer exceptions.
                pass
            await limiter.release()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-Id": str(header_request_id),
        },
    )


__all__ = ["QueueSink", "SSEConnectionLimiter", "router"]
