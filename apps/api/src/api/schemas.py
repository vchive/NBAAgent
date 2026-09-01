"""Public HTTP wire schemas.

The domain models use upper-case canonical enums and retain internal evidence fields.  This
module is the deliberately smaller wire projection used by FastAPI: enum values are
lower-case where specified by the HTTP contract, UUIDs/timestamps are serialisable, and no
provider metadata can accidentally be returned to a browser.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.models import (
    AnswerBlock as DomainAnswerBlock,
)
from ..domain.models import (
    ChatRequest as DomainChatRequest,
)
from ..domain.models import (
    DraftAnswer,
)
from ..domain.models import (
    PublicCorrection as DomainPublicCorrection,
)

type JsonScalar = str | int | float | bool | None


def _has_control_chars(value: str, *, allow_linebreaks: bool = True) -> bool:
    """Return whether text contains an unsafe ASCII control character.

    Newline and tab are useful in markdown/prose and remain allowed there;
    DEL (0x7f) is always rejected because it is a control character despite
    falling outside the ``ord < 32`` range.
    """

    for char in value:
        code = ord(char)
        if code == 0x7F or (code < 32 and (not allow_linebreaks or char not in "\n\t")):
            return True
    return False


_PUBLIC_TEXT_LEAK_RE = re.compile(
    r"(?:https?|ftp|file)://|www\."
    r"|\b(?:espn|fixture(?:\.v\d+)?|provider|prompt|traceback)\b"
    r"|(?:source_ref|evidence_ids?|canonical_id|provider(?:[_ -]?(?:call|cache|"
    r"result|response|payload|url|endpoint|name|id))|raw_(?:json|response|payload)|"
    r"trace_id|stack_trace|session_id|request_id|api[_ -]?key|bearer(?:[_ -]?token)?|"
    r"system_prompt|developer_message|tool_call)",
    re.IGNORECASE,
)


def _validate_public_text(value: str, *, allow_linebreaks: bool = True) -> str:
    """Reject public prose that exposes URLs or internal/provider vocabulary."""

    if _has_control_chars(value, allow_linebreaks=allow_linebreaks):
        raise ValueError("control characters are not allowed")
    if _PUBLIC_TEXT_LEAK_RE.search(value):
        raise ValueError("public text contains internal or provider metadata")
    return value


def _validate_json_scalar(value: JsonScalar, *, allow_linebreaks: bool = False) -> JsonScalar:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON scalar must be finite")
    if isinstance(value, str):
        _validate_public_text(value, allow_linebreaks=allow_linebreaks)
    return value


class _WireBase(BaseModel):
    """Base config for public responses.

    ``extra='ignore'`` is used only for response blocks: additive internal/provider fields
    must be ignored by rendering rather than reflected back to clients.  Request and error
    envelopes override this with ``extra='forbid'`` below.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        str_strip_whitespace=False,
        use_enum_values=True,
    )


class _RequestBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=False,
        use_enum_values=True,
    )


class ChatStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"
    NO_DATA = "no_data"
    FAILED = "failed"


class EvidenceState(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    NONE = "none"


class AnswerBlockType(StrEnum):
    TEXT = "text"
    ANALYSIS = "analysis"
    WARNING = "warning"
    TABLE = "table"
    FACT = "fact"


class CorrectionStatus(StrEnum):
    CORRECTED = "corrected"
    UNVERIFIED = "unverified"


class CompositionMode(StrEnum):
    """Provider-neutral answer source shown to the demo UI.

    ``model`` means a constrained generative pass was accepted and appended
    as analysis.  ``fallback`` means the model path was selected but the
    deterministic, evidence-first answer was returned instead.  Objective
    facts use ``deterministic`` by design.
    """

    DETERMINISTIC = "deterministic"
    MODEL = "model"
    AGENT = "agent"
    FALLBACK = "fallback"


class CompositionStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    USED = "used"
    FALLBACK = "fallback"
    DISABLED = "disabled"


class CompositionInfo(_WireBase):
    """Safe, minimal generation provenance for a conversational answer.

    It intentionally contains no model/provider name, endpoint, error text,
    prompt, key, or internal request identifiers.  This lets the client make
    a model fallback visible without widening the public trust boundary.
    """

    mode: CompositionMode = CompositionMode.DETERMINISTIC
    status: CompositionStatus = CompositionStatus.NOT_REQUESTED
    latency_ms: int = Field(default=0, ge=0)


class TechnicalErrorCode(StrEnum):
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    SERVICE_BUSY = "SERVICE_BUSY"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
    UPSTREAM_AUTH = "UPSTREAM_AUTH"
    INVALID_UPSTREAM_DATA = "INVALID_UPSTREAM_DATA"
    COMPOSER_UNAVAILABLE = "COMPOSER_UNAVAILABLE"
    OUTPUT_BLOCKED = "OUTPUT_BLOCKED"


def _enum_value(value: Any, enum_type: type[StrEnum], *, lower: bool = False) -> Any:
    """Coerce canonical domain enums and wire strings to a wire enum value."""

    if isinstance(value, enum_type):
        return value
    raw = getattr(value, "value", value)
    if isinstance(raw, str):
        candidate = raw.lower() if lower else raw.upper()
        try:
            return enum_type(candidate)
        except ValueError:
            # Pydantic will produce the normal, useful enum validation error.
            return value
    return value


def _wire_status(value: Any) -> ChatStatus:
    if isinstance(value, ChatStatus):
        return value
    raw = getattr(value, "value", value)
    if isinstance(raw, str):
        mapping = {
            "COMPLETED": ChatStatus.COMPLETED,
            "NO_DATA": ChatStatus.NO_DATA,
            "NEEDS_CLARIFICATION": ChatStatus.NEEDS_CLARIFICATION,
            "BLOCKED": ChatStatus.BLOCKED,
            "FAILED": ChatStatus.FAILED,
            "completed": ChatStatus.COMPLETED,
            "no_data": ChatStatus.NO_DATA,
            "needs_clarification": ChatStatus.NEEDS_CLARIFICATION,
            "blocked": ChatStatus.BLOCKED,
            "failed": ChatStatus.FAILED,
        }
        if raw in mapping:
            return mapping[raw]
    return value


class ChatRequest(DomainChatRequest):
    """Validated request body for both synchronous and POST-SSE chat routes."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=False,
        use_enum_values=False,
    )


class AnswerBlock(_WireBase):
    """Public, provider-free answer block.

    Unknown fields are ignored by design.  The API never serialises the ignored values,
    which prevents an upstream ``source_ref``/URL field from being smuggled into a response.
    """

    type: AnswerBlockType
    content: str | None = None
    label: str | None = None
    value: JsonScalar = None
    unit: str | None = None
    columns: list[str] | None = None
    rows: list[list[JsonScalar]] | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, value: Any) -> Any:
        return _enum_value(value, AnswerBlockType, lower=True)

    @field_validator("content", "label", "unit")
    @classmethod
    def _bounded_text(cls, value: str | None) -> str | None:
        return None if value is None else _validate_public_text(value)

    @field_validator("columns")
    @classmethod
    def _columns_safe(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            for column in value:
                _validate_public_text(column, allow_linebreaks=False)
        return value

    @field_validator("value")
    @classmethod
    def _value_safe(cls, value: JsonScalar) -> JsonScalar:
        return _validate_json_scalar(value)

    @field_validator("rows")
    @classmethod
    def _rows_safe(cls, value: list[list[JsonScalar]] | None) -> list[list[JsonScalar]] | None:
        if value is None:
            return None
        for row in value:
            for cell in row:
                _validate_json_scalar(cell)
        return value

    @model_validator(mode="after")
    def _shape(self) -> AnswerBlock:
        if self.type in {
            AnswerBlockType.TEXT,
            AnswerBlockType.ANALYSIS,
            AnswerBlockType.WARNING,
        }:
            if not self.content:
                raise ValueError(f"{self.type.value} block requires content")
        elif self.type is AnswerBlockType.FACT:
            if not self.label or self.value is None:
                raise ValueError("fact block requires label and value")
        elif self.type is AnswerBlockType.TABLE:
            if not self.columns:
                raise ValueError("table block requires non-empty columns")
            if any(not isinstance(column, str) or not column.strip() for column in self.columns):
                raise ValueError("table columns must be non-empty strings")
            if self.rows is None:
                raise ValueError("table block requires rows")
            width = len(self.columns)
            if any(len(row) != width for row in self.rows):
                raise ValueError("table rows must match columns width")
        return self

    @classmethod
    def from_domain(cls, block: DomainAnswerBlock | Mapping[str, Any]) -> AnswerBlock:
        if isinstance(block, DomainAnswerBlock):
            payload = block.model_dump(mode="python")
        else:
            payload = dict(block)
        return cls.model_validate(payload)


class PublicCorrection(_WireBase):
    status: CorrectionStatus
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, value: Any) -> Any:
        return _enum_value(value, CorrectionStatus, lower=True)

    @field_validator("message")
    @classmethod
    def _safe_message(cls, value: str) -> str:
        return _validate_public_text(value)

    @classmethod
    def from_domain(
        cls, correction: DomainPublicCorrection | Mapping[str, Any]
    ) -> PublicCorrection:
        payload = (
            correction.model_dump(mode="python")
            if isinstance(correction, DomainPublicCorrection)
            else dict(correction)
        )
        return cls.model_validate(payload)


_AS_OF_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


class ChatResponse(_WireBase):
    """Conversational 200 response shared by sync and SSE completion events."""

    request_id: UUID
    session_id: UUID
    status: ChatStatus
    answer_markdown: str = Field(min_length=1, max_length=20_000)
    blocks: list[AnswerBlock] = Field(default_factory=list, max_length=64)
    as_of_beijing: str | None = None
    evidence_state: EvidenceState
    data_origin: Literal["public", "demo_snapshot", "mixed", "none"] = "none"
    corrections: list[PublicCorrection] = Field(default_factory=list, max_length=16)
    follow_up: str | None = Field(default=None, max_length=1000)
    latency_ms: int = Field(ge=0)
    composition: CompositionInfo = Field(default_factory=CompositionInfo)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: Any) -> Any:
        return _wire_status(value)

    @field_validator("evidence_state", mode="before")
    @classmethod
    def _evidence_state(cls, value: Any) -> Any:
        return _enum_value(value, EvidenceState, lower=True)

    @field_validator("as_of_beijing")
    @classmethod
    def _as_of_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _AS_OF_RE.fullmatch(value):
            raise ValueError("as_of_beijing must use YYYY-MM-DD HH:mm")
        # ``strptime`` catches impossible dates while retaining the exact wire string.
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("as_of_beijing is not a valid date/time") from exc
        return value

    @field_validator("answer_markdown", "follow_up")
    @classmethod
    def _text_safe(cls, value: str | None) -> str | None:
        return None if value is None else _validate_public_text(value)

    @model_validator(mode="after")
    def _status_shape(self) -> ChatResponse:
        # Technical failures use ErrorResponse.  Keeping ``error`` out of this model makes
        # sync/SSE conversational envelopes equivalent and avoids half-populated failures.
        if self.status is ChatStatus.FAILED:
            raise ValueError("failed responses must use ErrorResponse")
        return self

    @classmethod
    def from_domain(
        cls,
        *,
        request_id: UUID,
        session_id: UUID,
        status: Any,
        answer: DraftAnswer | Mapping[str, Any],
        latency_ms: int,
        as_of_beijing: str | None = None,
    ) -> ChatResponse:
        if isinstance(answer, DraftAnswer):
            markdown = answer.markdown
            blocks = [AnswerBlock.from_domain(block) for block in answer.blocks]
            evidence_state = answer.evidence_state
            data_origin = "none"
            corrections = [PublicCorrection.from_domain(item) for item in answer.corrections]
            follow_up = answer.follow_up
        else:
            payload = dict(answer)
            markdown = payload.get("markdown", payload.get("answer_markdown", ""))
            blocks = [AnswerBlock.from_domain(item) for item in payload.get("blocks", []) or []]
            evidence_state = payload.get("evidence_state", EvidenceState.NONE)
            data_origin = payload.get("data_origin", "none")
            corrections = [
                PublicCorrection.from_domain(item) for item in payload.get("corrections", []) or []
            ]
            follow_up = payload.get("follow_up")
            composition = payload.get("composition", CompositionInfo())
        if isinstance(answer, DraftAnswer):
            composition = CompositionInfo()
        return cls(
            request_id=request_id,
            session_id=session_id,
            status=_wire_status(status),
            answer_markdown=markdown,
            blocks=blocks,
            as_of_beijing=as_of_beijing,
            evidence_state=evidence_state,
            data_origin=data_origin,
            corrections=corrections,
            follow_up=follow_up,
            latency_ms=latency_ms,
            composition=composition,
        )


class ErrorDetail(_RequestBase):
    # Keep the enum object on Python access (the HTTP route maps ``code.value`` to an
    # HTTP status); Pydantic still renders the uppercase wire value in ``mode="json"``.
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=False,
        use_enum_values=False,
    )

    code: TechnicalErrorCode
    retryable: bool
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, value: Any) -> Any:
        raw = getattr(value, "value", value)
        return raw.upper() if isinstance(raw, str) else value

    @field_validator("message")
    @classmethod
    def _message_safe(cls, value: str) -> str:
        return _validate_public_text(value)


class ErrorResponse(_RequestBase):
    request_id: UUID
    session_id: UUID
    status: str = "failed"
    error: ErrorDetail

    @field_validator("status")
    @classmethod
    def _failed_only(cls, value: str) -> str:
        if value != "failed":
            raise ValueError("technical error status must be failed")
        return value


# Contract-friendly aliases.  Keeping one canonical class for each envelope avoids subtly
# different sync/SSE serialisations while allowing callers to use terminology from the docs.
AnswerEnvelope = ChatResponse
ChatResponseEnvelope = ChatResponse
ChatRequestSchema = ChatRequest
ChatResponseSchema = ChatResponse
AnswerBlockSchema = AnswerBlock
PublicCorrectionSchema = PublicCorrection
TechnicalError = ErrorDetail
ErrorEnvelope = ErrorResponse
ErrorDetailSchema = ErrorDetail


class DependencyStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    NOT_READY = "not_ready"


class HealthDependencies(_WireBase):
    session_store: DependencyStatus
    cache: DependencyStatus
    hermes: DependencyStatus


class HealthResponse(_WireBase):
    status: str
    version: str = "v1"
    mode: str
    dependencies: HealthDependencies

    @field_validator("status")
    @classmethod
    def _health_status(cls, value: str) -> str:
        if value not in {"ok", "degraded", "not_ready"}:
            raise ValueError("invalid health status")
        return value

    @field_validator("mode")
    @classmethod
    def _health_mode(cls, value: str) -> str:
        if value not in {"live", "fixture", "hybrid"}:
            raise ValueError("invalid health mode")
        return value


class HighlightGame(_WireBase):
    """Public scoreboard projection; provider/evidence internals are omitted."""

    game_id: str = Field(min_length=1, max_length=128)
    start_utc: datetime
    home_name: str = Field(min_length=1, max_length=200)
    home_abbreviation: str | None = Field(default=None, max_length=10)
    away_name: str = Field(min_length=1, max_length=200)
    away_abbreviation: str | None = Field(default=None, max_length=10)
    status: Literal["scheduled", "live", "final", "postponed", "unknown"]
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)
    series_game_number: int | None = Field(default=None, ge=1, le=20)
    venue_name: str | None = Field(default=None, max_length=240)
    venue_city: str | None = Field(default=None, max_length=160)
    venue_state: str | None = Field(default=None, max_length=120)
    venue_country: str | None = Field(default=None, max_length=120)
    # ``mixed`` is meaningful only for a list envelope.  Every selected card
    # must retain one server-owned trust origin of its own.
    data_origin: Literal["public", "demo_snapshot", "none"] = "none"

    @field_validator("start_utc")
    @classmethod
    def _utc_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("start_utc must include a timezone")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError("start_utc must be UTC")
        return value


class HighlightLeader(_WireBase):
    """Safe, compact leader projection used by the scoreboard detail view."""

    player_name: str = Field(min_length=1, max_length=200)
    points: int | None = Field(default=None, ge=0)
    rebounds: int | None = Field(default=None, ge=0)
    assists: int | None = Field(default=None, ge=0)

    @field_validator("player_name")
    @classmethod
    def _player_name_safe(cls, value: str) -> str:
        return _validate_public_text(value)


class HighlightPlay(_WireBase):
    """UI-ready play-by-play row with provider metadata removed."""

    period: int = Field(ge=1, le=10)
    clock: str = Field(min_length=1, max_length=16)
    team: str | None = Field(default=None, max_length=10)
    player_name: str | None = Field(default=None, max_length=200)
    action: str = Field(min_length=1, max_length=120)
    detail: str | None = Field(default=None, max_length=240)
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)

    @field_validator("clock", "team", "player_name", "action", "detail")
    @classmethod
    def _play_text_safe(cls, value: str | None) -> str | None:
        return None if value is None else _validate_public_text(value)


class HighlightDetailResponse(_WireBase):
    """Detailed, user-facing data loaded after selecting one game."""

    game: HighlightGame
    leaders: list[HighlightLeader] = Field(default_factory=list, max_length=16)
    plays: list[HighlightPlay] = Field(default_factory=list, max_length=2000)
    as_of_beijing: str | None = None
    evidence_state: EvidenceState
    data_origin: Literal["public", "demo_snapshot", "mixed", "none"] = "none"

    @field_validator("as_of_beijing")
    @classmethod
    def _as_of_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _AS_OF_RE.fullmatch(value):
            raise ValueError("as_of_beijing must use YYYY-MM-DD HH:mm")
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("as_of_beijing is not a valid date/time") from exc
        return value


class HighlightsResponse(_WireBase):
    date: str
    timezone: str
    games: list[HighlightGame] = Field(default_factory=list)
    as_of_beijing: str | None = None
    evidence_state: EvidenceState
    data_origin: Literal["public", "demo_snapshot", "mixed", "none"] = "none"

    @field_validator("date")
    @classmethod
    def _strict_date(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("date must use YYYY-MM-DD")
        datetime.strptime(value, "%Y-%m-%d")
        return value

    @field_validator("as_of_beijing")
    @classmethod
    def _as_of_format(cls, value: str | None) -> str | None:
        """Keep highlights freshness metadata aligned with the chat envelope.

        ``as_of_beijing`` is a user-visible timestamp, not an arbitrary provider
        string.  Requiring the same minute-precision Beijing format as
        ``ChatResponse`` prevents malformed dates, control characters, and raw
        upstream values from crossing the public scoreboard contract.
        """

        if value is None:
            return None
        if not _AS_OF_RE.fullmatch(value):
            raise ValueError("as_of_beijing must use YYYY-MM-DD HH:mm")
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("as_of_beijing is not a valid date/time") from exc
        return value

    @field_validator("timezone")
    @classmethod
    def _iana_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            return ZoneInfo(value.strip()).key
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an IANA timezone") from exc


class HighlightsRangeResponse(_WireBase):
    """Public scoreboard projection for a bounded local-date interval."""

    timezone: str
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    games: list[HighlightGame] = Field(default_factory=list, max_length=2000)
    as_of_beijing: str | None = None
    evidence_state: EvidenceState
    data_origin: Literal["public", "demo_snapshot", "mixed", "none"] = "none"

    @field_validator("from_date", "to_date")
    @classmethod
    def _strict_range_date(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("date must use YYYY-MM-DD")
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("date is not valid") from exc
        return value

    @model_validator(mode="after")
    def _range_is_ordered(self) -> HighlightsRangeResponse:
        start = datetime.strptime(self.from_date, "%Y-%m-%d").date()
        end = datetime.strptime(self.to_date, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("range must be ordered")
        return self

    @field_validator("as_of_beijing")
    @classmethod
    def _as_of_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _AS_OF_RE.fullmatch(value):
            raise ValueError("as_of_beijing must use YYYY-MM-DD HH:mm")
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("as_of_beijing is not a valid date/time") from exc
        return value

    @field_validator("timezone")
    @classmethod
    def _iana_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            return ZoneInfo(value.strip()).key
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an IANA timezone") from exc


class HighlightAvailabilityDay(_WireBase):
    """Public, tri-state availability for one local calendar day.

    ``available`` means at least one normalized game was returned and ``empty`` means the
    provider completed successfully with no games for that day.  ``unknown``
    is deliberately separate: a timeout, partial upstream response, or a
    future day must never be presented as a confirmed no-game date.
    """

    date: str
    status: Literal["available", "empty", "unknown"]
    game_count: int | None = Field(default=None, ge=0)
    is_future: bool = False

    @field_validator("date")
    @classmethod
    def _strict_date(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("date must use YYYY-MM-DD")
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("date is not valid") from exc
        return value


class HighlightsAvailabilityResponse(_WireBase):
    """Bounded calendar projection used to disable dates without games."""

    timezone: str
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    days: list[HighlightAvailabilityDay] = Field(default_factory=list, max_length=31)
    as_of_beijing: str | None = None
    evidence_state: EvidenceState

    @field_validator("from_date", "to_date")
    @classmethod
    def _strict_range_date(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("date must use YYYY-MM-DD")
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("date is not valid") from exc
        return value

    @model_validator(mode="after")
    def _range_is_ordered(self) -> HighlightsAvailabilityResponse:
        start = datetime.strptime(self.from_date, "%Y-%m-%d").date()
        end = datetime.strptime(self.to_date, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("availability range must be ordered")
        if (end - start).days + 1 > 31:
            raise ValueError("availability range is limited to 31 days")
        if len(self.days) != (end - start).days + 1:
            raise ValueError("days must cover the requested range")
        expected = [
            (start + timedelta(days=index)).isoformat()
            for index in range((end - start).days + 1)
        ]
        if [item.date for item in self.days] != expected:
            raise ValueError("days must be ordered and contiguous")
        return self

    @field_validator("as_of_beijing")
    @classmethod
    def _as_of_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _AS_OF_RE.fullmatch(value):
            raise ValueError("as_of_beijing must use YYYY-MM-DD HH:mm")
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("as_of_beijing is not a valid date/time") from exc
        return value

    @field_validator("timezone")
    @classmethod
    def _iana_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            return ZoneInfo(value.strip()).key
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an IANA timezone") from exc


class RunStartedPayload(_WireBase):
    request_id: UUID
    session_id: UUID


_RUN_STATUS_TEXT_ALLOWLIST = frozenset(
    {
        "已确认问题范围",
        "正在查找相关比赛数据",
        "正在核对比赛数据",
        "正在整理回答",
        "正在生成智能分析",
    }
)


class RunStatusPayload(_WireBase):
    stage: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=500)

    @field_validator("stage", "text")
    @classmethod
    def _safe_status_text(cls, value: str) -> str:
        return _validate_public_text(value)

    @model_validator(mode="after")
    def _no_unverified_numbers(self) -> RunStatusPayload:
        # Status frames are emitted before the final verified answer.  Exact
        # application-owned copy is safe; extension text is accepted only
        # when it cannot preview a score, date, rank, or other factual number.
        if self.text not in _RUN_STATUS_TEXT_ALLOWLIST and re.search(r"\d", self.text):
            raise ValueError("run.status text cannot contain factual numbers")
        return self


class MessageDeltaPayload(_WireBase):
    text: str = Field(min_length=1, max_length=16_384)

    @field_validator("text")
    @classmethod
    def _delta_safe(cls, value: str) -> str:
        return _validate_public_text(value)


class ClarificationRequiredPayload(_WireBase):
    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question")
    @classmethod
    def _question_safe(cls, value: str) -> str:
        """Keep control characters out of an SSE branch payload.

        ``question`` is rendered directly by clients.  Unlike the normal chat
        request, this model is also used by the low-level SSE serializer, so it
        needs its own boundary validation rather than relying on ``ChatRequest``.
        """

        return _validate_public_text(value)


class SafetyBlockedPayload(_WireBase):
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def _message_safe(cls, value: str) -> str:
        return _validate_public_text(value)


class RunErrorPayload(ErrorResponse):
    """SSE ``run.error`` payload (same technical error envelope)."""


__all__ = [
    "AnswerBlock",
    "AnswerBlockType",
    "AnswerEnvelope",
    "AnswerBlockSchema",
    "ChatRequest",
    "ChatRequestSchema",
    "ChatResponse",
    "ChatResponseEnvelope",
    "ChatResponseSchema",
    "ChatStatus",
    "ClarificationRequiredPayload",
    "CorrectionStatus",
    "CompositionInfo",
    "CompositionMode",
    "CompositionStatus",
    "DependencyStatus",
    "ErrorDetail",
    "ErrorDetailSchema",
    "ErrorEnvelope",
    "ErrorResponse",
    "EvidenceState",
    "HealthDependencies",
    "HealthResponse",
    "HighlightAvailabilityDay",
    "HighlightDetailResponse",
    "HighlightsAvailabilityResponse",
    "HighlightGame",
    "HighlightLeader",
    "HighlightPlay",
    "HighlightsResponse",
    "HighlightsRangeResponse",
    "JsonScalar",
    "MessageDeltaPayload",
    "PublicCorrection",
    "PublicCorrectionSchema",
    "RunErrorPayload",
    "RunStartedPayload",
    "RunStatusPayload",
    "SafetyBlockedPayload",
    "TechnicalError",
    "TechnicalErrorCode",
]
