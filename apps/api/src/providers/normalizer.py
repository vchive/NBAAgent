"""Normalize fixture/live shaped payloads into canonical domain objects.

The normalizer is deliberately boring: unknown fields are ignored, timestamps are
made timezone-aware, and missing values stay ``None``.  Keeping this boundary small
means a public adapter can be swapped without changing the chat use case.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from apps.api.src.domain.models import (
    EntityRef,
    Evidence,
    Freshness,
    Game,
    GameBundle,
    HistoryRecord,
    NewsItem,
    PlayByPlayBundle,
    SeasonLabel,
    SeriesRef,
    SourceClass,
    Standing,
    StatLine,
    TrustLevel,
)
from apps.api.src.domain.time_policy import make_season_label, to_utc

# Provider news fields are untrusted text.  Keep only a bounded, display-safe
# projection at the adapter boundary; the application never receives raw HTML
# or control characters from a fixture/live payload.  This is intentionally a
# small sanitizer rather than a Markdown/HTML parser because news summaries
# are rendered as plain text by the deterministic composer.
_BLOCK_TAG_RE = re.compile(
    r"<\s*(?:script|style)\b[^>]*>.*?<\s*/\s*(?:script|style)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]{0,200}>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _news_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value)).strip()
    text = _BLOCK_TAG_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    text = _CONTROL_RE.sub(" ", text)
    # Collapse runs of whitespace introduced by tag/control removal while
    # preserving ordinary spaces in Chinese/English copy.
    text = " ".join(text.split())
    return text[:limit] or None


def season(value: str | SeasonLabel | dict[str, Any]) -> SeasonLabel:
    if isinstance(value, SeasonLabel):
        return value
    if isinstance(value, dict):
        return SeasonLabel.model_validate(value)
    start = int(str(value).split("-")[0])
    return make_season_label(start)


def instant(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return to_utc(dt)


def entity(value: EntityRef | dict[str, Any]) -> EntityRef:
    if isinstance(value, EntityRef):
        return value
    payload = dict(value)
    payload.setdefault("aliases", [])
    payload.setdefault("confidence", 1)
    return EntityRef.model_validate(payload)


class Normalizer:
    """Canonical mapping helpers used by all provider adapters."""

    source_class = SourceClass.FIXTURE
    source_ref = "fixture.v1"
    source_url = "https://fixture.invalid/nba"

    def evidence(
        self,
        evidence_id: str,
        *,
        retrieved_at_utc: datetime | None = None,
        partial: bool = False,
    ) -> Evidence:
        fetched = instant(retrieved_at_utc) or datetime.now(UTC)
        return Evidence(
            evidence_id=evidence_id,
            source_class=self.source_class,
            source_ref=self.source_ref,
            url=self.source_url,
            fetched_at_utc=fetched,
            data_as_of_utc=fetched,
            trust=TrustLevel.HIGH,
            freshness=Freshness.FRESH if not partial else Freshness.UNKNOWN,
        )

    def game(self, raw: dict[str, Any]) -> Game:
        payload = dict(raw)
        payload["season"] = season(payload["season"])
        payload["start_utc"] = instant(payload["start_utc"])
        payload["home"] = entity(payload["home"])
        payload["away"] = entity(payload["away"])
        return Game.model_validate(payload)

    def stat_line(self, raw: dict[str, Any]) -> StatLine:
        payload = dict(raw)
        payload["subject"] = entity(payload["subject"])
        if payload.get("season") is not None:
            payload["season"] = season(payload["season"])
        return StatLine.model_validate(payload)

    def series(self, raw: dict[str, Any] | None) -> SeriesRef | None:
        if not raw:
            return None
        payload = dict(raw)
        payload["season"] = season(payload["season"])
        for key in ("home", "away"):
            if payload.get(key) is not None:
                payload[key] = entity(payload[key])
        return SeriesRef.model_validate(payload)

    def standing(self, raw: dict[str, Any]) -> Standing:
        payload = dict(raw)
        payload["season"] = season(payload["season"])
        payload["team"] = entity(payload["team"])
        payload["as_of_utc"] = instant(payload.get("as_of_utc"))
        return Standing.model_validate(payload)

    def history(self, raw: dict[str, Any]) -> HistoryRecord:
        payload = dict(raw)
        if payload.get("season") is not None:
            payload["season"] = season(payload["season"])
        if payload.get("subject") is not None:
            payload["subject"] = entity(payload["subject"])
        return HistoryRecord.model_validate(payload)

    def news(self, raw: dict[str, Any]) -> NewsItem:
        """Map a provider article to the bounded canonical news shape.

        ESPN and fixture snapshots use slightly different field names
        (``headline``/``title``, ``published``/``published_utc`` and
        ``description``/``summary``).  Normalising those aliases here keeps
        the fixture provider and live adapter on the same contract.  Subject
        references are deliberately copied only when they already have the
        canonical ``EntityRef`` shape; arbitrary provider category text is
        ignored instead of being exposed as an entity.
        """

        payload = dict(raw)
        news_id = payload.get("news_id", payload.get("id"))
        news_id = str(news_id) if news_id is not None else None

        title = payload.get("title", payload.get("headline"))
        # Do not invent a title for malformed provider rows.  Leaving ``None``
        # lets the canonical model reject that row (and the provider mark its
        # response partial) instead of presenting a fabricated headline.
        title_text = _news_text(title, limit=500)

        published = payload.get("published_utc")
        if published is None:
            published = payload.get(
                "published", payload.get("publishedAt", payload.get("lastModified"))
            )
        published_utc = instant(published)

        subjects = payload.get("subject_refs")
        if subjects is None:
            subjects = payload.get("subjects", payload.get("categories", []))
        if isinstance(subjects, dict):
            subjects = [subjects]
        if not isinstance(subjects, (list, tuple)):
            subjects = []
        normalised_subjects: list[EntityRef] = []
        for value in subjects:
            if isinstance(value, EntityRef):
                normalised_subjects.append(value)
                continue
            if not isinstance(value, dict):
                continue
            # Category payloads sometimes wrap the canonical entity under
            # ``team``/``athlete``; unwrap only those known containers.
            if isinstance(value.get("team"), dict):
                value = value["team"]
            elif isinstance(value.get("athlete"), dict):
                value = value["athlete"]
            try:
                normalised_subjects.append(entity(value))
            except (TypeError, ValueError):
                # One malformed category should not make an otherwise valid
                # article unusable.  The provider result can still be marked
                # partial by its adapter when it needs to surface that fact.
                continue
        summary = payload.get("summary", payload.get("description"))
        summary_text = _news_text(summary, limit=4000)

        # ``NewsItem`` requires an evidence link.  Live adapters provide this
        # explicitly; fixture records conventionally derive it from the ID so
        # direct normalizer use remains deterministic and safe.
        evidence_id = payload.get("evidence_id")
        if not evidence_id and news_id:
            evidence_id = f"fixture:news:{news_id}"
        return NewsItem.model_validate(
            {
                "news_id": news_id,
                "title": title_text,
                "published_utc": published_utc,
                "subject_refs": normalised_subjects[:16],
                "summary": summary_text,
                "evidence_id": evidence_id,
            }
        )

    def pbp(self, raw: dict[str, Any]) -> PlayByPlayBundle:
        # Import lazily to keep this module usable while adapters are bootstrapped.
        from apps.api.src.domain.models import PlayEvent

        payload = dict(raw)
        events = []
        for item in payload.get("events", []):
            event = dict(item)
            for key in ("shooter", "assister"):
                if event.get(key) is not None:
                    event[key] = entity(event[key])
            event["wallclock_utc"] = instant(event.get("wallclock_utc"))
            events.append(PlayEvent.model_validate(event))
        payload["events"] = events
        return PlayByPlayBundle.model_validate(payload)

    def bundle(
        self,
        raw: dict[str, Any],
        *,
        game_raw: dict[str, Any] | None = None,
    ) -> GameBundle:
        game_payload = game_raw or raw.get("game")
        if game_payload is None:
            raise ValueError("summary payload is missing game")
        stats = [self.stat_line(x) for x in raw.get("stat_lines", [])]
        leaders = [self.stat_line(x) for x in raw.get("leaders", [])]
        return GameBundle(
            game=self.game(game_payload),
            stat_lines=stats,
            leaders=leaders,
            series=self.series(raw.get("series")),
            plays=self.pbp(raw["plays"]) if raw.get("plays") else None,
        )


def normalize_many(
    normalizer: Normalizer, values: Iterable[dict[str, Any]], kind: str
) -> list[Any]:
    fn = getattr(normalizer, kind)
    return [fn(value) for value in values]


__all__ = ["Normalizer", "entity", "instant", "normalize_many", "season"]
