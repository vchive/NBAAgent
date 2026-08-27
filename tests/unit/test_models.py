"""Contract-level checks for canonical domain models."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.api.src.domain.models import (
    AnswerBlock,
    AnswerBlockType,
    Category,
    DateRange,
    EntityKind,
    EntityRef,
    ErrorCode,
    EvidenceState,
    FactAssertion,
    Game,
    GameStatus,
    HermesLiteMode,
    HermesStatus,
    PlayByPlayBundle,
    PlayEvent,
    PlayEventType,
    QueryIntent,
    QueryMode,
    QueryOutcome,
    QueryPhase,
    QueryRecord,
    SafetyCategory,
    SeasonLabel,
    TimeWindow,
    TimeWindowScope,
    VerificationState,
)


def team(identifier: str, name: str | None = None) -> EntityRef:
    return EntityRef(
        kind=EntityKind.TEAM,
        canonical_id=identifier,
        display_name=name or identifier.upper(),
    )


def season() -> SeasonLabel:
    return SeasonLabel(start_year=2025, end_year=2026, label="2025-26")


def test_season_label_requires_cross_year_consistency() -> None:
    assert season().label == "2025-26"
    with pytest.raises(ValidationError):
        SeasonLabel(start_year=2025, end_year=2027, label="2025-26")
    with pytest.raises(ValidationError):
        SeasonLabel(start_year=2025, end_year=2026, label="2024-25")


def test_date_range_is_aware_and_half_open() -> None:
    start = datetime(2026, 6, 11, 16, tzinfo=UTC)
    end = datetime(2026, 6, 12, 16, tzinfo=UTC)
    assert DateRange(start_inclusive=start, end_exclusive=end)
    with pytest.raises(ValidationError):
        DateRange(start_inclusive=end, end_exclusive=start)
    with pytest.raises(ValidationError):
        DateRange(
            start_inclusive=datetime(2026, 6, 11, 16),
            end_exclusive=end,
        )


def test_game_requires_distinct_team_references_and_nonnegative_scores() -> None:
    game = Game(
        game_id="g1",
        season=season(),
        start_utc=datetime(2026, 6, 12, tzinfo=UTC),
        home=team("bos"),
        away=team("okc"),
        status=GameStatus.FINAL,
        home_score=108,
        away_score=104,
    )
    assert game.home.kind is EntityKind.TEAM
    with pytest.raises(ValidationError):
        Game(
            game_id="g1",
            season=season(),
            start_utc=datetime(2026, 6, 12, tzinfo=UTC),
            home=team("bos"),
            away=team("bos"),
            status=GameStatus.FINAL,
        )
    with pytest.raises(ValidationError):
        Game(
            game_id="g1",
            season=season(),
            start_utc=datetime(2026, 6, 12, tzinfo=UTC),
            home=team("bos"),
            away=team("okc"),
            status=GameStatus.FINAL,
            home_score=-1,
        )


def test_time_window_and_answer_block_shapes() -> None:
    window = TimeWindow(
        start_seconds=Decimal("0"),
        end_seconds=Decimal("5"),
        scope=TimeWindowScope.GAME_END,
    )
    assert window.end_seconds == 5
    with pytest.raises(ValidationError):
        TimeWindow(start_seconds=6, end_seconds=5)
    block = AnswerBlock(
        type=AnswerBlockType.TABLE,
        columns=["球队", "胜场"],
        rows=[["BOS", 3]],
    )
    assert block.rows == [["BOS", 3]]
    with pytest.raises(ValidationError):
        AnswerBlock(type=AnswerBlockType.TABLE, columns=["球队", "胜场"], rows=[["BOS"]])


def test_pbp_bundle_preserves_nulls_and_validates_sequence() -> None:
    event = PlayEvent(
        event_id="p1",
        game_id="g1",
        sequence=1,
        provider_index=0,
        period=4,
        clock_seconds_remaining=Decimal("5"),
        event_type=PlayEventType.SHOT,
        points=None,
    )
    bundle = PlayByPlayBundle(game_id="g1", events=[event], sequence_valid=True)
    assert bundle.events[0].points is None
    with pytest.raises(ValidationError):
        PlayByPlayBundle(
            game_id="g1",
            events=[event.model_copy(update={"sequence": None})],
            sequence_valid=True,
        )
    with pytest.raises(ValidationError):
        PlayByPlayBundle(
            game_id="g1",
            events=[event, event.model_copy(update={"provider_index": 0, "event_id": "p2"})],
            sequence_valid=False,
        )


def test_fact_assertion_requires_evidence_when_verified() -> None:
    subject = team("bos")
    fact = FactAssertion(
        fact_id="f1",
        subject=subject,
        predicate="wins",
        value=3,
        evidence_ids=["e1"],
        verification=VerificationState.VERIFIED,
    )
    assert fact.value == 3
    with pytest.raises(ValidationError):
        FactAssertion(
            fact_id="f1",
            subject=subject,
            predicate="wins",
            value=3,
            verification=VerificationState.VERIFIED,
        )


def test_query_intent_category_mapping_is_explicit() -> None:
    query = QueryIntent(
        category=Category.A,
        intent_name="DATA",
        mode=QueryMode.OBJECTIVE,
        confidence=Decimal("0.95"),
    )
    assert query.intent_name.value == "DATA"
    with pytest.raises(ValidationError):
        QueryIntent(
            category=Category.A,
            intent_name="HISTORY",
            mode=QueryMode.OBJECTIVE,
            confidence=Decimal("0.95"),
        )


def _query_record(**overrides: object) -> QueryRecord:
    payload: dict[str, object] = {
        "request_id": uuid4(),
        "session_id": uuid4(),
        "raw_text_hash": "hash",
    }
    payload.update(overrides)
    return QueryRecord.model_validate(payload)


def test_query_record_requires_a_terminal_outcome() -> None:
    with pytest.raises(ValidationError):
        _query_record(phase=QueryPhase.COMPLETED)
    with pytest.raises(ValidationError):
        _query_record(outcome=QueryOutcome.COMPLETED)
    with pytest.raises(ValidationError):
        _query_record(phase=QueryPhase.FAILED, outcome=QueryOutcome.FAILED)

    failed = _query_record(
        phase=QueryPhase.FAILED,
        outcome=QueryOutcome.FAILED,
        error_code=ErrorCode.UPSTREAM_TIMEOUT,
    )
    assert failed.phase is QueryPhase.FAILED


def test_query_record_short_circuit_cannot_claim_provider_or_evidence() -> None:
    blocked = _query_record(
        phase=QueryPhase.COMPLETED,
        outcome=QueryOutcome.BLOCKED,
        safety_category=SafetyCategory.GAMBLING,
    )
    assert blocked.evidence_state is EvidenceState.NONE

    with pytest.raises(ValidationError):
        _query_record(
            phase=QueryPhase.COMPLETED,
            outcome=QueryOutcome.BLOCKED,
            provider_call_count=1,
            safety_category=SafetyCategory.GAMBLING,
        )
    with pytest.raises(ValidationError):
        _query_record(
            phase=QueryPhase.COMPLETED,
            outcome=QueryOutcome.BLOCKED,
            evidence_state=EvidenceState.VERIFIED,
            safety_category=SafetyCategory.GAMBLING,
        )
    with pytest.raises(ValidationError):
        _query_record(
            phase=QueryPhase.COMPLETED,
            outcome=QueryOutcome.NEEDS_CLARIFICATION,
            evidence_state=EvidenceState.PARTIAL,
        )


def test_query_record_validates_hermes_status_values() -> None:
    record = _query_record(
        hermes_mode=HermesLiteMode.SIDECAR,
        hermes_status="ok",
    )
    assert record.hermes_status is HermesStatus.OK

    with pytest.raises(ValidationError):
        _query_record(
            hermes_mode=HermesLiteMode.SIDECAR,
            hermes_status="DEGRADED",
        )
    with pytest.raises(ValidationError):
        _query_record(
            hermes_mode=HermesLiteMode.OFF,
            hermes_status=HermesStatus.OK,
        )
