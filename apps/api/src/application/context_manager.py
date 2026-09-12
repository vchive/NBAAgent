"""Bounded multi-turn context management with session isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from apps.api.src.domain.models import (
    ConversationContext,
    EntityKind,
    FactBundle,
    QueryIntent,
    TurnSummary,
)
from apps.api.src.infrastructure.session_store import (
    InMemorySessionStore,
)
from apps.api.src.infrastructure.session_store import (
    SessionConflictError as StoreConflict,
)


class ContextManager:
    def __init__(
        self,
        store: InMemorySessionStore,
        *,
        ttl_seconds: int = 86_400,
        max_turns: int = 8,
        max_summary_bytes: int = 16_384,
        clock: Any | None = None,
    ) -> None:
        self.store = store
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns
        self.max_summary_bytes = max_summary_bytes
        self.clock = clock

    def _now(self) -> datetime:
        if self.clock is None:
            return datetime.now(UTC)
        if callable(self.clock):
            value = self.clock()
        elif hasattr(self.clock, "now_utc"):
            value = self.clock.now_utc()
        elif hasattr(self.clock, "now"):
            value = self.clock.now()
        else:
            raise TypeError("clock must be callable or expose now_utc()")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return an aware timestamp")
        return value.astimezone(UTC)

    async def load(self, session_id: UUID) -> ConversationContext | None:
        value = await self.store.load(session_id)
        return value if isinstance(value, ConversationContext) else None

    async def ensure(
        self, session_id: UUID, timezone_name: str = "Asia/Shanghai"
    ) -> ConversationContext:
        existing = await self.load(session_id)
        if existing is not None:
            return existing
        now = self._now()
        return ConversationContext(
            session_id=session_id,
            timezone=timezone_name,
            expires_at_utc=now + timedelta(seconds=self.ttl_seconds),
        )

    async def commit(
        self,
        context: ConversationContext,
        *,
        intent: QueryIntent,
        facts: FactBundle | None = None,
        answer: str = "",
        user_message: str = "",
    ) -> ConversationContext:
        refs = list(intent.entities[:8])
        fact_ids = [
            fact.fact_id
            for fact in (facts.facts if facts else [])
            if fact.verification.value in {"VERIFIED", "PARTIAL"}
        ][:32]
        summary_text = (answer or f"{intent.intent_name.value} 查询").strip()[:2048]
        bounded_user_message = " ".join(str(user_message or "").split())[:1000] or None

        def build_candidate(base: ConversationContext) -> ConversationContext:
            """Merge this turn onto *base* without crossing session boundaries."""

            explicit_game = next(
                (item for item in intent.entities if item.kind is EntityKind.GAME),
                None,
            )
            explicit_teams = list(
                {
                    item.canonical_id: item
                    for item in intent.entities
                    if item.kind is EntityKind.TEAM
                }.values()
            )
            # A bare, explicit two-team matchup starts a new series/topic
            # scope.  Keeping an old active game or player here makes the next
            # pronoun (``这场``/``他``) jump back across the topic switch.  A
            # numbered or explicit game is handled below and a recommendation
            # intent carries its verified selected game explicitly.
            starts_matchup_scope = bool(
                intent.matchup
                and len(explicit_teams) >= 2
                and explicit_game is None
                and intent.game_number is None
            )
            metric_names = {
                str(getattr(metric, "name", "")).casefold()
                for metric in intent.metrics
            }
            # A scoped ``G2`` lookup is resolved by matchup + season rather
            # than by the global fixture alias.  Once verification returns a
            # unique game subject, persist that canonical id as the new active
            # game so the following “那场” cannot jump back to a previously
            # recommended G4.  Recommendation/comparison turns intentionally
            # retain the chosen game even when they mention G2 as a foil.
            fact_game_refs: dict[str, Any] = {}
            if facts is not None:
                for fact in facts.facts:
                    subject = getattr(fact, "subject", None)
                    verification = str(
                        getattr(
                            getattr(fact, "verification", None),
                            "value",
                            getattr(fact, "verification", ""),
                        )
                    ).upper()
                    if (
                        subject is not None
                        and subject.kind is EntityKind.GAME
                        and verification in {"VERIFIED", "PARTIAL"}
                    ):
                        fact_game_refs[subject.canonical_id] = subject
            verified_game = (
                next(iter(fact_game_refs.values()))
                if len(fact_game_refs) == 1
                else None
            )
            if (
                verified_game is not None
                and intent.game_number is not None
                and "series_game_recommendation" not in metric_names
            ):
                season_label = getattr(intent.season, "label", None)
                verified_game = verified_game.model_copy(
                    update={
                        "display_name": (
                            f"{season_label} 系列赛 G{intent.game_number}"
                            if season_label
                            else f"系列赛 G{intent.game_number}"
                        ),
                        "aliases": [f"G{intent.game_number}"],
                    }
                )
            if explicit_game is not None:
                active_game = explicit_game
            elif (
                intent.game_number is not None
                and "series_game_recommendation" not in metric_names
            ):
                # Even a no-data result must invalidate the stale active game:
                # the user explicitly changed the target and did not ask a
                # comparison against the recommendation.
                active_game = verified_game
            elif starts_matchup_scope:
                active_game = None
            else:
                active_game = base.active_game
            active_team = next(
                (item for item in intent.entities if item.kind is EntityKind.TEAM),
                base.active_team,
            )
            explicit_player = next(
                (item for item in intent.entities if item.kind is EntityKind.PLAYER),
                None,
            )
            if explicit_player is not None:
                active_player = explicit_player
            elif starts_matchup_scope:
                active_player = None
            else:
                active_player = base.active_player
            turn = TurnSummary(
                turn_index=base.completed_user_turn_count + 1,
                user_intent=intent.intent_name.value,
                user_message=bounded_user_message,
                active_refs=refs,
                verified_fact_ids=fact_ids,
                text_summary=summary_text,
            )
            summaries = [*base.recent_turn_summaries, turn][-self.max_turns :]
            # Keep summaries under the byte budget, dropping oldest first.
            while summaries and sum(
                len((item.user_message or "").encode("utf-8"))
                + len(item.text_summary.encode("utf-8"))
                for item in summaries
            ) > self.max_summary_bytes:
                summaries.pop(0)
            return base.model_copy(
                update={
                    "active_game": active_game,
                    "active_team": active_team,
                    "active_player": active_player,
                    "active_season": intent.season or base.active_season,
                    "completed_user_turn_count": base.completed_user_turn_count + 1,
                    "turn_count": min(base.turn_count + 1, self.max_turns),
                    "recent_turn_summaries": summaries,
                    "expires_at_utc": self._now() + timedelta(seconds=self.ttl_seconds),
                }
            )

        base = context
        # A concurrent turn may commit between ``ensure`` and this write.  Reload
        # the latest context once and merge onto it instead of silently dropping
        # the active game/summary.  A persistent conflict is surfaced to the
        # caller after the bounded retry; it can safely ask the user to retry.
        for attempt in range(2):
            candidate = build_candidate(base)
            try:
                return await self.store.save(
                    base.session_id,
                    candidate,
                    expected_version=base.version,
                )
            except StoreConflict as exc:
                if attempt:
                    raise RuntimeError("session version conflict") from exc
                latest = await self.load(base.session_id)
                if latest is None:
                    latest = await self.ensure(base.session_id, base.timezone)
                base = latest
        raise RuntimeError("session version conflict")


__all__ = ["ContextManager"]
