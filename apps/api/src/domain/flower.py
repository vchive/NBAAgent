"""Domain contracts for the flower-growing assistant.

The original application was built around basketball entities.  Flower support is
kept in a small, dependency-free module so the reusable chat/session machinery can
be switched to a different domain without making the HTTP layer know about plant
details.  Models in this file are deliberately conservative: external search text
is bounded and cleaned, timestamps are timezone aware, and a context never stores
an unbounded transcript.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_RE = re.compile(r"<[^>]{0,500}>")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>]{1,500}", re.IGNORECASE)


def _safe_text(value: Any, *, max_length: int, allow_linebreaks: bool = True) -> str:
    """Normalise and bound user/source text without silently inventing content."""

    # Do not NFKC-normalise the value returned to a user: NFKC changes Chinese
    # full-width punctuation (for example ``，`` to ``,``), which makes an
    # otherwise natural Chinese answer look machine-generated.  Matching code
    # performs its own NFKC pass where needed.
    text = str(value or "").strip()
    if _CONTROL_RE.search(text) or (not allow_linebreaks and any(c in text for c in "\r\n\t")):
        raise ValueError("text contains control characters")
    if len(text) > max_length:
        raise ValueError(f"text exceeds {max_length} characters")
    return text


def clean_external_text(value: Any, *, max_length: int = 1200) -> str:
    """Clean an untrusted search snippet before it enters the domain.

    Search results are observations, not instructions.  HTML, invisible format
    characters and obvious prompt-injection fragments are removed.  The model is
    still allowed to retain a useful title/snippet when only one unsafe sentence
    appears in a larger result.
    """

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = html.unescape(_HTML_RE.sub(" ", text))
    text = _URL_RE.sub("", text)
    text = re.sub(
        r"(?:ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?|"
        r"system\s+prompt|developer\s+message|tool\s*call|"
        r"忽略(?:之前|先前|所有)?指令|系统提示|开发者消息|工具调用)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = " ".join(text.split())
    return text[:max_length]


class FlowerModel(BaseModel):
    """Base configuration shared by flower domain DTOs."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
        str_strip_whitespace=False,
    )


class LightLevel(StrEnum):
    FULL_SUN = "full_sun"
    PARTIAL_SUN = "partial_sun"
    BRIGHT_INDIRECT = "bright_indirect"
    SHADE = "shade"
    UNKNOWN = "unknown"


class ContainerType(StrEnum):
    POT = "pot"
    WINDOW_BOX = "window_box"
    GROUND = "ground"
    HYDROPONIC = "hydroponic"
    UNKNOWN = "unknown"


class ToxicityLevel(StrEnum):
    NONE_KNOWN = "none_known"
    LOW = "low"
    CAUTION = "caution"
    TOXIC = "toxic"
    UNKNOWN = "unknown"


class VerificationLevel(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    UNVERIFIED = "unverified"


class FlowerIntentName(StrEnum):
    PLANT_SELECTION = "plant_selection"
    IDENTIFICATION = "identification"
    WATERING = "watering"
    LIGHT = "light"
    SOIL = "soil"
    FERTILIZING = "fertilizing"
    PRUNING = "pruning"
    PROPAGATION = "propagation"
    PEST_DISEASE = "pest_disease"
    SEASONAL_PLAN = "seasonal_plan"
    GENERAL_CARE = "general_care"
    SAFETY = "safety"
    OUT_OF_SCOPE = "out_of_scope"


class SafetyCategory(StrEnum):
    CHEMICAL_MIXING = "chemical_mixing"
    UNKNOWN_INGESTION = "unknown_ingestion"
    PET_EXPOSURE = "pet_exposure"
    HUMAN_EXPOSURE = "human_exposure"
    HIGH_RISK_CHEMICAL = "high_risk_chemical"
    UNSAFE_DISPOSAL = "unsafe_disposal"
    UNKNOWN = "unknown"


class PlantProfile(FlowerModel):
    """Curated facts and conditional care principles for a common flower.

    ``canonical_name`` accepts ``name`` as an input alias for callers that use a
    simpler vocabulary.  The output remains stable and uses the canonical field.
    """

    canonical_name: str = Field(
        validation_alias=AliasChoices("canonical_name", "name"),
        min_length=1,
        max_length=100,
    )
    scientific_name: str | None = Field(default=None, max_length=160)
    aliases: list[str] = Field(default_factory=list, max_length=32)
    light_preference: str = Field(
        validation_alias=AliasChoices("light_preference", "light", "light_requirement"),
        min_length=1,
        max_length=300,
    )
    temperature_range_c: tuple[float, float] | None = Field(default=None)
    soil_preference: str = Field(
        validation_alias=AliasChoices("soil_preference", "soil"),
        min_length=1,
        max_length=500,
    )
    watering_principle: str = Field(
        validation_alias=AliasChoices("watering_principle", "watering", "water"),
        min_length=1,
        max_length=500,
    )
    fertilization_principle: str = Field(
        validation_alias=AliasChoices(
            "fertilization_principle", "fertilising_principle", "fertilization", "fertilizer"
        ),
        min_length=1,
        max_length=500,
    )
    pruning_principle: str | None = Field(default=None, max_length=500)
    propagation_principle: str | None = Field(default=None, max_length=500)
    common_issues: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("common_issues", "common_problems"),
        max_length=24,
    )
    safety_notes: list[str] = Field(default_factory=list, max_length=24)
    toxicity: ToxicityLevel = ToxicityLevel.UNKNOWN
    source_label: str = Field(default="curated_offline", max_length=80)

    @property
    def name(self) -> str:
        return self.canonical_name

    @property
    def light(self) -> str:
        return self.light_preference

    @field_validator(
        "canonical_name",
        "scientific_name",
        "light_preference",
        "soil_preference",
        "watering_principle",
        "fertilization_principle",
        "pruning_principle",
        "propagation_principle",
        "source_label",
    )
    @classmethod
    def _text_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_text(value, max_length=600)

    @field_validator("aliases", "common_issues", "safety_notes")
    @classmethod
    def _list_text_fields(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = _safe_text(value, max_length=240, allow_linebreaks=False)
            if text and text not in result:
                result.append(text)
        return result

    @field_validator("temperature_range_c")
    @classmethod
    def _temperature_range(cls, value: tuple[float, float] | None) -> tuple[float, float] | None:
        if value is None:
            return None
        low, high = value
        if low < -50 or high > 70 or low > high:
            raise ValueError("temperature range is invalid")
        return (float(low), float(high))


class ContainerInfo(FlowerModel):
    type: ContainerType = ContainerType.UNKNOWN
    diameter_cm: float | None = Field(default=None, ge=1, le=300)
    depth_cm: float | None = Field(default=None, ge=1, le=300)
    material: str | None = Field(default=None, max_length=80)
    drainage: bool | None = None

    @field_validator("material")
    @classmethod
    def _material_safe(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value, max_length=80, allow_linebreaks=False)


class GardenContext(FlowerModel):
    """Bounded plant/environment memory for one logical chat session."""

    session_id: UUID | None = None
    # A string is accepted for lightweight callers; production adapters generally
    # replace it with a resolved PlantProfile after knowledge lookup.
    plant: PlantProfile | str | None = None
    location: str | None = Field(default=None, max_length=160)
    climate: str | None = Field(default=None, max_length=160)
    light: LightLevel | str | None = None
    container: ContainerInfo | str | None = None
    season: str | None = Field(default=None, max_length=80)
    recent_observations: list[str] = Field(default_factory=list, max_length=8)
    recent_questions: list[str] = Field(default_factory=list, max_length=8)
    # A lifetime count is kept separately from the bounded transcript window.
    # ``turn_count`` is retained for backwards compatibility with the first
    # flower prototype; callers that need an accurate count must use this
    # field because old questions are evicted from ``recent_questions``.
    completed_user_turn_count: int = Field(default=0, ge=0, le=100_000)
    # Answers are retained only as a short, server-owned projection for
    # session-meta questions (for example, “你刚才回答了什么”).  They are not
    # provider evidence and must never be sent to the public context payload.
    recent_answers: list[str] = Field(default_factory=list, max_length=8)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    turn_count: int = Field(default=0, ge=0, le=100_000)

    _TEXT_FIELDS: ClassVar[tuple[str, ...]] = ("location", "climate", "season")

    @field_validator(*_TEXT_FIELDS)
    @classmethod
    def _context_text_safe(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value, max_length=160, allow_linebreaks=False)

    @field_validator("plant")
    @classmethod
    def _plant_safe(cls, value: PlantProfile | str | None) -> PlantProfile | str | None:
        if isinstance(value, str):
            return _safe_text(value, max_length=100, allow_linebreaks=False)
        return value

    @field_validator("container")
    @classmethod
    def _container_safe(cls, value: ContainerInfo | str | None) -> ContainerInfo | str | None:
        if isinstance(value, str):
            return _safe_text(value, max_length=120, allow_linebreaks=False)
        return value

    @field_validator("recent_observations", "recent_questions")
    @classmethod
    def _bounded_history(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = _safe_text(value, max_length=500)
            if text:
                cleaned.append(text)
        return cleaned[-8:]

    @field_validator("recent_answers")
    @classmethod
    def _bounded_answers(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            # Answers are usually Markdown and may contain line breaks.  Keep
            # a bounded projection rather than rejecting an otherwise valid
            # turn merely because the rendered answer is long.
            text = str(value or "").strip()
            if _CONTROL_RE.search(text):
                raise ValueError("text contains control characters")
            if text:
                cleaned.append(text[:2000])
        return cleaned[-8:]

    @field_validator("updated_at")
    @classmethod
    def _aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must be timezone aware")
        return value.astimezone(UTC)

    @property
    def plant_name(self) -> str | None:
        if isinstance(self.plant, PlantProfile):
            return self.plant.canonical_name
        return self.plant

    @property
    def plant_profile(self) -> PlantProfile | None:
        return self.plant if isinstance(self.plant, PlantProfile) else None

    def summary(self) -> str:
        """Return a short, provider-neutral context hint for a model prompt."""

        parts: list[str] = []
        if self.plant_name:
            parts.append(f"植物：{self.plant_name}")
        if self.location:
            parts.append(f"地点：{self.location}")
        if self.climate:
            parts.append(f"气候：{self.climate}")
        if self.light:
            light = self.light.value if isinstance(self.light, LightLevel) else self.light
            parts.append(f"光照：{light}")
        if self.container:
            container = (
                self.container.type.value
                if isinstance(self.container, ContainerInfo)
                else self.container
            )
            parts.append(f"容器：{container}")
        if self.season:
            parts.append(f"季节：{self.season}")
        if self.recent_observations:
            parts.append(f"最近观察：{self.recent_observations[-1]}")
        return "；".join(parts)[:1000] or "暂无已确认的种植上下文"

    def remember(
        self,
        *,
        observation: str | None = None,
        question: str | None = None,
        answer: str | None = None,
        completed: bool = True,
        now: datetime | None = None,
        **updates: Any,
    ) -> GardenContext:
        """Return an immutable-style bounded update for the next turn."""

        observations = [*self.recent_observations]
        questions = [*self.recent_questions]
        answers = [*self.recent_answers]
        if observation:
            observations.append(_safe_text(observation, max_length=500))
        if question:
            questions.append(_safe_text(question, max_length=500))
        if answer:
            # Do not fail a completed turn because a model produced a long
            # Markdown answer.  The session-meta projection only needs a
            # concise bounded copy.
            answer_text = str(answer).strip()
            if _CONTROL_RE.search(answer_text):
                raise ValueError("text contains control characters")
            answers.append(answer_text[:2000])
        completed_increment = 1 if completed else 0
        updates.update(
            recent_observations=observations[-8:],
            recent_questions=questions[-8:],
            recent_answers=answers[-8:],
            completed_user_turn_count=min(
                self.completed_user_turn_count + completed_increment,
                100_000,
            ),
            turn_count=min(self.turn_count + completed_increment, 100_000),
            updated_at=now or datetime.now(UTC),
        )
        return self.model_copy(update=updates)


class CareAction(FlowerModel):
    category: str = Field(min_length=1, max_length=60)
    action: str = Field(min_length=1, max_length=500)
    frequency: str | None = Field(default=None, max_length=160)
    trigger: str | None = Field(default=None, max_length=300)
    priority: int = Field(default=2, ge=1, le=3)
    rationale: str | None = Field(default=None, max_length=400)
    safety_note: str | None = Field(default=None, max_length=400)

    @field_validator("category", "action", "frequency", "trigger", "rationale", "safety_note")
    @classmethod
    def _action_text_safe(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value, max_length=500)


class CarePlan(FlowerModel):
    plant_name: str = Field(min_length=1, max_length=100)
    actions: list[CareAction] = Field(default_factory=list, max_length=24)
    conditions: list[str] = Field(default_factory=list, max_length=16)
    uncertainty: str | None = Field(default=None, max_length=500)
    professional_review: str | None = Field(default=None, max_length=500)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("plant_name", "uncertainty", "professional_review")
    @classmethod
    def _plan_text_safe(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value, max_length=500)

    @field_validator("conditions")
    @classmethod
    def _conditions_safe(cls, values: list[str]) -> list[str]:
        return [_safe_text(value, max_length=200) for value in values if str(value).strip()]

    @field_validator("generated_at")
    @classmethod
    def _plan_timestamp_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone aware")
        return value.astimezone(UTC)


class SearchObservation(FlowerModel):
    """A cleaned, non-authoritative observation from a public search."""

    title: str = Field(min_length=1, max_length=500)
    snippet: str | None = Field(default=None, max_length=1200)
    source_label: str | None = Field(default=None, max_length=120)
    source_url: str | None = Field(default=None, max_length=1000)
    query: str | None = Field(default=None, max_length=300)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    relevance: float = Field(default=0.0, ge=0, le=1)
    verification: VerificationLevel = VerificationLevel.UNVERIFIED
    freshness: str = Field(default="unknown", max_length=40)

    @field_validator("title", "snippet", "source_label", "query")
    @classmethod
    def _external_text_clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = clean_external_text(value)
        if not cleaned:
            raise ValueError("external text is empty after cleaning")
        return cleaned

    @field_validator("source_url")
    @classmethod
    def _url_private_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Keep the URL only as internal provenance.  Reject control characters;
        # public renderers must omit this field entirely.
        return _safe_text(value, max_length=1000, allow_linebreaks=False)

    @field_validator("retrieved_at")
    @classmethod
    def _observation_timestamp_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone aware")
        return value.astimezone(UTC)


class SafetyNotice(FlowerModel):
    """Pre-retrieval short-circuit response for dangerous gardening requests."""

    category: SafetyCategory
    blocked: bool = True
    message: str = Field(min_length=1, max_length=700)
    immediate_actions: list[str] = Field(default_factory=list, max_length=8)
    do_not_do: list[str] = Field(default_factory=list, max_length=8)
    seek_help: str | None = Field(default=None, max_length=500)
    confidence: float = Field(default=0.99, ge=0, le=1)

    @field_validator("message", "seek_help")
    @classmethod
    def _notice_text_safe(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value, max_length=700)

    @field_validator("immediate_actions", "do_not_do")
    @classmethod
    def _notice_lists_safe(cls, values: list[str]) -> list[str]:
        return [_safe_text(value, max_length=300) for value in values if str(value).strip()]

    @model_validator(mode="after")
    def _blocked_invariant(self) -> SafetyNotice:
        if not self.blocked:
            raise ValueError("a safety notice must be a short-circuit")
        return self


class PlantMatch(FlowerModel):
    """Knowledge-base lookup result with explicit ambiguity."""

    query: str = Field(min_length=0, max_length=200)
    profile: PlantProfile | None = None
    candidates: list[PlantProfile] = Field(default_factory=list, max_length=8)
    matched_alias: str | None = Field(default=None, max_length=100)
    confidence: float = Field(default=0.0, ge=0, le=1)
    ambiguous: bool = False
    suggestions: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _match_shape(self) -> PlantMatch:
        if self.profile is not None and self.candidates and self.profile not in self.candidates:
            self.candidates = [self.profile, *self.candidates][:8]
        if self.ambiguous and self.profile is not None:
            raise ValueError("ambiguous match cannot select one profile")
        return self


class FlowerParseResult(FlowerModel):
    """Deterministic parse output consumed by either template or Hermes paths."""

    intent_name: FlowerIntentName
    original_text: str = Field(min_length=1, max_length=2000)
    plant_match: PlantMatch | None = None
    resolved_plant: PlantProfile | None = None
    used_context_reference: bool = False
    location: str | None = None
    climate: str | None = None
    light: LightLevel | str | None = None
    container: ContainerInfo | str | None = None
    season: str | None = None
    observations: list[str] = Field(default_factory=list, max_length=8)
    missing_slots: list[str] = Field(default_factory=list, max_length=8)
    clarification: str | None = Field(default=None, max_length=500)
    confidence: float = Field(default=0.0, ge=0, le=1)
    safety_notice: SafetyNotice | None = None

    @field_validator("original_text")
    @classmethod
    def _query_safe(cls, value: str) -> str:
        return _safe_text(value, max_length=2000)

    @field_validator("location", "climate", "season", "clarification")
    @classmethod
    def _parse_text_safe(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value, max_length=500)

    @field_validator("observations")
    @classmethod
    def _observations_safe(cls, values: list[str]) -> list[str]:
        return [_safe_text(value, max_length=200) for value in values]


class FlowerTurn(FlowerModel):
    """Result of one local-domain turn, useful for API adapters and tests."""

    parse: FlowerParseResult
    context: GardenContext
    answer_markdown: str = Field(min_length=1, max_length=20_000)
    safety_notice: SafetyNotice | None = None
    observations: list[SearchObservation] = Field(default_factory=list, max_length=8)

    @field_validator("answer_markdown")
    @classmethod
    def _answer_safe(cls, value: str) -> str:
        return _safe_text(value, max_length=20_000)


def similarity(a: str, b: str) -> float:
    """Small shared similarity helper for alias matching and deterministic tests."""

    left = "".join(ch for ch in unicodedata.normalize("NFKC", a).casefold() if ch.isalnum())
    right = "".join(ch for ch in unicodedata.normalize("NFKC", b).casefold() if ch.isalnum())
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


__all__ = [
    "CareAction",
    "CarePlan",
    "ContainerInfo",
    "ContainerType",
    "FlowerIntentName",
    "FlowerParseResult",
    "FlowerTurn",
    "GardenContext",
    "LightLevel",
    "PlantMatch",
    "PlantProfile",
    "SafetyCategory",
    "SafetyNotice",
    "SearchObservation",
    "ToxicityLevel",
    "VerificationLevel",
    "clean_external_text",
    "similarity",
]
