"""Deterministic fact verification and premise checking."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .models import (
    Claim,
    EntityKind,
    EntityRef,
    Evidence,
    EvidenceState,
    FactAssertion,
    FactBundle,
    Game,
    GameBundle,
    StatLine,
    VerificationState,
)


@dataclass(slots=True)
class VerificationResult:
    facts: FactBundle
    game: Game | None = None
    bundle: GameBundle | None = None
    evidence: list[Evidence] = field(default_factory=list)


def _fact(
    fact_id: str,
    subject: EntityRef,
    predicate: str,
    value: Any,
    evidence_ids: Iterable[str],
    *,
    verification: VerificationState = VerificationState.VERIFIED,
    unit: str | None = None,
    derived_from: Iterable[str] = (),
) -> FactAssertion:
    ids = list(dict.fromkeys(str(item) for item in evidence_ids if item))
    state = verification
    if state in {VerificationState.VERIFIED, VerificationState.PARTIAL} and not ids:
        state = VerificationState.UNVERIFIED
    return FactAssertion(
        fact_id=fact_id,
        subject=subject,
        predicate=predicate,
        value=value,
        unit=unit,
        evidence_ids=ids,
        derived_from_fact_ids=list(derived_from),
        verification=state,
    )


def verify_game(game: Game, evidence_ids: Iterable[str] = ()) -> FactBundle:
    """Turn a canonical game into independently traceable assertions."""

    ids = list(evidence_ids) or [f"game:{game.game_id}"]
    facts: list[FactAssertion] = [
        _fact(
            f"{game.game_id}:start",
            EntityRef(kind=EntityKind.GAME, canonical_id=game.game_id, display_name="这场比赛"),
            "start_utc",
            game.start_utc.isoformat(),
            ids,
        ),
        _fact(
            f"{game.game_id}:status",
            EntityRef(kind=EntityKind.GAME, canonical_id=game.game_id, display_name="这场比赛"),
            "status",
            game.status.value,
            ids,
        ),
    ]
    game_ref = EntityRef(kind=EntityKind.GAME, canonical_id=game.game_id, display_name="这场比赛")
    if game.home_score is not None:
        facts.append(
            _fact(f"{game.game_id}:home_score", game.home, "score", game.home_score, ids, unit="分")
        )
    else:
        facts.append(
            _fact(
                f"{game.game_id}:home_score",
                game.home,
                "score",
                None,
                ids,
                verification=VerificationState.UNVERIFIED,
                unit="分",
            )
        )
    if game.away_score is not None:
        facts.append(
            _fact(f"{game.game_id}:away_score", game.away, "score", game.away_score, ids, unit="分")
        )
    else:
        facts.append(
            _fact(
                f"{game.game_id}:away_score",
                game.away,
                "score",
                None,
                ids,
                verification=VerificationState.UNVERIFIED,
                unit="分",
            )
        )
    if (
        game.home_score is not None
        and game.away_score is not None
        and game.home_score != game.away_score
    ):
        winner = game.home if game.home_score > game.away_score else game.away
        facts.append(_fact(f"{game.game_id}:winner", game_ref, "winner", winner.display_name, ids))
    if game.venue is not None:
        facts.append(
            _fact(
                f"{game.game_id}:venue",
                game_ref,
                "venue",
                {
                    "name": game.venue.name,
                    "city": game.venue.city,
                    "state": game.venue.state,
                    "country": game.venue.country,
                },
                ids,
            )
        )
    if game.duration_seconds is not None:
        hours, remainder = divmod(game.duration_seconds, 3600)
        minutes = remainder // 60
        duration_text = (
            f"{hours}小时{minutes}分钟" if hours else f"{minutes}分钟"
        )
        facts.append(
            _fact(
                f"{game.game_id}:duration",
                game_ref,
                "game_duration",
                duration_text,
                ids,
            )
        )
    if game.attendance is not None:
        facts.append(
            _fact(
                f"{game.game_id}:attendance",
                game_ref,
                "attendance",
                game.attendance,
                ids,
                unit="人",
            )
        )
    state = (
        EvidenceState.VERIFIED
        if all(item.verification is VerificationState.VERIFIED for item in facts)
        else EvidenceState.PARTIAL
    )
    return FactBundle(facts=facts, evidence_state=state)


def verify_stat_lines(lines: Iterable[StatLine], evidence_ids: Iterable[str] = ()) -> FactBundle:
    facts: list[FactAssertion] = []
    fallback = list(evidence_ids)
    for line in lines:
        ids = list(line.evidence_ids) or fallback
        for metric, value in line.metrics.items():
            state = (
                VerificationState.VERIFIED
                if value is not None and ids
                else VerificationState.UNVERIFIED
            )
            facts.append(
                _fact(
                    (
                        f"stat:{line.subject.canonical_id}:{line.scope.value.lower()}"
                        f":{metric}:{line.game_id or line.season or 'na'}"
                    ),
                    line.subject,
                    metric,
                    value,
                    ids,
                    verification=state,
                )
            )
    if not facts:
        return FactBundle(facts=[], missing=["统计数据"], evidence_state=EvidenceState.NONE)
    state = (
        EvidenceState.VERIFIED
        if all(item.verification is VerificationState.VERIFIED for item in facts)
        else EvidenceState.PARTIAL
    )
    return FactBundle(facts=facts, evidence_state=state)


def verify_bundle(bundle: GameBundle, evidence_ids: Iterable[str] = ()) -> VerificationResult:
    ids = list(evidence_ids) or [f"summary:{bundle.game.game_id}"]
    game_facts = verify_game(bundle.game, ids)
    # Providers commonly expose ``leaders`` as a ranked subset of the full
    # ``stat_lines`` collection. Expanding both lists verbatim duplicates
    # every metric and can overflow the bounded FactBundle on a normal
    # 30-player box score. Merge by canonical stat identity and retain the
    # more complete metric projection for each player/game/scope tuple.
    merged_lines: dict[tuple[str, str | None, str, str | None], StatLine] = {}
    for line in [*bundle.stat_lines, *bundle.leaders]:
        key = (
            line.subject.canonical_id,
            line.game_id,
            line.scope.value,
            line.season.label if line.season is not None else None,
        )
        previous = merged_lines.get(key)
        if previous is None:
            merged_lines[key] = line
            continue
        metrics = dict(previous.metrics)
        metrics.update(
            {name: value for name, value in line.metrics.items() if value is not None}
        )
        evidence = list(dict.fromkeys([*previous.evidence_ids, *line.evidence_ids]))
        merged_lines[key] = previous.model_copy(
            update={"metrics": metrics, "evidence_ids": evidence}
        )
    stat_facts = verify_stat_lines(merged_lines.values(), ids)
    merged = FactBundle(
        facts=[*game_facts.facts, *stat_facts.facts],
        missing=[*game_facts.missing, *stat_facts.missing],
        evidence_state=(
            EvidenceState.VERIFIED
            if game_facts.evidence_state is EvidenceState.VERIFIED
            and stat_facts.evidence_state in {EvidenceState.VERIFIED, EvidenceState.NONE}
            else EvidenceState.PARTIAL
        ),
    )
    return VerificationResult(facts=merged, game=bundle.game, bundle=bundle)


def _normalise_predicate(predicate: str) -> str:
    value = predicate.lower().strip()
    aliases = {
        "获胜": "winner",
        "赢": "winner",
        "赢家": "winner",
        "比分": "score",
        "得分": "points",
        "shot_type": "last_shot_type",
        "出手者": "last_shooter",
        "投篮者": "last_shooter",
        "助攻者": "last_assister",
    }
    return aliases.get(value, value)


def verify_premise(claims: Iterable[Claim], facts: FactBundle) -> list[Any]:
    """Return canonical ``Correction`` objects for claims that can be checked.

    An absent fact deliberately produces ``UNVERIFIED`` rather than asserting the
    user's premise is wrong.
    """

    from .models import Correction, CorrectionStatus

    corrections: list[Correction] = []
    for claim in claims:
        predicate = _normalise_predicate(claim.predicate)
        candidates = [
            item
            for item in facts.facts
            if item.subject.canonical_id == claim.subject.canonical_id
            and _normalise_predicate(item.predicate) == predicate
        ]
        if not candidates:
            # For winner claims the subject may be the game entity.
            candidates = [
                item for item in facts.facts if _normalise_predicate(item.predicate) == predicate
            ]
        verified = next(
            (item for item in candidates if item.verification is VerificationState.VERIFIED), None
        )
        if verified is None:
            corrections.append(
                Correction(claim=claim, verified_value=None, status=CorrectionStatus.UNVERIFIED)
            )
        elif verified.value != claim.claimed_value:
            corrections.append(
                Correction(
                    claim=claim, verified_value=verified.value, status=CorrectionStatus.CORRECTED
                )
            )
    return corrections


class Verifier:
    """Facade retained for dependency injection and test ergonomics."""

    def verify_game(self, game: Game, evidence_ids: Iterable[str] = ()) -> FactBundle:
        return verify_game(game, evidence_ids)

    def verify_stat_lines(
        self, lines: Iterable[StatLine], evidence_ids: Iterable[str] = ()
    ) -> FactBundle:
        return verify_stat_lines(lines, evidence_ids)

    def verify_bundle(
        self, bundle: GameBundle, evidence_ids: Iterable[str] = ()
    ) -> VerificationResult:
        return verify_bundle(bundle, evidence_ids)

    def verify_premise(self, claims: Iterable[Claim], facts: FactBundle) -> list[Any]:
        return verify_premise(claims, facts)


__all__ = [
    "VerificationResult",
    "Verifier",
    "verify_bundle",
    "verify_game",
    "verify_premise",
    "verify_stat_lines",
]
