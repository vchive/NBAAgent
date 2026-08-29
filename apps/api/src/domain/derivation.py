"""Pure deterministic NBA aggregations and play-by-play selection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .models import (
    EntityKind,
    EntityRef,
    EvidenceState,
    FactAssertion,
    FactBundle,
    Game,
    GameStatus,
    PlayByPlayBundle,
    PlayEvent,
    SeriesRef,
    ShotType,
    VerificationState,
)
from .time_policy import select_pbp_window


@dataclass(slots=True)
class DerivedResult:
    facts: list[FactAssertion] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    partial: bool = False
    events: list[PlayEvent] = field(default_factory=list)
    # A date-scoped schedule can contain more than one game.  Keep the
    # canonical rows alongside derived facts so the renderer can present the
    # complete slate without overloading the single-game ``game`` slot used
    # by existing answer paths.  The field is optional/backwards compatible;
    # derivation helpers that do not project a schedule leave it empty.
    games: list[Game] = field(default_factory=list)

    @property
    def evidence_state(self) -> EvidenceState:
        if not self.facts:
            return EvidenceState.NONE
        return EvidenceState.PARTIAL if self.partial else EvidenceState.VERIFIED

    def as_fact_bundle(self) -> FactBundle:
        return FactBundle(
            facts=self.facts, missing=self.missing, evidence_state=self.evidence_state
        )


def _ref(kind: EntityKind, canonical_id: str, name: str) -> EntityRef:
    return EntityRef(kind=kind, canonical_id=canonical_id, display_name=name)


def _derived_fact(
    fact_id: str,
    subject: EntityRef,
    predicate: str,
    value: Any,
    source_ids: Iterable[str],
    *,
    unit: str | None = None,
    partial: bool = False,
) -> FactAssertion:
    return FactAssertion(
        fact_id=fact_id,
        subject=subject,
        predicate=predicate,
        value=value,
        unit=unit,
        evidence_ids=list(source_ids) or ["derived:fixture"],
        derived_from_fact_ids=list(source_ids),
        verification=VerificationState.PARTIAL if partial else VerificationState.VERIFIED,
    )


def derive_game_totals(game: Game, source_fact_ids: Iterable[str] = ()) -> DerivedResult:
    ids = list(source_fact_ids) or [f"{game.game_id}:home_score", f"{game.game_id}:away_score"]
    result = DerivedResult()
    if game.home_score is None or game.away_score is None:
        result.missing.append("终场比分")
        result.partial = True
        return result
    game_ref = _ref(EntityKind.GAME, game.game_id, "这场比赛")
    result.facts.append(
        _derived_fact(
            f"{game.game_id}:margin",
            game_ref,
            "margin",
            abs(game.home_score - game.away_score),
            ids,
            unit="分",
        )
    )
    result.facts.append(
        _derived_fact(
            f"{game.game_id}:total_points",
            game_ref,
            "total_points",
            game.home_score + game.away_score,
            ids,
            unit="分",
        )
    )
    return result


def derive_leaders(bundle: Any) -> DerivedResult:
    result = DerivedResult()
    leaders = list(getattr(bundle, "leaders", []) or [])
    if not leaders:
        result.missing.append("得分榜")
        return result
    # Prefer points; ties are kept rather than arbitrarily selecting a player.
    values = [
        (line, line.metrics.get("points"))
        for line in leaders
        if line.metrics.get("points") is not None
    ]
    if not values:
        result.missing.append("得分")
        result.partial = True
        return result
    top = max(value for _, value in values)
    tied = [(line, value) for line, value in values if value == top]
    for line, value in tied:
        result.facts.append(
            _derived_fact(
                f"leader:{line.game_id or 'scope'}:{line.subject.canonical_id}:points",
                line.subject,
                "points_leader",
                value,
                line.evidence_ids,
                unit="分",
            )
        )
    if len(tied) > 1:
        result.partial = True
    return result


def derive_series(
    games: Iterable[Game],
    *,
    series_id: str | None = None,
    series: SeriesRef | None = None,
) -> DerivedResult:
    """Aggregate a series by counting valid final games exactly once."""

    values = list(games)
    if series_id:
        values = [game for game in values if game.series_id == series_id]
    dedup: dict[str, Game] = {}
    partial = False
    for game in values:
        if game.game_id in dedup:
            continue
        dedup[game.game_id] = game
    wins: dict[str, int] = {}
    valid: list[Game] = []
    for game in sorted(dedup.values(), key=lambda item: item.start_utc):
        if (
            game.status is not GameStatus.FINAL
            or game.home_score is None
            or game.away_score is None
            or game.home_score == game.away_score
        ):
            partial = True
            continue
        winner = game.home if game.home_score > game.away_score else game.away
        wins[winner.canonical_id] = wins.get(winner.canonical_id, 0) + 1
        valid.append(game)
    if not valid:
        return DerivedResult(missing=["系列赛比赛记录"], partial=True)
    participants = (series.home, series.away) if series else (valid[0].home, valid[0].away)
    result = DerivedResult(partial=partial)
    source_ids = [f"game:{game.game_id}" for game in valid]
    for participant in participants:
        if participant is None:
            continue
        result.facts.append(
            _derived_fact(
                f"series:{series_id or 'unknown'}:{participant.canonical_id}:wins",
                participant,
                "series_wins",
                wins.get(participant.canonical_id, 0),
                source_ids,
                unit="场",
                partial=partial,
            )
        )
    result.facts.append(
        _derived_fact(
            f"series:{series_id or 'unknown'}:games",
            _ref(EntityKind.SERIES, series_id or "unknown", "系列赛"),
            "games_counted",
            len(valid),
            source_ids,
            unit="场",
            partial=partial,
        )
    )
    return result


def derive_pbp(
    bundle: PlayByPlayBundle, window: Any, *, period: int | None = None
) -> DerivedResult:
    events = select_pbp_window(bundle, window, period=period)
    result = DerivedResult(events=events, partial=not bundle.sequence_valid)
    # The count is itself a deterministic, traceable fact so a renderer can
    # mention it without triggering the output guard's untraceable-number rule.
    game_ref = _ref(EntityKind.GAME, bundle.game_id, "这场比赛")
    result.facts.append(
        _derived_fact(
            f"pbp:{bundle.game_id}:events_count",
            game_ref,
            "events_count",
            len(events),
            [f"pbp:{bundle.game_id}"],
            unit="回合",
            partial=not bundle.sequence_valid,
        )
    )
    for event in events:
        # A PBP event without a participant *and* without a score carries no
        # user-facing fact.  A terminal/administrative record can legitimately
        # have no shooter or points while still carrying the authoritative
        # score-after state; retain that state so the output guard can trace a
        # rendered final score instead of treating it as an invented number.
        if (
            event.points is None
            and event.shooter is None
            and event.home_score_after is None
            and event.away_score_after is None
        ):
            continue
        subject = event.shooter or _ref(EntityKind.GAME, bundle.game_id, "这场比赛")
        value = {
            "period": event.period,
            "clock_seconds_remaining": float(event.clock_seconds_remaining),
            "points": event.points,
            "shooter": event.shooter.display_name if event.shooter else None,
            "assister": event.assister.display_name if event.assister else None,
            "shot_type": event.shot_type.value,
            "home_score_after": event.home_score_after,
            "away_score_after": event.away_score_after,
        }
        result.facts.append(
            _derived_fact(
                f"pbp:{event.event_id}",
                subject,
                "play",
                value,
                [f"pbp:{bundle.game_id}"],
                unit="回合",
                partial=(
                    event.points is None
                    or event.shooter is None
                    or event.shot_type in {ShotType.UNKNOWN, ShotType.NONE}
                ),
            )
        )

    # Expose a compact, traceable projection of the *final selected record*
    # for fact-check questions.  It is tempting to skip a terminal
    # whistle/administrative row and use the previous identifiable shot, but
    # that silently turns an unknown final possession into a false claim (for
    # example, presenting a five-second free throw as the decisive last shot).
    # When the final record omits a shooter/type, leave the corresponding fact
    # absent so ``verify_premise`` returns UNVERIFIED and the renderer can say
    # that the final attempt is not identifiable.
    last_event = events[-1] if events else None
    # The newest score-bearing event is a separate concept from the newest
    # identifiable shot.  Providers commonly append a terminal whistle row
    # with ``points=null`` and the final score; using the shot row for both
    # concepts would answer “事件后比分” with a stale intermediate score.
    last_score_event = next(
        (
            event
            for event in reversed(events)
            if event.home_score_after is not None or event.away_score_after is not None
        ),
        None,
    )
    source = [f"pbp:{bundle.game_id}"]
    if last_event is not None:
        if last_event.shooter is not None:
            result.facts.append(
                _derived_fact(
                    f"pbp:{bundle.game_id}:last_shooter",
                    game_ref,
                    "last_shooter",
                    last_event.shooter.display_name,
                    source,
                    unit="球员",
                    partial=False,
                )
            )
        if last_event.assister is not None:
            result.facts.append(
                _derived_fact(
                    f"pbp:{bundle.game_id}:last_assister",
                    game_ref,
                    "last_assister",
                    last_event.assister.display_name,
                    source,
                    unit="球员",
                    partial=False,
                )
            )
        if last_event.shot_type not in {ShotType.UNKNOWN, ShotType.NONE}:
            result.facts.append(
                _derived_fact(
                    f"pbp:{bundle.game_id}:last_shot_type",
                    game_ref,
                    "last_shot_type",
                    last_event.shot_type.value,
                    source,
                    unit="类型",
                    partial=False,
                )
            )
    if last_score_event is not None:
        result.facts.append(
            _derived_fact(
                f"pbp:{bundle.game_id}:last_score_after",
                game_ref,
                "last_score_after",
                {
                    "home": last_score_event.home_score_after,
                    "away": last_score_event.away_score_after,
                },
                source,
                unit="比分",
                partial=(
                    last_score_event.home_score_after is None
                    or last_score_event.away_score_after is None
                ),
            )
        )
    return result


def derive_last_seconds(
    bundle: PlayByPlayBundle, window: Any, *, period: int | None = None
) -> DerivedResult:
    return derive_pbp(bundle, window, period=period)


def derive_recent_streak(games: Iterable[Game], team_id: str, n: int = 5) -> DerivedResult:
    if n <= 0:
        return DerivedResult(missing=["有效场次数"])
    values = [
        game
        for game in games
        if game.status is GameStatus.FINAL
        and (game.home.canonical_id == team_id or game.away.canonical_id == team_id)
    ]
    values.sort(key=lambda game: game.start_utc, reverse=True)
    values = values[:n]
    if not values:
        return DerivedResult(missing=["球队比赛记录"])
    # A score of zero is a valid result.  Only an explicit ``None`` means the
    # provider omitted a score; never coerce it to ``-1`` or count a missing
    # game as a loss.
    complete = [
        game for game in values if game.home_score is not None and game.away_score is not None
    ]
    team = next(game.home if game.home.canonical_id == team_id else game.away for game in values)
    if not complete:
        return DerivedResult(missing=["近期比赛比分"], partial=True)
    wins = sum(
        1
        for game in complete
        if (game.home.canonical_id == team_id and game.home_score > game.away_score)
        or (game.away.canonical_id == team_id and game.away_score > game.home_score)
    )
    partial = len(values) < n or len(complete) < len(values)
    result = DerivedResult(partial=partial)
    if len(complete) < len(values):
        result.missing.append("近期比赛比分")
    result.facts.append(
        _derived_fact(
            f"streak:{team_id}:{len(complete)}",
            team,
            "recent_record",
            {
                "wins": wins,
                "losses": len(complete) - wins,
                "games": len(complete),
            },
            [f"game:{game.game_id}" for game in complete],
            unit="场",
            partial=result.partial,
        )
    )
    return result


# Friendly aliases used by tests and application code.
derive_series_aggregate = derive_series
aggregate_series = derive_series
select_key_plays = derive_pbp


__all__ = [
    "DerivedResult",
    "aggregate_series",
    "derive_game_totals",
    "derive_last_seconds",
    "derive_leaders",
    "derive_pbp",
    "derive_recent_streak",
    "derive_series",
    "derive_series_aggregate",
    "select_key_plays",
]
