"""Canonical domain models for the NBA Chat Agent.

The models in this module are deliberately independent from FastAPI, a concrete
provider, or an LLM runtime.  They are the typed seam shared by the application,
fixture/live providers, and the evaluation runner.  The names and invariants
mirror ``specs/001-nba-chat-agent/data-model.md``; wire schemas may expose a
smaller, sanitised projection of these objects.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Scalar = str | int | float | bool | None
# Values crossing the public answer-block boundary are deliberately scalar.
# Keeping this alias separate from the broader ``Any`` fields used for internal
# evidence/claims prevents provider-shaped dictionaries from reaching clients.
type JsonScalar = str | int | float | bool | None


def _has_control_chars(value: str, *, allow_linebreaks: bool = True) -> bool:
    """Return whether *value* contains an unsafe ASCII control character.

    Newline and tab remain valid in user-facing prose/markdown.  DEL (0x7f) is
    rejected in every context; it is a control character even though it is not
    covered by the ``ord < 32`` check used by older validators.
    """

    for char in value:
        code = ord(char)
        if code == 0x7F or (code < 32 and (not allow_linebreaks or char not in "\n\t")):
            return True
    return False


def _validate_json_scalar(value: JsonScalar, *, allow_linebreaks: bool = False) -> JsonScalar:
    """Validate a finite, non-nested JSON scalar used in a wire block."""

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON scalar must be finite")
    if isinstance(value, str) and _has_control_chars(value, allow_linebreaks=allow_linebreaks):
        raise ValueError("control characters are not allowed")
    return value


_CONFERENCE_ALIASES: dict[str, str] = {
    "east": "East",
    "eastern": "East",
    "eastern conference": "East",
    "east conference": "East",
    "东部": "East",
    "东区": "East",
    "东部联盟": "East",
    "东部赛区": "East",
    "west": "West",
    "western": "West",
    "western conference": "West",
    "west conference": "West",
    "西部": "West",
    "西区": "West",
    "西部联盟": "West",
    "西部赛区": "West",
}


def canonical_conference(value: Any) -> str | None:
    """Normalise common provider/user conference labels.

    ``Standing.conference`` deliberately remains an open string because public
    providers use labels such as ``Eastern Conference``.  This helper gives
    filters a stable comparison value without constraining stored evidence.
    """

    if value is None:
        return None
    if isinstance(value, dict):
        value = (
            value.get("displayName")
            or value.get("display_name")
            or value.get("name")
            or value.get("abbreviation")
        )
    text = re.sub(r"\s+", " ", str(value).strip().casefold())
    return _CONFERENCE_ALIASES.get(text)


class _UpperStrEnum(str, Enum):
    """String enum whose values are the canonical upper-case names."""

    def __str__(self) -> str:  # pragma: no cover - useful for logs/debugging
        return self.value


class GameStatus(_UpperStrEnum):
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    FINAL = "FINAL"
    POSTPONED = "POSTPONED"
    UNKNOWN = "UNKNOWN"


class EntityKind(_UpperStrEnum):
    PLAYER = "PLAYER"
    TEAM = "TEAM"
    GAME = "GAME"
    SERIES = "SERIES"
    SEASON = "SEASON"
    UNKNOWN = "UNKNOWN"


class QueryPhase(_UpperStrEnum):
    RECEIVED = "RECEIVED"
    SAFETY_CHECKED = "SAFETY_CHECKED"
    CONTEXT_RESOLVED = "CONTEXT_RESOLVED"
    PARSED = "PARSED"
    PLAN_READY = "PLAN_READY"
    RETRIEVING = "RETRIEVING"
    NORMALIZED = "NORMALIZED"
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    DERIVED = "DERIVED"
    COMPOSED = "COMPOSED"
    OUTPUT_GUARDED = "OUTPUT_GUARDED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class QueryOutcome(_UpperStrEnum):
    COMPLETED = "COMPLETED"
    NO_DATA = "NO_DATA"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class StatScope(_UpperStrEnum):
    GAME = "GAME"
    SERIES = "SERIES"
    SEASON = "SEASON"
    CAREER = "CAREER"


class IntentName(_UpperStrEnum):
    DATA = "DATA"
    SCHEDULE_RESULT = "SCHEDULE_RESULT"
    HISTORY = "HISTORY"
    FACT_CHECK = "FACT_CHECK"
    PLAY_BY_PLAY = "PLAY_BY_PLAY"
    TACTICAL = "TACTICAL"
    RECAP = "RECAP"
    FOLLOW_UP = "FOLLOW_UP"
    SAFETY = "SAFETY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Category(_UpperStrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    H = "H"
    I = "I"  # noqa: E741 - category label required by the brief
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class SafetyCategory(_UpperStrEnum):
    POLITICS = "POLITICS"
    GEO_SENSITIVE = "GEO_SENSITIVE"
    SOCIAL_CONFLICT = "SOCIAL_CONFLICT"
    OFF_COURT_PRIVACY = "OFF_COURT_PRIVACY"
    RUMOR = "RUMOR"
    LEGAL_CRIME = "LEGAL_CRIME"
    FIXED_GAME_CONSPIRACY = "FIXED_GAME_CONSPIRACY"
    GAMBLING = "GAMBLING"
    ABUSE_HATE = "ABUSE_HATE"
    INSULT_NICKNAME = "INSULT_NICKNAME"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    ALLOW = "ALLOW"


class SafetyOutcome(_UpperStrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class EvidenceState(_UpperStrEnum):
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class CorrectionStatus(_UpperStrEnum):
    CORRECTED = "CORRECTED"
    UNVERIFIED = "UNVERIFIED"


class AnswerBlockType(_UpperStrEnum):
    TEXT = "TEXT"
    ANALYSIS = "ANALYSIS"
    WARNING = "WARNING"
    TABLE = "TABLE"
    FACT = "FACT"


class HistoryRecordType(_UpperStrEnum):
    CHAMPIONSHIP = "CHAMPIONSHIP"
    FRANCHISE_RECORD = "FRANCHISE_RECORD"
    LEAGUE_RECORD = "LEAGUE_RECORD"
    SERIES_RECORD = "SERIES_RECORD"


class EvaluationProviderMode(_UpperStrEnum):
    LIVE = "LIVE"
    FIXTURE = "FIXTURE"
    HYBRID = "HYBRID"


class ErrorCode(_UpperStrEnum):
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    AMBIGUOUS_ENTITY = "AMBIGUOUS_ENTITY"
    MISSING_SLOT = "MISSING_SLOT"
    NO_DATA = "NO_DATA"
    SERVICE_BUSY = "SERVICE_BUSY"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
    UPSTREAM_AUTH = "UPSTREAM_AUTH"
    INVALID_UPSTREAM_DATA = "INVALID_UPSTREAM_DATA"
    COMPOSER_UNAVAILABLE = "COMPOSER_UNAVAILABLE"
    OUTPUT_BLOCKED = "OUTPUT_BLOCKED"


class AdmissionResult(_UpperStrEnum):
    ADMITTED = "ADMITTED"
    RATE_LIMITED = "RATE_LIMITED"
    QUEUE_FULL = "QUEUE_FULL"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"


class RuntimeProfile(_UpperStrEnum):
    TEMPLATE = "TEMPLATE"
    HERMES = "HERMES"
    HYBRID = "HYBRID"


class IntelligenceMode(_UpperStrEnum):
    """Per-request language composition preference.

    ``HYBRID`` keeps objective answers deterministic and reserves the legacy
    composer for analysis intents. ``FULL`` enters the bounded Hermes Agent
    after safety/context and before deterministic parsing. The Agent may only
    call server-owned NBA tools; verification, arithmetic and PBP ownership
    remain deterministic.
    """

    HYBRID = "HYBRID"
    FULL = "FULL"


class HermesLiteMode(_UpperStrEnum):
    OFF = "OFF"
    EMBEDDED_SPIKE = "EMBEDDED_SPIKE"
    EMBEDDED_AGENT = "EMBEDDED_AGENT"
    SIDECAR = "SIDECAR"


class HermesStatus(_UpperStrEnum):
    """Observed status of the optional Hermes-lite composer.

    Keep this enum in the domain layer rather than importing the application
    port type.  QueryRecord is persisted/validated independently of the
    runtime adapter, and the wire contract only permits these four values.
    """

    OK = "OK"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    UNSAFE = "UNSAFE"


class QueryMode(_UpperStrEnum):
    OBJECTIVE = "OBJECTIVE"
    FACT_CHECK = "FACT_CHECK"
    ANALYSIS = "ANALYSIS"
    SAFETY = "SAFETY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Operation(_UpperStrEnum):
    LOOKUP = "LOOKUP"
    AGGREGATE = "AGGREGATE"
    COMPARE = "COMPARE"
    EXPLAIN = "EXPLAIN"


class TimeWindowScope(_UpperStrEnum):
    GAME_END = "GAME_END"
    PERIOD_END = "PERIOD_END"


class PlayEventType(_UpperStrEnum):
    SHOT = "SHOT"
    FREE_THROW = "FREE_THROW"
    FOUL = "FOUL"
    TURNOVER = "TURNOVER"
    REBOUND = "REBOUND"
    SUBSTITUTION = "SUBSTITUTION"
    OTHER = "OTHER"


class ShotType(_UpperStrEnum):
    TWO_POINT = "TWO_POINT"
    THREE_POINT = "THREE_POINT"
    FREE_THROW = "FREE_THROW"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class SeriesStage(_UpperStrEnum):
    REGULAR = "REGULAR"
    PLAY_IN = "PLAY_IN"
    PLAYOFF = "PLAYOFF"
    FINALS = "FINALS"


class SourceClass(_UpperStrEnum):
    OFFICIAL = "OFFICIAL"
    ESTABLISHED_SPORTS = "ESTABLISHED_SPORTS"
    NEWS = "NEWS"
    SEARCH = "SEARCH"
    FIXTURE = "FIXTURE"


class TrustLevel(_UpperStrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Freshness(_UpperStrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class VerificationState(_UpperStrEnum):
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    UNVERIFIED = "UNVERIFIED"
    CONTRADICTED = "CONTRADICTED"


class CanonicalModel(BaseModel):
    """Base configuration shared by domain models."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=False,
        use_enum_values=False,
    )


def _aware(value: datetime | None) -> datetime | None:
    """Validate timezone awareness while preserving optional ``None`` values.

    Pydantic invokes field validators for explicitly supplied ``None`` on
    optional datetime fields (for example a play event with no wall-clock
    timestamp).  The previous implementation dereferenced ``None`` and turned
    a legitimate missing provider field into an internal 500.  Required
    datetime fields still reject ``None`` through their type validation after
    this validator returns.
    """

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


def _iana(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("timezone must not be empty")
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid IANA timezone: {value}") from exc
    return value


class EntityRef(CanonicalModel):
    kind: EntityKind
    canonical_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=32)
    confidence: Decimal = Field(default=Decimal("1"), ge=0, le=1)

    @field_validator("canonical_id", "display_name")
    @classmethod
    def _non_control_text(cls, value: str) -> str:
        if _has_control_chars(value, allow_linebreaks=False):
            raise ValueError("control characters are not allowed")
        return value


class SeasonLabel(CanonicalModel):
    start_year: int = Field(ge=1900, le=2200)
    end_year: int = Field(ge=1901, le=2201)
    label: str = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def _validate_label(self) -> SeasonLabel:
        if self.end_year != self.start_year + 1:
            raise ValueError("end_year must equal start_year + 1")
        expected = f"{self.start_year:04d}-{self.end_year % 100:02d}"
        if self.label != expected:
            raise ValueError("season label does not match start/end years")
        return self


class TimeContext(CanonicalModel):
    instant_utc: datetime
    input_timezone: str = "Asia/Shanghai"
    display_timezone: str = "Asia/Shanghai"
    season: SeasonLabel | None = None
    relative_phrase: str | None = None

    _validate_instant = field_validator("instant_utc")(_aware)
    _validate_input_tz = field_validator("input_timezone", "display_timezone")(_iana)


class MetricRef(CanonicalModel):
    name: str = Field(min_length=1, max_length=100)
    unit: str | None = Field(default=None, max_length=40)
    scope: StatScope
    # ``rank`` keeps an ordinal attached to the requested statistic (for
    # example “得分第三”), rather than asking a renderer to infer it from
    # the raw message or silently falling back to the points leader.
    rank: int | None = Field(default=None, ge=1, le=100)


class TimeWindow(CanonicalModel):
    start_seconds: Decimal = Field(ge=0, le=60)
    end_seconds: Decimal = Field(ge=0, le=60)
    semantics: str = "PERIOD_CLOCK_REMAINING"
    scope: TimeWindowScope = TimeWindowScope.GAME_END
    # ``PERIOD_END`` normally targets one explicitly named period.  Natural
    # language such as “每节最后 5 秒” is the one intentional exception: the
    # selector materialises a separate window for every period in the complete
    # bundle.  A flag is safer than a sentinel period number, which could be
    # mistaken for an actual NBA quarter.
    all_periods: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.start_seconds > self.end_seconds:
            raise ValueError("start_seconds must be <= end_seconds")
        if self.semantics != "PERIOD_CLOCK_REMAINING":
            raise ValueError("unsupported time-window semantics")
        if self.all_periods and self.scope is not TimeWindowScope.PERIOD_END:
            raise ValueError("all_periods is only valid for PERIOD_END windows")
        return self


class DateRange(CanonicalModel):
    start_inclusive: datetime
    end_exclusive: datetime

    _validate_start = field_validator("start_inclusive", "end_exclusive")(_aware)

    @model_validator(mode="after")
    def _ordered(self) -> DateRange:
        if self.start_inclusive >= self.end_exclusive:
            raise ValueError("date range must be a non-empty half-open interval")
        return self


class StatsQuery(CanonicalModel):
    subject: EntityRef
    scope: StatScope
    season: SeasonLabel | None = None
    game_id: str | None = None
    series_id: str | None = None
    date_range: DateRange | None = None

    @model_validator(mode="after")
    def _scope_fields(self) -> StatsQuery:
        if self.scope is StatScope.GAME and not self.game_id:
            raise ValueError("GAME stats require game_id")
        if self.scope is StatScope.SERIES and not self.series_id:
            raise ValueError("SERIES stats require series_id")
        if self.scope is StatScope.SEASON and self.season is None:
            raise ValueError("SEASON stats require season")
        return self


class Claim(CanonicalModel):
    subject: EntityRef
    predicate: str = Field(min_length=1, max_length=120)
    claimed_value: Any


class Slot(CanonicalModel):
    name: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=500)


class Correction(CanonicalModel):
    claim: Claim
    verified_value: Any = None
    status: CorrectionStatus


class PublicCorrection(CanonicalModel):
    status: CorrectionStatus
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("message")
    @classmethod
    def _safe_message(cls, value: str) -> str:
        if _has_control_chars(value):
            raise ValueError("control characters are not allowed")
        return value


class TurnSummary(CanonicalModel):
    turn_index: int = Field(ge=1)
    user_intent: str = Field(min_length=1, max_length=120)
    # The bounded, post-safety user wording lets the full Agent reconstruct a
    # logical conversation without enabling Hermes' filesystem/native memory.
    # It remains session-scoped and expires with ConversationContext.
    user_message: str | None = Field(default=None, max_length=1000)
    active_refs: list[EntityRef] = Field(default_factory=list, max_length=16)
    verified_fact_ids: list[str] = Field(default_factory=list, max_length=128)
    text_summary: str = Field(min_length=1, max_length=2048)

    @field_validator("user_message")
    @classmethod
    def _safe_user_message(cls, value: str | None) -> str | None:
        if value is not None and _has_control_chars(value):
            raise ValueError("control characters are not allowed")
        return value


class SafetyDecision(CanonicalModel):
    outcome: SafetyOutcome
    category: SafetyCategory
    confidence: Decimal = Field(ge=0, le=1)
    refusal_template_id: str | None = Field(default=None, max_length=100)


class AnswerBlock(CanonicalModel):
    """A sanitised user-facing answer block.

    Unknown fields are ignored intentionally: the HTTP contract allows clients
    to evolve while preventing provider metadata from being rendered.
    """

    model_config = ConfigDict(extra="ignore", validate_assignment=True, use_enum_values=False)

    type: AnswerBlockType
    content: str | None = None
    label: str | None = None
    value: JsonScalar = None
    unit: str | None = None
    columns: list[str] | None = None
    rows: list[list[JsonScalar]] | None = None

    @field_validator("content", "label", "unit")
    @classmethod
    def _text_safe(cls, value: str | None) -> str | None:
        if value is not None and _has_control_chars(value):
            raise ValueError("control characters are not allowed")
        return value

    @field_validator("columns")
    @classmethod
    def _columns_safe(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(
            _has_control_chars(item, allow_linebreaks=False) for item in value
        ):
            raise ValueError("control characters are not allowed")
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
                raise ValueError("FACT block requires label and value")
        elif self.type is AnswerBlockType.TABLE:
            if not self.columns:
                raise ValueError("TABLE block requires non-empty columns")
            if self.rows is None:
                raise ValueError("TABLE block requires rows")
            width = len(self.columns)
            if any(len(row) != width for row in self.rows):
                raise ValueError("TABLE rows must match columns width")
        return self


class DraftAnswer(CanonicalModel):
    markdown: str = Field(min_length=1, max_length=20000)
    blocks: list[AnswerBlock] = Field(default_factory=list, max_length=64)
    evidence_state: EvidenceState
    corrections: list[PublicCorrection] = Field(default_factory=list, max_length=16)
    follow_up: str | None = Field(default=None, max_length=1000)

    @field_validator("markdown", "follow_up")
    @classmethod
    def _text_safe(cls, value: str | None) -> str | None:
        if value is not None and _has_control_chars(value):
            raise ValueError("control characters are not allowed")
        return value


class FactBundle(CanonicalModel):
    facts: list[FactAssertion] = Field(default_factory=list, max_length=500)
    missing: list[str] = Field(default_factory=list, max_length=100)
    corrections: list[Correction] = Field(default_factory=list, max_length=32)
    evidence_state: EvidenceState = EvidenceState.NONE


class SeasonRange(CanonicalModel):
    start_inclusive: SeasonLabel
    end_inclusive: SeasonLabel

    @model_validator(mode="after")
    def _ordered(self) -> SeasonRange:
        if self.start_inclusive.start_year > self.end_inclusive.start_year:
            raise ValueError("season range must be ordered")
        return self


class ChatRequest(CanonicalModel):
    session_id: UUID | None = None
    message: str = Field(min_length=1, max_length=2000)
    client_timezone: str | None = None
    client_message_id: str | None = Field(default=None, max_length=128)
    intelligence_mode: IntelligenceMode | None = None
    # Optional scoreboard selection supplied by the web demo.  This is only a
    # context hint: the application resolves it against server-known game
    # records before using it for planning, and never trusts client-provided
    # scores/team names.
    selected_game_id: str | None = Field(default=None, max_length=128)

    @field_validator("intelligence_mode", mode="before")
    @classmethod
    def _intelligence_mode_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, IntelligenceMode):
            return value
        if isinstance(value, str):
            if value.strip().lower() == "auto":
                return None
            try:
                return IntelligenceMode(value.strip().upper())
            except ValueError:
                return value
        return value

    @field_validator("message")
    @classmethod
    def _message_valid(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        if _has_control_chars(value):
            raise ValueError("control characters are not allowed")
        return value

    @field_validator("client_message_id")
    @classmethod
    def _client_message_id_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("client_message_id must not be blank")
        # Idempotency keys are persisted and may appear in internal telemetry;
        # reject control characters and line breaks at the request boundary.
        if _has_control_chars(value, allow_linebreaks=False):
            raise ValueError("control characters are not allowed")
        return value

    @field_validator("selected_game_id")
    @classmethod
    def _selected_game_id_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
            raise ValueError("selected_game_id format is invalid")
        return value

    @field_validator("client_timezone")
    @classmethod
    def _timezone_valid(cls, value: str | None) -> str | None:
        return None if value is None else _iana(value)


class QueryIntent(CanonicalModel):
    category: Category
    intent_name: IntentName
    mode: QueryMode
    confidence: Decimal = Field(ge=0, le=1)
    entities: list[EntityRef] = Field(default_factory=list, max_length=32)
    metrics: list[MetricRef] = Field(default_factory=list, max_length=32)
    season: SeasonLabel | None = None
    # Optional conference scope for standings queries.  The parser stores the
    # canonical English values (``East``/``West``); keeping this as a plain
    # string preserves compatibility with provider-specific conference labels
    # while allowing the gateway to normalise aliases at its trust boundary.
    conference: str | None = Field(default=None, max_length=64)
    date_range: DateRange | None = None
    game_number: int | None = Field(default=None, ge=1, le=20)
    period: int | None = Field(default=None, ge=1, le=20)
    clock_window: TimeWindow | None = None
    operation: Operation = Operation.LOOKUP
    premise_claims: list[Claim] = Field(default_factory=list, max_length=32)
    missing_slots: list[Slot] = Field(default_factory=list, max_length=16)
    # “最近一场比赛的关键回合” is uniquely resolvable even though it has
    # no explicit game ID. The planner uses this hint for a bounded latest
    # completed-game lookup before loading play-by-play data.
    recent_game: bool = False

    _CATEGORY_INTENT: ClassVar[dict[Category, IntentName]] = {
        Category.A: IntentName.DATA,
        Category.B: IntentName.SCHEDULE_RESULT,
        Category.C: IntentName.HISTORY,
        Category.D: IntentName.FACT_CHECK,
        Category.E: IntentName.PLAY_BY_PLAY,
        Category.F: IntentName.TACTICAL,
        Category.G: IntentName.RECAP,
        Category.H: IntentName.FOLLOW_UP,
        Category.I: IntentName.SAFETY,
        Category.OUT_OF_SCOPE: IntentName.OUT_OF_SCOPE,
    }

    @model_validator(mode="after")
    def _category_mapping(self) -> QueryIntent:
        expected = self._CATEGORY_INTENT.get(self.category)
        if expected is not None and self.intent_name is not expected:
            raise ValueError("category and intent_name do not match")
        return self


class ConversationContext(CanonicalModel):
    session_id: UUID
    version: int = Field(default=0, ge=0)
    timezone: str = "Asia/Shanghai"
    # Accurate count of successfully handled, safety-allowed user turns.  It
    # is independent from the bounded context window below and therefore does
    # not stop increasing when old summaries are evicted.
    completed_user_turn_count: int = Field(default=0, ge=0)
    # Number of summaries retained for entity/history projection.  This stays
    # bounded by ``recent_turn_summaries`` and is not a lifetime turn count.
    turn_count: int = Field(default=0, ge=0, le=8)
    active_game: EntityRef | None = None
    active_team: EntityRef | None = None
    active_player: EntityRef | None = None
    active_season: SeasonLabel | None = None
    recent_turn_summaries: list[TurnSummary] = Field(default_factory=list, max_length=8)
    expires_at_utc: datetime

    _validate_timezone = field_validator("timezone")(_iana)
    _validate_expiry = field_validator("expires_at_utc")(_aware)

    @model_validator(mode="after")
    def _summary_bound(self) -> ConversationContext:
        if len(self.recent_turn_summaries) > 8:
            raise ValueError("at most eight turn summaries are retained")
        return self


class Team(CanonicalModel):
    team_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    abbreviation: str | None = Field(default=None, max_length=10)
    aliases: list[str] = Field(default_factory=list, max_length=64)
    alias_history: list[dict[str, Any]] = Field(default_factory=list, max_length=64)


class Player(CanonicalModel):
    player_id: str = Field(min_length=1, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=64)
    current_team_id: str | None = Field(default=None, max_length=128)


class GameFilters(CanonicalModel):
    date_range: DateRange | None = None
    season: SeasonLabel | None = None
    team_ids: list[str] = Field(default_factory=list, max_length=32)
    status: GameStatus | None = None

    @field_validator("team_ids")
    @classmethod
    def _unique_team_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("team_ids must be unique")
        return value


class NewsQuery(CanonicalModel):
    subject_refs: list[EntityRef] = Field(default_factory=list, max_length=16)
    keywords: list[str] = Field(default_factory=list, max_length=8)
    date_range: DateRange | None = None
    limit: int = Field(default=10, ge=1, le=20)

    @field_validator("keywords")
    @classmethod
    def _keywords_valid(cls, value: list[str]) -> list[str]:
        for keyword in value:
            if not 1 <= len(keyword) <= 80:
                raise ValueError("news keywords must contain 1..80 characters")
            if _has_control_chars(keyword, allow_linebreaks=False):
                raise ValueError("control characters are not allowed in keywords")
        return value


class HistoryQuery(CanonicalModel):
    subject_refs: list[EntityRef] = Field(default_factory=list, max_length=16)
    season_range: SeasonRange | None = None
    record_type: HistoryRecordType
    limit: int = Field(default=20, ge=1, le=50)


class Venue(CanonicalModel):
    """Optional public game location; missing components remain unknown."""

    name: str = Field(min_length=1, max_length=240)
    city: str | None = Field(default=None, max_length=160)
    state: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)


class Game(CanonicalModel):
    game_id: str = Field(min_length=1, max_length=128)
    season: SeasonLabel
    start_utc: datetime
    home: EntityRef
    away: EntityRef
    status: GameStatus
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)
    series_id: str | None = Field(default=None, max_length=128)
    series_game_number: int | None = Field(default=None, ge=1, le=20)
    venue: Venue | None = None

    _validate_start = field_validator("start_utc")(_aware)

    @model_validator(mode="after")
    def _teams(self) -> Game:
        if self.home.kind is not EntityKind.TEAM or self.away.kind is not EntityKind.TEAM:
            raise ValueError("game home and away must be TEAM references")
        if self.home.canonical_id == self.away.canonical_id:
            raise ValueError("home and away teams must differ")
        if self.status in {GameStatus.SCHEDULED, GameStatus.POSTPONED} and (
            self.home_score is not None or self.away_score is not None
        ):
            raise ValueError("scheduled/postponed games cannot carry a score")
        return self


class StatLine(CanonicalModel):
    subject: EntityRef
    game_id: str | None = None
    series_id: str | None = None
    season: SeasonLabel | None = None
    scope: StatScope
    metrics: dict[str, float | int | Decimal | None] = Field(default_factory=dict)
    metric_definitions: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("metrics")
    @classmethod
    def _finite_metrics(
        cls, value: dict[str, float | int | Decimal | None]
    ) -> dict[str, float | int | Decimal | None]:
        for metric, number in value.items():
            if isinstance(number, float) and not math.isfinite(number):
                raise ValueError(f"metric {metric} must be finite")
            if isinstance(number, Decimal) and not number.is_finite():
                raise ValueError(f"metric {metric} must be finite")
        return value

    @model_validator(mode="after")
    def _scope_fields(self) -> StatLine:
        if self.scope is StatScope.GAME and not self.game_id:
            raise ValueError("GAME stat lines require game_id")
        if self.scope is StatScope.SERIES and not self.series_id:
            raise ValueError("SERIES stat lines require series_id")
        if self.scope is StatScope.SEASON and self.season is None:
            raise ValueError("SEASON stat lines require season")
        return self


class Standing(CanonicalModel):
    season: SeasonLabel
    team: EntityRef
    conference: str | None = None
    wins: int | None = Field(default=None, ge=0)
    losses: int | None = Field(default=None, ge=0)
    rank: int | None = Field(default=None, ge=1)
    as_of_utc: datetime | None = None

    _validate_as_of = field_validator("as_of_utc")(_aware)

    @model_validator(mode="after")
    def _team_kind(self) -> Standing:
        if self.team.kind is not EntityKind.TEAM:
            raise ValueError("standing subject must be a TEAM reference")
        return self


class SeriesRef(CanonicalModel):
    series_id: str = Field(min_length=1, max_length=128)
    season: SeasonLabel
    stage: SeriesStage
    home: EntityRef | None = None
    away: EntityRef | None = None

    @model_validator(mode="after")
    def _teams(self) -> SeriesRef:
        for team in (self.home, self.away):
            if team is not None and team.kind is not EntityKind.TEAM:
                raise ValueError("series participants must be TEAM references")
        if self.home and self.away and self.home.canonical_id == self.away.canonical_id:
            raise ValueError("series participants must differ")
        return self


class PlayEvent(CanonicalModel):
    event_id: str = Field(min_length=1, max_length=128)
    game_id: str = Field(min_length=1, max_length=128)
    sequence: int | None = Field(default=None, ge=0)
    provider_index: int = Field(ge=0)
    period: int = Field(ge=1)
    clock_seconds_remaining: Decimal = Field(ge=0, le=7200)
    event_type: PlayEventType
    shooter: EntityRef | None = None
    assister: EntityRef | None = None
    shot_type: ShotType = ShotType.UNKNOWN
    points: int | None = Field(default=None, ge=0)
    home_score_after: int | None = Field(default=None, ge=0)
    away_score_after: int | None = Field(default=None, ge=0)
    wallclock_utc: datetime | None = None
    raw_text_hash: str | None = Field(default=None, max_length=128)

    _validate_wallclock = field_validator("wallclock_utc")(_aware)


class PlayByPlayBundle(CanonicalModel):
    game_id: str = Field(min_length=1, max_length=128)
    events: list[PlayEvent] = Field(default_factory=list, max_length=10000)
    sequence_valid: bool = False

    @model_validator(mode="after")
    def _invariants(self) -> PlayByPlayBundle:
        if any(event.game_id != self.game_id for event in self.events):
            raise ValueError("all play events must belong to the bundle game")
        provider_indexes = [event.provider_index for event in self.events]
        if len(provider_indexes) != len(set(provider_indexes)):
            raise ValueError("provider_index must be unique within a PBP bundle")
        if self.sequence_valid:
            sequences = [event.sequence for event in self.events]
            if any(sequence is None for sequence in sequences):
                raise ValueError("sequence_valid requires a sequence on every event")
            if len(sequences) != len(set(sequences)):
                raise ValueError("sequence_valid requires unique sequences")
        return self


class GameBundle(CanonicalModel):
    game: Game
    stat_lines: list[StatLine] = Field(default_factory=list, max_length=500)
    series: SeriesRef | None = None
    leaders: list[StatLine] = Field(default_factory=list, max_length=100)
    plays: PlayByPlayBundle | None = None


class NewsItem(CanonicalModel):
    news_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    published_utc: datetime | None = None
    subject_refs: list[EntityRef] = Field(default_factory=list, max_length=16)
    summary: str | None = Field(default=None, max_length=4000)
    evidence_id: str = Field(min_length=1, max_length=128)

    _validate_published = field_validator("published_utc")(_aware)


class HistoryRecord(CanonicalModel):
    record_id: str = Field(min_length=1, max_length=128)
    record_type: HistoryRecordType
    subject: EntityRef | None = None
    season: SeasonLabel | None = None
    value: Any
    evidence_id: str = Field(min_length=1, max_length=128)


class Evidence(CanonicalModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    source_class: SourceClass
    source_ref: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2000)
    fetched_at_utc: datetime
    data_as_of_utc: datetime | None = None
    trust: TrustLevel
    freshness: Freshness

    _validate_fetched = field_validator("fetched_at_utc", "data_as_of_utc")(_aware)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not re.match(r"^https?://[^\s]+$", value):
            raise ValueError("evidence url must be an http(s) URL")
        return value


class FactAssertion(CanonicalModel):
    fact_id: str = Field(min_length=1, max_length=128)
    subject: EntityRef
    predicate: str = Field(min_length=1, max_length=160)
    value: Any = None
    # Optional temporal provenance for records whose value alone is not
    # sufficient to answer the question (for example, a championship record
    # whose value is the team name but whose user-facing answer needs the
    # season).  This is additive and optional so existing fact producers and
    # consumers keep the same contract; it is internal evidence metadata and
    # is never emitted as an unfiltered provider payload.
    season: SeasonLabel | None = None
    unit: str | None = Field(default=None, max_length=40)
    evidence_ids: list[str] = Field(default_factory=list, max_length=64)
    derived_from_fact_ids: list[str] = Field(default_factory=list, max_length=128)
    verification: VerificationState

    @model_validator(mode="after")
    def _evidence(self) -> FactAssertion:
        if (
            self.verification
            in {
                VerificationState.VERIFIED,
                VerificationState.PARTIAL,
            }
            and not self.evidence_ids
        ):
            raise ValueError("verified/partial facts require evidence ids")
        if self.derived_from_fact_ids and self.fact_id in self.derived_from_fact_ids:
            raise ValueError("a fact cannot derive from itself")
        return self


class ConversationRecord(CanonicalModel):
    session_id: UUID
    version: int = Field(default=0, ge=0)
    timezone: str = "Asia/Shanghai"
    completed_user_turn_count: int = Field(default=0, ge=0)
    retained_summary_count: int = Field(default=0, ge=0, le=8)
    active_refs: list[EntityRef] = Field(default_factory=list, max_length=32)
    turn_summaries: list[TurnSummary] = Field(default_factory=list, max_length=8)
    created_at_utc: datetime
    expires_at_utc: datetime

    _validate_timezone = field_validator("timezone")(_iana)
    _validate_times = field_validator("created_at_utc", "expires_at_utc")(_aware)

    @model_validator(mode="after")
    def _expiry(self) -> ConversationRecord:
        if self.created_at_utc >= self.expires_at_utc:
            raise ValueError("conversation must expire after creation")
        return self


class QueryRecord(CanonicalModel):
    request_id: UUID
    session_id: UUID
    raw_text_hash: str = Field(min_length=1, max_length=128)
    intent_category: Category | None = None
    intent_name: IntentName | None = None
    safety_category: SafetyCategory | None = None
    parsed_query: QueryIntent | None = None
    phase: QueryPhase = QueryPhase.RECEIVED
    outcome: QueryOutcome | None = None
    provider_call_count: int = Field(default=0, ge=0)
    cache_read_count: int = Field(default=0, ge=0)
    cache_write_count: int = Field(default=0, ge=0)
    evidence_state: EvidenceState = EvidenceState.NONE
    ttft_ms: int | None = Field(default=None, ge=0)
    total_latency_ms: int | None = Field(default=None, ge=0)
    error_code: ErrorCode | None = None
    admission_result: AdmissionResult | None = None
    queue_wait_ms: int | None = Field(default=None, ge=0)
    deadline_at_utc: datetime | None = None
    hermes_mode: HermesLiteMode | None = None
    hermes_status: HermesStatus | None = None
    fallback_reason: str | None = Field(default=None, max_length=500)
    agent_iteration_count: int = Field(default=0, ge=0, le=4)
    agent_tool_call_count: int = Field(default=0, ge=0, le=4)
    agent_tool_names: list[str] = Field(default_factory=list, max_length=4)

    _validate_deadline = field_validator("deadline_at_utc")(_aware)

    @field_validator("hermes_status", mode="before")
    @classmethod
    def _hermes_status(cls, value: Any) -> Any:
        if value is None:
            return None
        return value.upper() if isinstance(value, str) else value

    @field_validator("agent_tool_names")
    @classmethod
    def _agent_tool_allowlist(cls, value: list[str]) -> list[str]:
        allowed = {"nba_query", "nba_schedule", "nba_news"}
        if any(item not in allowed for item in value):
            raise ValueError("agent tool names must use the NBA allow-list")
        return value

    @model_validator(mode="after")
    def _lifecycle_invariants(self) -> QueryRecord:
        conversational_outcomes = {
            QueryOutcome.COMPLETED,
            QueryOutcome.NO_DATA,
            QueryOutcome.NEEDS_CLARIFICATION,
            QueryOutcome.BLOCKED,
        }

        # Outcomes are written only at a terminal phase.  This prevents a
        # partially persisted query from looking complete (or a failed query
        # from being reported as a successful one) after a restart.
        if self.phase is QueryPhase.COMPLETED:
            if self.outcome not in conversational_outcomes:
                raise ValueError("COMPLETED phase requires a non-failed terminal outcome")
        elif self.phase is QueryPhase.FAILED:
            if self.outcome is not QueryOutcome.FAILED:
                raise ValueError("FAILED phase requires failed outcome")
        elif self.outcome is not None:
            raise ValueError("non-terminal phase cannot have an outcome")

        short_circuit = (
            self.outcome is QueryOutcome.BLOCKED
            or self.safety_category is SafetyCategory.OUT_OF_SCOPE
        )
        if short_circuit:
            if (
                self.provider_call_count != 0
                or self.cache_read_count != 0
                or self.cache_write_count != 0
            ):
                raise ValueError("short-circuit outcomes cannot access provider/cache")
            if self.evidence_state is not EvidenceState.NONE:
                raise ValueError("short-circuit outcomes cannot carry evidence")
            if self.agent_iteration_count or self.agent_tool_call_count or self.agent_tool_names:
                raise ValueError("short-circuit outcomes cannot call the Agent or its tools")

        # An early clarification is also a local short-circuit when no source
        # or cache was touched.  It must not claim a verified/partial bundle.
        if (
            self.outcome is QueryOutcome.NEEDS_CLARIFICATION
            and self.provider_call_count == 0
            and self.cache_read_count == 0
            and self.cache_write_count == 0
            and self.evidence_state is not EvidenceState.NONE
        ):
            raise ValueError("early clarification without retrieval requires NONE evidence")
        if self.phase is QueryPhase.FAILED:
            if self.error_code is None:
                raise ValueError("FAILED phase requires an error code")
        if self.error_code is not None and self.outcome is not QueryOutcome.FAILED:
            raise ValueError("technical error code is only valid on failed outcomes")
        if (
            self.admission_result
            in {
                AdmissionResult.QUEUE_FULL,
                AdmissionResult.RATE_LIMITED,
                AdmissionResult.DEADLINE_EXCEEDED,
            }
            and self.provider_call_count != 0
        ):
            raise ValueError("rejected admission cannot call a provider")
        if self.error_code is ErrorCode.SERVICE_BUSY:
            if self.phase is not QueryPhase.FAILED or self.outcome is not QueryOutcome.FAILED:
                raise ValueError("SERVICE_BUSY must be a failed terminal outcome")
            if self.admission_result is AdmissionResult.ADMITTED:
                raise ValueError("SERVICE_BUSY cannot report an admitted request")
        if self.hermes_mode is HermesLiteMode.OFF and self.hermes_status is not None:
            raise ValueError("Hermes OFF mode cannot carry a runtime status")
        if self.intent_category is not None and self.intent_name is not None:
            expected = QueryIntent._CATEGORY_INTENT.get(self.intent_category)
            if expected is not None and self.intent_name is not expected:
                raise ValueError("intent_category and intent_name do not match")
        if self.parsed_query is not None:
            if (
                self.intent_category is not None
                and self.parsed_query.category is not self.intent_category
            ):
                raise ValueError("parsed_query category does not match telemetry")
            if (
                self.intent_name is not None
                and self.parsed_query.intent_name is not self.intent_name
            ):
                raise ValueError("parsed_query intent does not match telemetry")
        if self.phase in {
            QueryPhase.PARSED,
            QueryPhase.PLAN_READY,
            QueryPhase.RETRIEVING,
            QueryPhase.NORMALIZED,
            QueryPhase.VERIFIED,
            QueryPhase.UNVERIFIED,
            QueryPhase.DERIVED,
            QueryPhase.COMPOSED,
            QueryPhase.OUTPUT_GUARDED,
            QueryPhase.COMPLETED,
        } and (
            self.intent_category is None or self.intent_name is None or self.parsed_query is None
        ):
            # Safety/OUT_OF_SCOPE and early clarification branches are allowed
            # to complete before parsing.  A normal NO_DATA result still needs
            # its parsed intent so it cannot hide an attribution bug.
            can_complete_without_parse = self.safety_category in {
                SafetyCategory.OUT_OF_SCOPE,
                SafetyCategory.POLITICS,
                SafetyCategory.GEO_SENSITIVE,
                SafetyCategory.SOCIAL_CONFLICT,
                SafetyCategory.OFF_COURT_PRIVACY,
                SafetyCategory.RUMOR,
                SafetyCategory.LEGAL_CRIME,
                SafetyCategory.FIXED_GAME_CONSPIRACY,
                SafetyCategory.GAMBLING,
                SafetyCategory.ABUSE_HATE,
                SafetyCategory.INSULT_NICKNAME,
            } or (
                self.outcome
                in {
                    QueryOutcome.BLOCKED,
                    QueryOutcome.NEEDS_CLARIFICATION,
                }
                and self.provider_call_count == 0
                and self.cache_read_count == 0
                and self.cache_write_count == 0
            ) or (
                self.hermes_mode is HermesLiteMode.EMBEDDED_AGENT
                and self.hermes_status is HermesStatus.OK
                and self.outcome is QueryOutcome.COMPLETED
            )
            if not can_complete_without_parse:
                raise ValueError("parsed lifecycle phase requires intent fields")
        return self


class EvaluationTurn(CanonicalModel):
    turn_index: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=2000)
    expected_intent: IntentName
    expected_entities: Any
    reference_facts: Any
    tolerance: Any = None
    safety_expected: SafetyOutcome
    intelligence_mode: IntelligenceMode | None = None


class EvaluationCase(CanonicalModel):
    case_id: str = Field(min_length=1, max_length=128)
    category: Category
    turns: list[EvaluationTurn] = Field(min_length=1, max_length=8)
    source_snapshot: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _turns(self) -> EvaluationCase:
        indices = [turn.turn_index for turn in self.turns]
        if indices != list(range(1, len(indices) + 1)):
            raise ValueError("evaluation turn_index values must be contiguous from 1")
        if self.category is Category.H and len(self.turns) != 3:
            raise ValueError("category H evaluation cases require exactly three turns")
        return self


class EvaluationRun(CanonicalModel):
    run_id: UUID
    case_id: str = Field(min_length=1, max_length=128)
    # Category is duplicated from EvaluationCase in the persisted run so a
    # report remains self-describing even when cases are loaded from a later
    # fixture revision.
    category: Category | None = None
    repeat_index: int = Field(ge=1)
    provider_mode: EvaluationProviderMode
    ratings: dict[str, Any]
    scores: dict[str, float] | None = None
    safety_veto: bool = False
    evidence_state: EvidenceState
    corrections: list[PublicCorrection] = Field(default_factory=list, max_length=32)
    ttft_ms: int | None = Field(default=None, ge=0)
    total_latency_ms: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=4000)


__all__ = [
    "AdmissionResult",
    "AnswerBlock",
    "AnswerBlockType",
    "Category",
    "canonical_conference",
    "ChatRequest",
    "Claim",
    "ConversationContext",
    "ConversationRecord",
    "Correction",
    "CorrectionStatus",
    "DateRange",
    "DraftAnswer",
    "EntityKind",
    "EntityRef",
    "ErrorCode",
    "EvaluationCase",
    "EvaluationProviderMode",
    "EvaluationRun",
    "EvaluationTurn",
    "Evidence",
    "EvidenceState",
    "FactAssertion",
    "FactBundle",
    "Freshness",
    "Game",
    "GameBundle",
    "GameFilters",
    "GameStatus",
    "HermesLiteMode",
    "HermesStatus",
    "HistoryQuery",
    "HistoryRecord",
    "HistoryRecordType",
    "IntentName",
    "IntelligenceMode",
    "JsonScalar",
    "MetricRef",
    "NewsItem",
    "NewsQuery",
    "Operation",
    "PlayByPlayBundle",
    "PlayEvent",
    "PlayEventType",
    "Player",
    "PublicCorrection",
    "QueryIntent",
    "QueryMode",
    "QueryOutcome",
    "QueryPhase",
    "QueryRecord",
    "RuntimeProfile",
    "SafetyCategory",
    "SafetyDecision",
    "SafetyOutcome",
    "SeasonLabel",
    "SeasonRange",
    "SeriesRef",
    "SeriesStage",
    "ShotType",
    "Slot",
    "SourceClass",
    "Standing",
    "StatLine",
    "StatScope",
    "StatsQuery",
    "Team",
    "TimeContext",
    "TimeWindow",
    "TimeWindowScope",
    "TrustLevel",
    "TurnSummary",
    "Venue",
    "VerificationState",
]
