from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from apps.api.src.domain.derivation import derive_pbp, derive_series
from apps.api.src.domain.models import (
    EntityKind,
    EntityRef,
    Game,
    GameStatus,
    PlayByPlayBundle,
    PlayEvent,
    PlayEventType,
    SeasonLabel,
    TimeWindow,
    TimeWindowScope,
)


def _team(identifier: str, name: str) -> EntityRef:
    return EntityRef(kind=EntityKind.TEAM, canonical_id=identifier, display_name=name)


def _season() -> SeasonLabel:
    return SeasonLabel(start_year=2025, end_year=2026, label="2025-26")


def _game(identifier: str, home: str, away: str, hs: int, ass: int, index: int) -> Game:
    return Game(
        game_id=identifier,
        season=_season(),
        start_utc=datetime(2026, 6, 1 + index, tzinfo=UTC),
        home=_team(home, home.upper()),
        away=_team(away, away.upper()),
        status=GameStatus.FINAL,
        home_score=hs,
        away_score=ass,
        series_id="s1",
        series_game_number=index,
    )


def test_series_counts_valid_final_games_once() -> None:
    result = derive_series(
        [
            _game("g1", "bos", "okc", 100, 90, 1),
            _game("g2", "okc", "bos", 105, 110, 2),
            _game("g2", "okc", "bos", 105, 110, 2),
        ],
        series_id="s1",
    )
    values = {
        fact.subject.canonical_id: fact.value
        for fact in result.facts
        if fact.predicate == "series_wins"
    }
    assert values == {"bos": 2, "okc": 0}


def test_pbp_window_includes_zero_and_five_seconds() -> None:
    events = [
        PlayEvent(
            event_id="five",
            game_id="g1",
            sequence=1,
            provider_index=0,
            period=4,
            clock_seconds_remaining=Decimal("5"),
            event_type=PlayEventType.SHOT,
        ),
        PlayEvent(
            event_id="zero",
            game_id="g1",
            sequence=2,
            provider_index=1,
            period=4,
            clock_seconds_remaining=Decimal("0"),
            event_type=PlayEventType.OTHER,
        ),
        PlayEvent(
            event_id="six",
            game_id="g1",
            sequence=3,
            provider_index=2,
            period=4,
            clock_seconds_remaining=Decimal("6"),
            event_type=PlayEventType.SHOT,
        ),
    ]
    result = derive_pbp(
        PlayByPlayBundle(game_id="g1", events=events, sequence_valid=True),
        TimeWindow(start_seconds=0, end_seconds=5, scope=TimeWindowScope.GAME_END),
    )
    assert [event.event_id for event in result.events] == ["five", "zero"]


def test_pbp_keeps_terminal_score_when_terminal_event_has_no_shooter() -> None:
    """A whistle/terminal row may carry the final score but no player fields."""

    events = [
        PlayEvent(
            event_id="shot",
            game_id="g1",
            sequence=1,
            provider_index=0,
            period=4,
            clock_seconds_remaining=Decimal("5"),
            event_type=PlayEventType.FREE_THROW,
            points=1,
            home_score_after=106,
            away_score_after=102,
        ),
        PlayEvent(
            event_id="terminal",
            game_id="g1",
            sequence=2,
            provider_index=1,
            period=4,
            clock_seconds_remaining=Decimal("0"),
            event_type=PlayEventType.OTHER,
            home_score_after=108,
            away_score_after=104,
        ),
    ]

    result = derive_pbp(
        PlayByPlayBundle(game_id="g1", events=events, sequence_valid=True),
        TimeWindow(start_seconds=0, end_seconds=5, scope=TimeWindowScope.GAME_END),
    )

    score_facts = [fact for fact in result.facts if fact.predicate == "last_score_after"]
    assert len(score_facts) == 1
    assert score_facts[0].value == {"home": 108, "away": 104}
