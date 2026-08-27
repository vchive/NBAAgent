"""POST-SSE framing and event contracts.

The browser uses ``fetch`` to consume a POST stream, therefore this module emits ordinary
Server-Sent Events rather than relying on ``EventSource``.  Event payloads are validated
against the same public schemas used by the synchronous route.  No provider/raw fields are
accepted, and a bounded frame size prevents an accidentally verbose model draft from
holding an SSE connection hostage.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from .schemas import (
    ChatResponse,
    ClarificationRequiredPayload,
    MessageDeltaPayload,
    RunErrorPayload,
    RunStartedPayload,
    RunStatusPayload,
    SafetyBlockedPayload,
)

EVENT_RUN_STARTED = "run.started"
EVENT_RUN_STATUS = "run.status"
EVENT_MESSAGE_DELTA = "message.delta"
EVENT_MESSAGE_COMPLETED = "message.completed"
EVENT_CLARIFICATION_REQUIRED = "clarification.required"
EVENT_SAFETY_BLOCKED = "safety.blocked"
EVENT_RUN_ERROR = "run.error"

EVENT_NAMES = frozenset(
    {
        EVENT_RUN_STARTED,
        EVENT_RUN_STATUS,
        EVENT_MESSAGE_DELTA,
        EVENT_MESSAGE_COMPLETED,
        EVENT_CLARIFICATION_REQUIRED,
        EVENT_SAFETY_BLOCKED,
        EVENT_RUN_ERROR,
    }
)


class SSESerializationError(ValueError):
    """Raised when an event cannot be represented safely on the public stream."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    # ``json.dumps(default=str)`` is deliberately not used globally: arbitrary objects could
    # expose reprs containing provider URLs or credentials.  Primitive conversion is enough
    # for our wire schemas; reject unknown values below.
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise SSESerializationError(f"unsupported SSE payload value: {type(value).__name__}")


def _payload_for_event(event: str, payload: Any) -> Any:
    classes: dict[str, type[BaseModel]] = {
        EVENT_RUN_STARTED: RunStartedPayload,
        EVENT_RUN_STATUS: RunStatusPayload,
        EVENT_MESSAGE_DELTA: MessageDeltaPayload,
        EVENT_MESSAGE_COMPLETED: ChatResponse,
        EVENT_CLARIFICATION_REQUIRED: ClarificationRequiredPayload,
        EVENT_SAFETY_BLOCKED: SafetyBlockedPayload,
        EVENT_RUN_ERROR: RunErrorPayload,
    }
    model_type = classes.get(event)
    if model_type is None:
        raise SSESerializationError(f"unknown SSE event: {event}")
    if isinstance(payload, model_type):
        model = payload
    else:
        try:
            model = model_type.model_validate(payload)
        except Exception as exc:
            raise SSESerializationError(f"invalid payload for {event}") from exc
    return model.model_dump(mode="json")


def serialize_event(
    event: str,
    payload: Any,
    *,
    max_bytes: int = 16_384,
) -> str:
    """Serialize one validated event to an SSE frame.

    JSON is compact and UTF-8.  ``json.dumps`` escapes embedded newlines, so a user/model
    string cannot inject a second ``event:`` or ``data:`` line.  The returned frame always
    ends in the blank line required by the SSE protocol.
    """

    if not isinstance(event, str) or event not in EVENT_NAMES or "\n" in event or "\r" in event:
        raise SSESerializationError("invalid SSE event name")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    data = _payload_for_event(event, payload)
    try:
        encoded = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        # Keep serializer failures inside the public SSE error vocabulary.  In
        # particular, ``allow_nan=False`` rejects a model-produced NaN instead
        # of allowing a non-conforming JSON frame to escape.
        raise SSESerializationError("SSE payload is not valid JSON") from exc
    frame = f"event: {event}\ndata: {encoded}\n\n"
    if len(frame.encode("utf-8")) > max_bytes:
        raise SSESerializationError("SSE event exceeds configured size limit")
    return frame


# Names used by a few early clients/tests; keep them as aliases to one implementation.
encode_event = serialize_event
format_event = serialize_event
serialize_sse_event = serialize_event


def heartbeat() -> str:
    """Return a protocol comment heartbeat."""

    return ": heartbeat\n\n"


serialize_heartbeat = heartbeat


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """A typed event that can be encoded lazily."""

    event: str
    payload: Any
    max_bytes: int = 16_384

    # ``name``/``data`` properties accommodate code using the terminology from the SSE RFC.
    @property
    def name(self) -> str:
        return self.event

    @property
    def data(self) -> Any:
        return self.payload

    def encode(self) -> str:
        return serialize_event(self.event, self.payload, max_bytes=self.max_bytes)

    def as_frame(self) -> str:
        return self.encode()


@dataclass(slots=True)
class SSEEventStream:
    """Small state machine enforcing the event order in ``contracts/http-api.md``.

    Heartbeats are out-of-band comments and do not affect the state.  The stream remains
    reusable for tests: ``frames`` yields already validated frames and ``close`` marks it
    terminal.  A route may choose not to use this helper and call ``serialize_event``
    directly when its framework handles ordering.
    """

    max_event_bytes: int = 16_384
    # Optional transport-owned identifiers.  The HTTP route supplies these
    # after preparing its response headers; standalone users can leave them
    # unset and retain the original shape/order-only helper behaviour.
    expected_request_id: UUID | None = None
    expected_session_id: UUID | None = None
    _events: list[SSEEvent] = field(default_factory=list, init=False)
    _started: bool = field(default=False, init=False)
    _awaiting_completion: bool = field(default=False, init=False)
    _saw_delta: bool = field(default=False, init=False)
    _branch_event: str | None = field(default=None, init=False)
    _request_id: UUID | None = field(default=None, init=False)
    _session_id: UUID | None = field(default=None, init=False)
    _terminal_event: str | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    def append(self, event: str, payload: Any) -> SSEEvent:
        if self._closed:
            raise SSESerializationError("SSE stream is already closed")
        if event == EVENT_RUN_STARTED:
            if self._started:
                raise SSESerializationError("run.started must be emitted once")
            if self._events:
                raise SSESerializationError("run.started must be the first event")
        elif not self._started:
            raise SSESerializationError("run.started is required before other events")

        if event == EVENT_RUN_STATUS and self._awaiting_completion:
            raise SSESerializationError("run.status cannot follow a terminal branch")
        if event == EVENT_RUN_STATUS and self._saw_delta:
            raise SSESerializationError("run.status cannot follow message.delta")
        if event == EVENT_MESSAGE_DELTA and self._awaiting_completion:
            raise SSESerializationError("message.delta cannot follow a terminal branch")
        if event in {EVENT_CLARIFICATION_REQUIRED, EVENT_SAFETY_BLOCKED}:
            # A branch marker is an alternative to the delta/completion path;
            # once deltas have started, switching to a branch would make the
            # stream impossible for a client to interpret deterministically.
            if self._saw_delta:
                raise SSESerializationError("terminal branch cannot follow message.delta")
            if self._awaiting_completion:
                raise SSESerializationError("duplicate terminal branch event")
        if event == EVENT_MESSAGE_COMPLETED and self._terminal_event is not None:
            raise SSESerializationError("stream already has a terminal event")
        if event == EVENT_RUN_ERROR and self._awaiting_completion:
            raise SSESerializationError("run.error cannot follow a terminal branch")
        if event == EVENT_RUN_ERROR and self._saw_delta:
            # Once a delta has been sent the client is on the conversational
            # completion branch.  A technical error after that point would
            # leave it with two incompatible terminal interpretations; the
            # producer must emit message.completed (or fail before any delta).
            raise SSESerializationError("run.error cannot follow message.delta")
        # Validate the payload before mutating branch/terminal state.  A malformed event
        # should be retryable in a test/route rather than leaving the stream permanently
        # closed.  Keep the projected values around for the cross-event checks below.
        try:
            projected = _payload_for_event(event, payload)
        except SSESerializationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise SSESerializationError("invalid SSE payload") from exc

        if event == EVENT_RUN_STARTED:
            try:
                started_request_id = UUID(str(projected["request_id"]))
                started_session_id = UUID(str(projected["session_id"]))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise SSESerializationError("invalid run.started identifiers") from exc
            if (
                self.expected_request_id is not None
                and started_request_id != self.expected_request_id
            ) or (
                self.expected_session_id is not None
                and started_session_id != self.expected_session_id
            ):
                raise SSESerializationError("run.started identifiers do not match stream")
        else:
            started_request_id = started_session_id = None

        if event in {EVENT_MESSAGE_COMPLETED, EVENT_RUN_ERROR}:
            # Both terminal envelopes carry the same identifiers as run.started.
            # This prevents a producer/replay bug from joining one stream to a
            # different session on the client side.
            try:
                event_request_id = UUID(str(projected["request_id"]))
                event_session_id = UUID(str(projected["session_id"]))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise SSESerializationError("invalid terminal identifiers") from exc
            if (self._request_id is not None and event_request_id != self._request_id) or (
                self._session_id is not None and event_session_id != self._session_id
            ):
                raise SSESerializationError("terminal identifiers do not match run.started")

        if event == EVENT_MESSAGE_COMPLETED:
            completion_status = str(projected.get("status", ""))
            if self._branch_event == EVENT_CLARIFICATION_REQUIRED:
                if completion_status != "needs_clarification":
                    raise SSESerializationError(
                        "clarification branch requires needs_clarification completion"
                    )
            elif self._branch_event == EVENT_SAFETY_BLOCKED:
                if completion_status != "blocked":
                    raise SSESerializationError("safety branch requires blocked completion")
            elif self._saw_delta:
                if completion_status != "completed":
                    raise SSESerializationError("message.delta branch requires completed status")
            elif completion_status not in {"completed", "no_data"}:
                raise SSESerializationError(
                    "direct completion must use completed or no_data status"
                )

        item = SSEEvent(event, payload, self.max_event_bytes)
        item.encode()

        if event == EVENT_RUN_STARTED:
            self._started = True
            self._request_id = started_request_id
            self._session_id = started_session_id
        if event in {EVENT_CLARIFICATION_REQUIRED, EVENT_SAFETY_BLOCKED}:
            self._awaiting_completion = True
            self._branch_event = event
            # The branch itself is not terminal on the wire: the contract
            # requires a following ``message.completed`` envelope.
        elif event == EVENT_MESSAGE_COMPLETED:
            # Completion may follow deltas/statuses or one of the branch markers.
            if self._terminal_event is not None:
                raise SSESerializationError("stream already completed")
            self._terminal_event = EVENT_MESSAGE_COMPLETED
            self._closed = True
        elif event == EVENT_RUN_ERROR:
            # A technical error is terminal and must not be followed by completion.
            if self._terminal_event is not None:
                raise SSESerializationError("stream already has a terminal event")
            self._terminal_event = EVENT_RUN_ERROR
            self._closed = True

        if event == EVENT_MESSAGE_DELTA:
            self._saw_delta = True

        self._events.append(item)
        return item

    # Stateless convenience methods are useful in route code that does not need ordering
    # enforcement, and make ``SSESerializer.serialize(...)`` a compatible spelling.
    @staticmethod
    def serialize(event: str, payload: Any, *, max_bytes: int = 16_384) -> str:
        return serialize_event(event, payload, max_bytes=max_bytes)

    @staticmethod
    def encode(event: str, payload: Any, *, max_bytes: int = 16_384) -> str:
        return serialize_event(event, payload, max_bytes=max_bytes)

    def heartbeat(self) -> str:
        if self._closed:
            raise SSESerializationError("SSE stream is already closed")
        return heartbeat()

    def frames(self) -> Iterator[str]:
        for event in self._events:
            yield event.encode()

    def events(self) -> tuple[SSEEvent, ...]:
        return tuple(self._events)

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


# Alternate class name used in route prototypes.
SSESerializer = SSEEventStream


class SSEEventSerializer:
    """Stateless serializer facade for callers that only need one frame."""

    serialize = staticmethod(serialize_event)
    encode = staticmethod(serialize_event)
    heartbeat = staticmethod(heartbeat)


def iter_frames(
    events: Iterable[SSEEvent | tuple[str, Any]], *, max_bytes: int = 16_384
) -> Iterator[str]:
    """Encode an iterable of events without exposing raw payload objects."""

    for item in events:
        if isinstance(item, SSEEvent):
            yield item.encode()
        else:
            event, payload = item
            yield serialize_event(event, payload, max_bytes=max_bytes)


__all__ = [
    "EVENT_NAMES",
    "EVENT_RUN_STARTED",
    "EVENT_RUN_STATUS",
    "EVENT_MESSAGE_DELTA",
    "EVENT_MESSAGE_COMPLETED",
    "EVENT_CLARIFICATION_REQUIRED",
    "EVENT_SAFETY_BLOCKED",
    "EVENT_RUN_ERROR",
    "SSEEvent",
    "SSEEventStream",
    "SSEEventSerializer",
    "SSESerializationError",
    "SSESerializer",
    "encode_event",
    "format_event",
    "heartbeat",
    "iter_frames",
    "serialize_event",
    "serialize_heartbeat",
    "serialize_sse_event",
]
