from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from apps.api.src.domain.derivation import derive_pbp, derive_series
from apps.api.src.domain.models import (
    Claim,
    EntityKind,
    EntityRef,
    FactAssertion,
    FactBundle,
    Game,
    GameStatus,
    PlayByPlayBundle,
    PlayEvent,
    PlayEventType,
    SeasonLabel,
    ShotType,
    TimeWindow,
    TimeWindowScope,
    VerificationState,
)
from apps.api.src.domain.verifier import verify_premise


def _team(identifier: str) -> EntityRef:
    return EntityRef(kind=EntityKind.TEAM, canonical_id=identifier, display_name=identifier.upper())


def _game(
    identifier: str,
    home: str,
    away: str,
    hs: int | None,
    ass: int | None,
    day: int,
    status: GameStatus = GameStatus.FINAL,
) -> Game:
    return Game(
        game_id=identifier,
        season=SeasonLabel(start_year=2025, end_year=2026, label="2025-26"),
        start_utc=datetime(2026, 6, day, tzinfo=UTC),
        home=_team(home),
        away=_team(away),
        status=status,
        home_score=hs,
        away_score=ass,
        series_id="series-test",
        series_game_number=day,
    )


def test_series_skips_unfinished_ties_and_marks_partial() -> None:
    result = derive_series(
        [
            _game("g1", "bos", "okc", 100, 90, 1),
            _game("g2", "okc", "bos", None, None, 2, GameStatus.SCHEDULED),
            _game("g3", "bos", "okc", 90, 90, 3),
            _game("g1", "bos", "okc", 100, 90, 1),
        ],
        series_id="series-test",
    )
    values = {
        fact.subject.canonical_id: fact.value
        for fact in result.facts
        if fact.predicate == "series_wins"
    }
    assert values == {"bos": 1, "okc": 0}
    assert result.partial is True


def test_pbp_duplicate_sequence_is_partial_but_window_order_is_stable() -> None:
    events = [
        PlayEvent(
            event_id="late",
            game_id="g",
            sequence=2,
            provider_index=1,
            period=5,
            clock_seconds_remaining=Decimal("1"),
            event_type=PlayEventType.SHOT,
            shot_type=ShotType.TWO_POINT,
            points=2,
        ),
        PlayEvent(
            event_id="early",
            game_id="g",
            sequence=1,
            provider_index=0,
            period=5,
            clock_seconds_remaining=Decimal("3"),
            event_type=PlayEventType.SHOT,
            shot_type=ShotType.THREE_POINT,
            points=3,
        ),
        PlayEvent(
            event_id="duplicate",
            game_id="g",
            sequence=2,
            provider_index=2,
            period=5,
            clock_seconds_remaining=Decimal("0"),
            event_type=PlayEventType.OTHER,
        ),
    ]
    result = derive_pbp(
        PlayByPlayBundle(game_id="g", events=events, sequence_valid=False),
        TimeWindow(start_seconds=0, end_seconds=5, scope=TimeWindowScope.GAME_END),
    )
    assert [event.event_id for event in result.events] == ["early", "late", "duplicate"]
    assert result.partial is True


def test_premise_correction_distinguishes_wrong_and_unverified_claims() -> None:
    game = _team("bos")
    fact = FactAssertion(
        fact_id="f",
        subject=game,
        predicate="score",
        value=108,
        evidence_ids=["e"],
        verification=VerificationState.VERIFIED,
    )
    missing = FactAssertion(
        fact_id="m",
        subject=game,
        predicate="rebounds",
        value=None,
        evidence_ids=[],
        verification=VerificationState.UNVERIFIED,
    )
    facts = FactBundle(facts=[fact, missing], evidence_state="PARTIAL")
    wrong = verify_premise([Claim(subject=game, predicate="score", claimed_value=104)], facts)
    unknown = verify_premise([Claim(subject=game, predicate="rebounds", claimed_value=9)], facts)
    assert wrong[0].status.value == "CORRECTED" and wrong[0].verified_value == 108
    assert unknown[0].status.value == "UNVERIFIED" and unknown[0].verified_value is None
