"""Allow-listed public NBA data adapter for the optional live profile.

The application never passes URLs to this adapter.  Callers provide typed
filters/identifiers and the adapter constructs requests under a configured,
HTTPS-only base URL.  Raw provider fields stop at this module; all successful
results are canonical domain objects with internal evidence metadata.

The public service changes response shapes occasionally, so the normalisers
below accept the small set of shapes observed in scoreboard/summary responses
while preserving missing values as ``None``.  A malformed individual event or
stat row makes the result partial; it is never replaced with a fabricated zero.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import httpx

from apps.api.src.application.ports import ProviderResult, RequestBudget
from apps.api.src.domain.errors import ProviderError, ProviderErrorKind
from apps.api.src.domain.models import (
    EntityKind,
    EntityRef,
    Game,
    GameBundle,
    GameFilters,
    GameStatus,
    HistoryQuery,
    HistoryRecord,
    HistoryRecordType,
    NewsItem,
    NewsQuery,
    PlayByPlayBundle,
    PlayEvent,
    PlayEventType,
    SeasonLabel,
    SeriesRef,
    SeriesStage,
    ShotType,
    SourceClass,
    Standing,
    StatLine,
    StatScope,
    StatsQuery,
)
from apps.api.src.domain.time_policy import make_season_label, to_utc
from apps.api.src.providers.normalizer import Normalizer, instant

DEFAULT_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
DEFAULT_ALLOWED_HOSTS = ("site.api.espn.com", "site.web.api.espn.com")
USER_AGENT = "NBAAgent/0.1 (+https://github.com/vchive/NBAAgent)"
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_CLOCK_RE = re.compile(r"(?:(\d+):)?(\d{1,2})(?:\.(\d+))?")


def _first_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, Mapping)), None)
    return None


def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _list_or_single_mapping(value: Any) -> list[Mapping[str, Any]]:
    """Accept either ESPN's list form or a singleton object in fixtures."""

    if isinstance(value, Mapping):
        return [value]
    return _list_of_mappings(value)


def _number(value: Any) -> int | float | None:
    """Parse a provider numeric value without inventing a default."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        return _number(value.get("value", value.get("displayValue")))
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "N/A"}:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)


def _first_non_none(*values: Any) -> Any:
    """Return the first value that is present, preserving meaningful zeroes."""

    for value in values:
        if value is not None:
            return value
    return None


def _conference_label(value: Any) -> str | None:
    """Extract a human-readable conference label from ESPN metadata."""

    if value is None:
        return None
    if isinstance(value, Mapping):
        value = (
            value.get("displayName")
            or value.get("display_name")
            or value.get("name")
            or value.get("abbreviation")
        )
    text = str(value).strip()
    return text or None


class ESPNAdapter:
    """Typed, bounded adapter used only when the live profile is enabled."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 8.0,
        max_response_bytes: int = 2_000_000,
        allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS,
        max_date_slices: int = 7,
    ) -> None:
        parsed = urlparse(base_url)
        hosts = tuple(host.lower() for host in allowed_hosts)
        if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in hosts:
            raise ValueError("public-data base URL is not in the allow-list")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("public-data base URL must not contain credentials or a query")
        if timeout_seconds <= 0 or max_response_bytes <= 0 or max_date_slices <= 0:
            raise ValueError("adapter limits must be positive")

        self.base_url = base_url.rstrip("/")
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.allowed_hosts = hosts
        self.max_date_slices = max_date_slices
        self.normalizer = Normalizer()
        # Evidence is internal-only, but it must still reflect the real trust
        # class rather than masquerading as a local fixture.
        self.normalizer.source_class = SourceClass.ESTABLISHED_SPORTS
        self.normalizer.source_ref = "public.nba.web"
        self.normalizer.source_url = self.base_url
        self.calls = 0

    def _result_error(
        self,
        kind: ProviderErrorKind,
        message: str,
        retryable: bool,
        *,
        retry_after_seconds: int | None = None,
        retrieved_at: datetime | None = None,
    ) -> ProviderResult[Any]:
        return ProviderResult(
            data=None,
            evidence=[],
            error=ProviderError(
                kind=kind,
                safe_message=message,
                retryable=retryable,
                retry_after_seconds=retry_after_seconds,
            ),
            retrieved_at_utc=retrieved_at or datetime.now(UTC),
        )

    @staticmethod
    def _retry_after(response: httpx.Response) -> int | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return min(3600, max(0, int(raw)))
        except ValueError:
            return None

    def _request_url(self, path: str) -> str:
        clean = path.strip().lstrip("/")
        if not clean or not _SAFE_PATH_RE.fullmatch(clean) or ".." in clean.split("/"):
            raise ValueError("invalid provider path")
        url = f"{self.base_url}/{clean}"
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.lower() not in self.allowed_hosts
        ):
            raise ValueError("provider endpoint is not allowed")
        return url

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any],
        budget: RequestBudget,
    ) -> ProviderResult[dict[str, Any]]:
        if not budget.reserve_operation():
            return self._result_error(
                ProviderErrorKind.TIMEOUT,
                "request deadline exceeded",
                True,
            )
        try:
            url = self._request_url(path)
        except ValueError:
            return self._result_error(
                ProviderErrorKind.AUTH,
                "endpoint not allowed",
                False,
            )

        remaining_seconds = budget.remaining_ms() / 1000
        if remaining_seconds <= 0:
            return self._result_error(
                ProviderErrorKind.TIMEOUT,
                "request deadline exceeded",
                True,
            )
        timeout = min(self.timeout_seconds, remaining_seconds)
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
        )
        self.calls += 1
        retrieved = datetime.now(UTC)
        try:
            try:
                response = await client.get(url, params=params, timeout=timeout)
            except TypeError as exc:
                # A few lightweight contract-test clients implement only the
                # ``get(url, params=...)`` subset of httpx.  Falling back here
                # keeps the adapter injectable without weakening the timeout
                # used by a real AsyncClient.
                if "timeout" not in str(exc):
                    raise
                response = await client.get(url, params=params)
            retrieved = datetime.now(UTC)
            if response.is_redirect:
                return self._result_error(
                    ProviderErrorKind.AUTH,
                    "unexpected upstream redirect",
                    False,
                    retrieved_at=retrieved,
                )
            if response.status_code == 429:
                return self._result_error(
                    ProviderErrorKind.RATE_LIMITED,
                    "upstream rate limited",
                    True,
                    retry_after_seconds=self._retry_after(response),
                    retrieved_at=retrieved,
                )
            if response.status_code in {401, 403}:
                return self._result_error(
                    ProviderErrorKind.AUTH,
                    "upstream authentication failed",
                    False,
                    retrieved_at=retrieved,
                )
            if response.status_code == 404:
                return self._result_error(
                    ProviderErrorKind.NOT_FOUND,
                    "upstream record not found",
                    False,
                    retrieved_at=retrieved,
                )
            if response.status_code >= 500:
                return self._result_error(
                    ProviderErrorKind.HTTP,
                    "upstream unavailable",
                    True,
                    retrieved_at=retrieved,
                )
            if response.status_code >= 400:
                return self._result_error(
                    ProviderErrorKind.HTTP,
                    "upstream request failed",
                    False,
                    retrieved_at=retrieved,
                )
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > self.max_response_bytes:
                        return self._result_error(
                            ProviderErrorKind.SCHEMA_MISMATCH,
                            "upstream response too large",
                            False,
                            retrieved_at=retrieved,
                        )
                except ValueError:
                    pass
            if len(response.content) > self.max_response_bytes:
                return self._result_error(
                    ProviderErrorKind.SCHEMA_MISMATCH,
                    "upstream response too large",
                    False,
                    retrieved_at=retrieved,
                )
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                return self._result_error(
                    ProviderErrorKind.INVALID_JSON,
                    "upstream payload invalid",
                    False,
                    retrieved_at=retrieved,
                )
            if not isinstance(payload, dict):
                return self._result_error(
                    ProviderErrorKind.SCHEMA_MISMATCH,
                    "upstream payload invalid",
                    False,
                    retrieved_at=retrieved,
                )
            fingerprint = hashlib.sha256(
                json.dumps(
                    {"path": path, "params": params},
                    ensure_ascii=True,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()[:20]
            evidence = self.normalizer.evidence(
                f"public:{fingerprint}",
                retrieved_at_utc=retrieved,
            )
            return ProviderResult(
                data=payload,
                evidence=[evidence],
                retrieved_at_utc=retrieved,
            )
        except httpx.TimeoutException:
            return self._result_error(
                ProviderErrorKind.TIMEOUT,
                "upstream timed out",
                True,
                retrieved_at=retrieved,
            )
        except httpx.HTTPError:
            return self._result_error(
                ProviderErrorKind.HTTP,
                "upstream unavailable",
                True,
                retrieved_at=retrieved,
            )
        finally:
            if own_client:
                await client.aclose()

    @staticmethod
    def _provider_dates(filters: GameFilters, max_slices: int) -> list[str | None]:
        if filters.date_range is None:
            return [None]
        start = to_utc(filters.date_range.start_inclusive).date()
        # The interval is half-open.  Subtracting a microsecond prevents an
        # exact midnight end from adding an unnecessary third provider date.
        end = (to_utc(filters.date_range.end_exclusive) - timedelta(microseconds=1)).date()
        days = (end - start).days + 1
        if days > max_slices:
            raise ValueError("date range exceeds adapter slice limit")
        return [(start + timedelta(days=index)).strftime("%Y%m%d") for index in range(days)]

    async def search_games(
        self,
        filters: GameFilters,
        budget: RequestBudget,
    ) -> ProviderResult[list[Game]]:
        try:
            provider_dates = self._provider_dates(filters, self.max_date_slices)
        except ValueError:
            return self._result_error(
                ProviderErrorKind.SCHEMA_MISMATCH,
                "requested date range is too wide",
                False,
            )

        games_by_id: dict[str, Game] = {}
        evidence = []
        partial = False
        last_retrieved = datetime.now(UTC)
        first_error: ProviderError | None = None
        for date_value in provider_dates:
            params = {"dates": date_value} if date_value is not None else {}
            raw = await self._get("scoreboard", params=params, budget=budget)
            last_retrieved = raw.retrieved_at_utc
            if raw.error is not None:
                first_error = first_error or raw.error
                partial = True
                continue
            evidence.extend(raw.evidence)
            events = raw.data.get("events", []) if raw.data else []
            if not isinstance(events, list):
                partial = True
                continue
            for event in events:
                try:
                    game = self._game(event, season_hint=filters.season)
                except (KeyError, StopIteration, TypeError, ValueError):
                    partial = True
                    continue
                if self._matches_filters(game, filters):
                    games_by_id[game.game_id] = game

        if not games_by_id and first_error is not None and not evidence:
            return ProviderResult(
                data=None,
                evidence=[],
                partial=False,
                error=first_error,
                retrieved_at_utc=last_retrieved,
            )
        games = sorted(games_by_id.values(), key=lambda item: item.start_utc, reverse=True)
        return ProviderResult(
            data=games,
            evidence=self._dedupe_evidence(evidence),
            partial=partial,
            retrieved_at_utc=last_retrieved,
        )

    async def get_game_summary(
        self,
        game_id: str,
        budget: RequestBudget,
    ) -> ProviderResult[GameBundle]:
        if not self._safe_identifier(game_id):
            return self._result_error(
                ProviderErrorKind.SCHEMA_MISMATCH,
                "invalid game identifier",
                False,
            )
        raw = await self._get("summary", params={"event": game_id}, budget=budget)
        if raw.error is not None:
            return raw
        try:
            event = self._summary_event(raw.data or {}, expected_game_id=game_id)
            game = self._game(event)
        except (KeyError, StopIteration, TypeError, ValueError):
            return ProviderResult(
                data=None,
                evidence=raw.evidence,
                partial=False,
                error=ProviderError(
                    kind=ProviderErrorKind.SCHEMA_MISMATCH,
                    retryable=False,
                    safe_message="summary payload invalid",
                ),
                retrieved_at_utc=raw.retrieved_at_utc,
            )

        evidence_id = raw.evidence[0].evidence_id if raw.evidence else f"summary:{game.game_id}"
        stat_lines, stat_partial = self._summary_stats(
            raw.data or {}, game, evidence_id=evidence_id
        )
        leaders = self._leaders(raw.data or {}, stat_lines, game.game_id, evidence_id=evidence_id)
        series = self._series(event, game)
        plays: PlayByPlayBundle | None = None
        play_partial = False
        play_values = self._play_values(raw.data or {})
        if play_values:
            plays, play_partial = self._normalise_plays(game.game_id, play_values)
        return ProviderResult(
            data=GameBundle(
                game=game,
                stat_lines=stat_lines,
                leaders=leaders,
                series=series,
                plays=plays,
            ),
            evidence=raw.evidence,
            partial=stat_partial or play_partial,
            retrieved_at_utc=raw.retrieved_at_utc,
        )

    async def get_play_by_play(
        self,
        game_id: str,
        budget: RequestBudget,
    ) -> ProviderResult[PlayByPlayBundle]:
        if not self._safe_identifier(game_id):
            return self._result_error(
                ProviderErrorKind.SCHEMA_MISMATCH,
                "invalid game identifier",
                False,
            )
        raw = await self._get("summary", params={"event": game_id}, budget=budget)
        if raw.error is not None:
            return raw
        values = self._play_values(raw.data or {})
        if not values:
            return ProviderResult(
                data=None,
                evidence=raw.evidence,
                partial=False,
                error=ProviderError(
                    kind=ProviderErrorKind.NOT_FOUND,
                    retryable=False,
                    safe_message="play-by-play not available",
                ),
                retrieved_at_utc=raw.retrieved_at_utc,
            )
        bundle, partial = self._normalise_plays(game_id, values)
        return ProviderResult(
            data=bundle,
            evidence=raw.evidence,
            partial=partial,
            retrieved_at_utc=raw.retrieved_at_utc,
        )

    async def get_player_stats(
        self,
        query: StatsQuery,
        budget: RequestBudget,
    ) -> ProviderResult[list[StatLine]]:
        return await self._get_subject_stats(query, budget, subject_path="athletes")

    async def get_team_stats(
        self,
        query: StatsQuery,
        budget: RequestBudget,
    ) -> ProviderResult[list[StatLine]]:
        return await self._get_subject_stats(query, budget, subject_path="teams")

    async def _get_subject_stats(
        self,
        query: StatsQuery,
        budget: RequestBudget,
        *,
        subject_path: str,
    ) -> ProviderResult[list[StatLine]]:
        if not self._safe_identifier(query.subject.canonical_id):
            return self._result_error(
                ProviderErrorKind.SCHEMA_MISMATCH,
                "invalid subject identifier",
                False,
            )
        if query.game_id:
            summary = await self.get_game_summary(query.game_id, budget)
            if summary.error is not None:
                return ProviderResult(
                    data=None,
                    evidence=summary.evidence,
                    partial=summary.partial,
                    error=summary.error,
                    retrieved_at_utc=summary.retrieved_at_utc,
                )
            rows = [
                row
                for row in summary.data.stat_lines
                if row.subject.canonical_id == query.subject.canonical_id
            ]
            return ProviderResult(
                data=rows,
                evidence=summary.evidence,
                partial=summary.partial,
                retrieved_at_utc=summary.retrieved_at_utc,
            )

        params: dict[str, Any] = {}
        if query.season is not None:
            # Provider seasons use the ending year.
            params["season"] = query.season.end_year
        raw = await self._get(
            f"{subject_path}/{query.subject.canonical_id}/statistics",
            params=params,
            budget=budget,
        )
        if raw.error is not None:
            return raw
        evidence_id = (
            raw.evidence[0].evidence_id if raw.evidence else f"stats:{query.subject.canonical_id}"
        )
        rows, partial = self._generic_stat_rows(raw.data or {}, query, evidence_id=evidence_id)
        return ProviderResult(
            data=rows,
            evidence=raw.evidence,
            partial=partial,
            retrieved_at_utc=raw.retrieved_at_utc,
        )

    async def get_standings(
        self,
        season: SeasonLabel,
        budget: RequestBudget,
    ) -> ProviderResult[list[Standing]]:
        raw = await self._get(
            "standings",
            params={"season": season.end_year},
            budget=budget,
        )
        if raw.error is not None:
            return raw
        # ESPN commonly places the conference name on each ``children`` group
        # rather than repeating it on every entry.  Retain that parent label
        # while flattening so conference-scoped queries remain filterable at
        # the canonical boundary.
        entries: list[tuple[Mapping[str, Any], str | None]] = []
        root = raw.data or {}
        root_label = _conference_label(
            root.get("group") or root.get("conference") or root.get("name")
        )
        entries.extend((entry, root_label) for entry in _list_of_mappings(root.get("entries")))
        for group in _list_of_mappings(root.get("children")):
            standings = _first_mapping(group.get("standings")) or {}
            group_label = _conference_label(
                group.get("group")
                or group.get("conference")
                or group.get("displayName")
                or group.get("name")
                or standings.get("group")
                or standings.get("conference")
                or standings.get("name")
            )
            entries.extend(
                (entry, group_label) for entry in _list_of_mappings(standings.get("entries"))
            )
        result: list[Standing] = []
        partial = False
        for entry, parent_conference in entries:
            try:
                team = self._team_ref(entry)
                stats = {
                    str(item.get("name") or item.get("abbreviation") or ""): item.get(
                        "value", item.get("displayValue")
                    )
                    for item in _list_of_mappings(entry.get("stats"))
                }
                wins = _number(_first_non_none(stats.get("wins"), stats.get("W")))
                losses = _number(_first_non_none(stats.get("losses"), stats.get("L")))
                rank = _number(
                    _first_non_none(
                        stats.get("playoffSeed"),
                        stats.get("rank"),
                        entry.get("rank"),
                    )
                )
                conference = _conference_label(
                    entry.get("group") or entry.get("conference") or parent_conference
                )
                result.append(
                    Standing(
                        season=season,
                        team=EntityRef.model_validate(team),
                        conference=conference,
                        wins=int(wins) if wins is not None else None,
                        losses=int(losses) if losses is not None else None,
                        rank=int(rank) if rank is not None and rank >= 1 else None,
                        as_of_utc=raw.retrieved_at_utc,
                    )
                )
            except (TypeError, ValueError):
                partial = True
        return ProviderResult(
            data=result,
            evidence=raw.evidence,
            partial=partial,
            retrieved_at_utc=raw.retrieved_at_utc,
        )

    async def get_history(
        self,
        query: HistoryQuery,
        budget: RequestBudget,
    ) -> ProviderResult[list[HistoryRecord]]:
        """Return provider-supplied structured history when available.

        The public service does not expose one stable league-history endpoint.
        This operation therefore accepts only the provider's structured
        ``records`` response and returns an empty, successful result when the
        capability is unavailable.  It never scrapes arbitrary HTML.
        """

        params: dict[str, Any] = {
            "type": query.record_type.value.lower(),
            "limit": query.limit,
        }
        if query.subject_refs:
            params["team"] = query.subject_refs[0].canonical_id
        if query.season_range:
            params["season_start"] = query.season_range.start_inclusive.start_year
            params["season_end"] = query.season_range.end_inclusive.start_year
        raw = await self._get("history", params=params, budget=budget)
        if raw.error is not None:
            if raw.error.kind is ProviderErrorKind.NOT_FOUND:
                return ProviderResult(
                    data=[],
                    evidence=[],
                    retrieved_at_utc=raw.retrieved_at_utc,
                )
            return raw
        result: list[HistoryRecord] = []
        partial = False
        for index, item in enumerate(_list_of_mappings((raw.data or {}).get("records"))):
            try:
                subject = self._entity_ref(item.get("subject"), EntityKind.TEAM)
                season_value = item.get("season")
                if isinstance(season_value, Mapping):
                    season_value = season_value.get("displayName") or season_value.get("name")
                result.append(
                    HistoryRecord(
                        record_id=str(item.get("id") or f"history-{index}"),
                        record_type=HistoryRecordType(
                            str(item.get("record_type") or query.record_type.value).upper()
                        ),
                        subject=subject,
                        season=self._season_value(season_value) if season_value else None,
                        value=item.get("value"),
                        evidence_id=raw.evidence[0].evidence_id,
                    )
                )
            except (TypeError, ValueError):
                partial = True
        # A provider may ignore optional filters; enforce them again at the
        # canonical boundary so a broad response cannot leak into a scoped
        # answer.
        if query.subject_refs:
            subject_ids = {ref.canonical_id for ref in query.subject_refs}
            result = [
                item
                for item in result
                if item.subject is None or item.subject.canonical_id in subject_ids
            ]
        if query.season_range:
            result = [
                item
                for item in result
                if item.season is None
                or query.season_range.start_inclusive.start_year
                <= item.season.start_year
                <= query.season_range.end_inclusive.start_year
            ]
        return ProviderResult(
            data=result[: query.limit],
            evidence=raw.evidence,
            partial=partial,
            retrieved_at_utc=raw.retrieved_at_utc,
        )

    async def search_news(
        self,
        query: NewsQuery,
        budget: RequestBudget,
    ) -> ProviderResult[list[NewsItem]]:
        params: dict[str, Any] = {"limit": query.limit}
        if query.keywords:
            params["query"] = " ".join(query.keywords)
        if query.date_range:
            params["from"] = query.date_range.start_inclusive.isoformat()
            params["to"] = query.date_range.end_exclusive.isoformat()
        raw = await self._get("news", params=params, budget=budget)
        if raw.error is not None:
            return raw
        values = (raw.data or {}).get("articles", (raw.data or {}).get("news", []))
        result: list[NewsItem] = []
        partial = False
        for index, item in enumerate(_list_of_mappings(values)):
            try:
                subjects = [
                    ref
                    for ref in (
                        self._entity_ref(value, EntityKind.TEAM)
                        for value in _list_of_mappings(item.get("categories"))
                    )
                    if ref is not None
                ]
                published = _first_non_none(
                    item.get("published"),
                    item.get("published_utc"),
                    item.get("lastModified"),
                )
                result.append(
                    NewsItem(
                        news_id=str(item.get("id") or f"news-{index}"),
                        title=str(item["headline"] if "headline" in item else item["title"]),
                        published_utc=instant(published),
                        subject_refs=subjects[:16],
                        summary=(
                            str(item.get("description") or item.get("summary"))[:4000] or None
                        ),
                        evidence_id=raw.evidence[0].evidence_id,
                    )
                )
            except (KeyError, TypeError, ValueError):
                partial = True
        if query.subject_refs:
            subject_ids = {ref.canonical_id for ref in query.subject_refs}
            result = [
                item
                for item in result
                if any(ref.canonical_id in subject_ids for ref in item.subject_refs)
            ]
        if query.date_range:
            result = [
                item
                for item in result
                if item.published_utc is None
                or query.date_range.start_inclusive
                <= item.published_utc
                < query.date_range.end_exclusive
            ]
        return ProviderResult(
            data=result[: query.limit],
            evidence=raw.evidence,
            partial=partial,
            retrieved_at_utc=raw.retrieved_at_utc,
        )

    @staticmethod
    def _safe_identifier(value: str) -> bool:
        return bool(value and len(value) <= 128 and re.fullmatch(r"[A-Za-z0-9_.-]+", value))

    @staticmethod
    def _dedupe_evidence(values: Iterable[Any]) -> list[Any]:
        result: dict[str, Any] = {}
        for value in values:
            result[getattr(value, "evidence_id", str(len(result)))] = value
        return list(result.values())

    @staticmethod
    def _matches_filters(game: Game, filters: GameFilters) -> bool:
        if filters.date_range is not None and not (
            filters.date_range.start_inclusive <= game.start_utc < filters.date_range.end_exclusive
        ):
            return False
        if filters.season is not None and game.season != filters.season:
            return False
        if filters.status is not None and game.status is not filters.status:
            return False
        if filters.team_ids and not (
            {game.home.canonical_id, game.away.canonical_id} & set(filters.team_ids)
        ):
            return False
        return True

    def _summary_event(
        self,
        payload: Mapping[str, Any],
        *,
        expected_game_id: str,
    ) -> Mapping[str, Any]:
        header = _first_mapping(payload.get("header")) or {}
        competitions = _list_of_mappings(header.get("competitions"))
        if competitions:
            competition = competitions[0]
            return {
                "id": header.get("id") or competition.get("id") or expected_game_id,
                "date": header.get("date") or competition.get("date"),
                "season": header.get("season"),
                # Depending on the endpoint version status may live on the
                # competition, header, or root object.  Preserve all three
                # possibilities so a completed summary is not downgraded to
                # UNKNOWN merely because one wrapper omitted its copy.
                "status": competition.get("status")
                or header.get("status")
                or payload.get("status"),
                "competitions": [competition],
            }
        events = _list_of_mappings(payload.get("events"))
        if events:
            return events[0]
        # Some captured summaries expose the competition at the root.
        if payload.get("competitors"):
            return {
                "id": payload.get("id") or expected_game_id,
                "date": payload.get("date"),
                "season": payload.get("season"),
                "status": payload.get("status"),
                "competitions": [payload],
            }
        raise ValueError("summary is missing a competition")

    def _game(
        self,
        event: Mapping[str, Any],
        *,
        season_hint: SeasonLabel | None = None,
    ) -> Game:
        competition = _list_of_mappings(event.get("competitions"))[0]
        competitors = _list_of_mappings(competition.get("competitors"))
        home = next(item for item in competitors if str(item.get("homeAway")).lower() == "home")
        away = next(item for item in competitors if str(item.get("homeAway")).lower() == "away")
        start = event.get("date") or competition.get("date")
        season_value = season_hint or self._season_from_event(event, start)
        series = _first_mapping(competition.get("series")) or {}
        return self.normalizer.game(
            {
                "game_id": str(event.get("id") or competition["id"]),
                "season": season_value,
                "start_utc": start,
                "home": self._team_ref(home),
                "away": self._team_ref(away),
                "status": self._status(event, competition),
                "home_score": self._score(home),
                "away_score": self._score(away),
                "series_id": str(series.get("id")) if series.get("id") is not None else None,
                "series_game_number": self._game_number(event, competition),
            }
        )

    @staticmethod
    def _team_ref(item: Mapping[str, Any]) -> dict[str, Any]:
        team = _first_mapping(item.get("team")) or item
        abbreviation = (
            team.get("abbreviation")
            or team.get("shortDisplayName")
            or team.get("displayName")
            or team.get("name")
            or "TEAM"
        )
        identifier = team.get("id") or team.get("uid") or abbreviation
        aliases = [
            str(value)
            for value in (
                abbreviation,
                team.get("displayName"),
                team.get("shortDisplayName"),
                team.get("name"),
                team.get("location"),
            )
            if value
        ]
        return {
            "kind": "TEAM",
            "canonical_id": str(identifier).lower(),
            "display_name": str(team.get("displayName") or abbreviation),
            "aliases": list(dict.fromkeys(aliases)),
            "confidence": 1,
        }

    @staticmethod
    def _player_ref(item: Mapping[str, Any]) -> EntityRef:
        athlete = _first_mapping(item.get("athlete")) or item
        name = athlete.get("displayName") or athlete.get("fullName") or athlete.get("shortName")
        identifier = athlete.get("id") or athlete.get("uid") or name
        if not identifier or not name:
            raise ValueError("athlete reference is incomplete")
        aliases = [
            str(value) for value in (athlete.get("shortName"), athlete.get("fullName")) if value
        ]
        return EntityRef(
            kind=EntityKind.PLAYER,
            canonical_id=str(identifier).lower(),
            display_name=str(name),
            aliases=list(dict.fromkeys(aliases)),
            confidence=1,
        )

    @classmethod
    def _entity_ref(
        cls,
        value: Any,
        default_kind: EntityKind,
    ) -> EntityRef | None:
        item = _first_mapping(value)
        if item is None:
            return None
        if default_kind is EntityKind.PLAYER or item.get("athlete"):
            return cls._player_ref(item)
        return EntityRef.model_validate(cls._team_ref(item))

    @staticmethod
    def _score(item: Mapping[str, Any]) -> int | None:
        value = _number(item.get("score"))
        return int(value) if value is not None else None

    @staticmethod
    def _status(event: Mapping[str, Any], competition: Mapping[str, Any]) -> str:
        raw_status = event.get("status") or competition.get("status")
        if isinstance(raw_status, str):
            state = raw_status.casefold()
            if state in {"post", "final", "completed"}:
                return GameStatus.FINAL.value
            if state in {"in", "live"}:
                return GameStatus.LIVE.value
            if state in {"pre", "scheduled"}:
                return GameStatus.SCHEDULED.value
            if "postpon" in state:
                return GameStatus.POSTPONED.value
            return GameStatus.UNKNOWN.value
        status = _first_mapping(raw_status) or {}
        status_type = _first_mapping(status.get("type")) or status
        state = str(status_type.get("state") or status_type.get("name") or "").lower()
        completed = status_type.get("completed")
        detail = str(status_type.get("description") or status_type.get("detail") or "").lower()
        if completed is True or state in {"post", "final"} or "final" in detail:
            return GameStatus.FINAL.value
        if state in {"in", "live"}:
            return GameStatus.LIVE.value
        if state in {"pre", "scheduled"}:
            return GameStatus.SCHEDULED.value
        if "postpon" in state or "postpon" in detail:
            return GameStatus.POSTPONED.value
        return GameStatus.UNKNOWN.value

    @staticmethod
    def _game_number(event: Mapping[str, Any], competition: Mapping[str, Any]) -> int | None:
        series = _first_mapping(competition.get("series")) or {}
        value = _number(series.get("gameNumber") or competition.get("seriesGameNumber"))
        if value is not None and 1 <= int(value) <= 20:
            return int(value)
        text_values = [
            event.get("name"),
            event.get("shortName"),
            *[note.get("headline") for note in _list_of_mappings(competition.get("notes"))],
        ]
        for text in text_values:
            match = re.search(r"(?:game|g)\s*(\d{1,2})", str(text or ""), re.I)
            if match and 1 <= int(match.group(1)) <= 20:
                return int(match.group(1))
        return None

    @classmethod
    def _season_from_event(
        cls,
        event: Mapping[str, Any],
        start: Any,
    ) -> SeasonLabel:
        season_raw = event.get("season")
        season = _first_mapping(season_raw)
        if season:
            display = season.get("displayName") or season.get("name")
            if display:
                try:
                    return cls._season_value(display)
                except ValueError:
                    pass
            year = _number(season.get("year"))
            if year is not None:
                return make_season_label(int(year) - 1)
        if isinstance(season_raw, str) and season_raw.strip():
            return cls._season_value(season_raw)
        return cls._season_from_date(start)

    @staticmethod
    def _season_value(value: Any) -> SeasonLabel:
        text = str(value).strip()
        match = re.search(r"(\d{4})[-/](\d{2,4})", text)
        if match:
            return make_season_label(int(match.group(1)))
        number = _number(value)
        if number is None:
            raise ValueError("invalid season")
        return make_season_label(int(number) - 1)

    @staticmethod
    def _season_from_date(value: Any) -> SeasonLabel:
        parsed = instant(value)
        if parsed is None:
            raise ValueError("event is missing a start date")
        return make_season_label(parsed.year - 1 if parsed.month <= 6 else parsed.year)

    def _series(self, event: Mapping[str, Any], game: Game) -> SeriesRef | None:
        competition = _list_of_mappings(event.get("competitions"))[0]
        raw = _first_mapping(competition.get("series")) or {}
        series_id = raw.get("id") or game.series_id
        if not series_id:
            return None
        notes = " ".join(
            str(note.get("headline") or "") for note in _list_of_mappings(competition.get("notes"))
        ).lower()
        stage = (
            SeriesStage.FINALS
            if "final" in notes
            else SeriesStage.PLAYOFF
            if raw
            else SeriesStage.REGULAR
        )
        return SeriesRef(
            series_id=str(series_id),
            season=game.season,
            stage=stage,
            home=game.home,
            away=game.away,
        )

    def _summary_stats(
        self,
        payload: Mapping[str, Any],
        game: Game,
        *,
        evidence_id: str,
    ) -> tuple[list[StatLine], bool]:
        boxscore = _first_mapping(payload.get("boxscore")) or {}
        rows: list[StatLine] = []
        partial = False
        for team_group in _list_of_mappings(boxscore.get("players")):
            statistics = _list_of_mappings(team_group.get("statistics"))
            for group in statistics:
                labels = group.get("labels") or group.get("names") or []
                keys = (
                    [self._metric_key(str(label)) for label in labels]
                    if isinstance(labels, list)
                    else []
                )
                for athlete_row in _list_of_mappings(group.get("athletes")):
                    try:
                        subject = self._player_ref(athlete_row)
                        values = athlete_row.get("stats") or athlete_row.get("statistics") or []
                        metrics: dict[str, int | float | None] = {}
                        if isinstance(values, list):
                            for key, value in zip(keys, values, strict=False):
                                if key:
                                    metrics[key] = _number(value)
                        elif isinstance(values, Mapping):
                            metrics = {
                                self._metric_key(str(key)): _number(value)
                                for key, value in values.items()
                                if self._metric_key(str(key))
                            }
                        if not metrics:
                            partial = True
                            continue
                        rows.append(
                            StatLine(
                                subject=subject,
                                game_id=game.game_id,
                                season=game.season,
                                scope=StatScope.GAME,
                                metrics=metrics,
                                metric_definitions={key: key for key in metrics},
                                evidence_ids=[evidence_id],
                            )
                        )
                    except (TypeError, ValueError):
                        partial = True
        return rows, partial

    def _leaders(
        self,
        payload: Mapping[str, Any],
        stat_lines: list[StatLine],
        game_id: str,
        *,
        evidence_id: str,
    ) -> list[StatLine]:
        leaders: list[StatLine] = []
        # ESPN has returned both an array and a singleton object for this
        # field across endpoint versions/captured responses.  Normalise the
        # outer value before walking the compact and team-wrapped shapes so a
        # valid one-player leader payload is not silently treated as empty.
        for root in _list_or_single_mapping(payload.get("leaders")):
            nested = _list_or_single_mapping(root.get("leaders"))
            category_entries: list[tuple[str, list[Mapping[str, Any]]]] = []
            if nested and any(
                item.get("athlete") is not None
                or item.get("value") is not None
                or item.get("displayValue") is not None
                for item in nested
            ):
                # Compact shape: {name: "points", leaders: [{athlete: ...}]}.
                category_entries.append(
                    (
                        str(
                            root.get("name")
                            or root.get("displayName")
                            or root.get("abbreviation")
                            or "points"
                        ),
                        nested,
                    )
                )
            elif nested:
                # Team-wrapped shape: {team: ..., leaders: [{name: "points", leaders: [...]}]}.
                for category in nested:
                    items = _list_or_single_mapping(category.get("leaders"))
                    if not items and category.get("athlete"):
                        items = [category]
                    category_entries.append(
                        (
                            str(
                                category.get("name")
                                or category.get("displayName")
                                or category.get("abbreviation")
                                or "points"
                            ),
                            items,
                        )
                    )
            elif root.get("athlete"):
                # Singleton leader object.
                category_entries.append(
                    (
                        str(root.get("name") or root.get("displayName") or "points"),
                        [root],
                    )
                )

            for metric_label, items in category_entries:
                metric = self._metric_key(metric_label)
                if not metric:
                    continue
                for item in items:
                    try:
                        subject = self._player_ref(item)
                        value = _number(
                            _first_non_none(item.get("value"), item.get("displayValue"))
                        )
                        leaders.append(
                            StatLine(
                                subject=subject,
                                game_id=game_id,
                                scope=StatScope.GAME,
                                metrics={metric: value},
                                metric_definitions={metric: metric},
                                evidence_ids=[evidence_id],
                            )
                        )
                    except (TypeError, ValueError):
                        continue
        if leaders:
            return leaders
        scored = [line for line in stat_lines if line.metrics.get("points") is not None]
        if not scored:
            return []
        top = max(float(line.metrics["points"]) for line in scored)
        return [line for line in scored if float(line.metrics["points"]) == top]

    @staticmethod
    def _metric_key(label: str) -> str:
        # ``\w`` retains CJK labels as well as ASCII abbreviations; the prior
        # ASCII-only filter turned a Chinese provider label into an empty
        # metric and silently dropped an otherwise valid stat.
        normalized = re.sub(r"[^\w%]", "", label.casefold())
        mapping = {
            "pts": "points",
            "points": "points",
            "reb": "rebounds",
            "rebs": "rebounds",
            "rebounds": "rebounds",
            "ast": "assists",
            "assists": "assists",
            "stl": "steals",
            "blk": "blocks",
            "to": "turnovers",
            "tov": "turnovers",
            "min": "minutes",
            "fg%": "field_goal_percentage",
            "fgpct": "field_goal_percentage",
            "3p%": "three_point_percentage",
            "3pt%": "three_point_percentage",
            "ft%": "free_throw_percentage",
            "fieldgoalpercentage": "field_goal_percentage",
            "threepointpercentage": "three_point_percentage",
            "ftpct": "free_throw_percentage",
            "罚球": "free_throws",
            "三分": "three_pointers",
            "得分": "points",
            "篮板": "rebounds",
            "助攻": "assists",
        }
        return mapping.get(normalized, normalized[:80])

    def _generic_stat_rows(
        self,
        payload: Mapping[str, Any],
        query: StatsQuery,
        *,
        evidence_id: str,
    ) -> tuple[list[StatLine], bool]:
        rows: list[StatLine] = []
        partial = False
        candidates = (
            payload.get("statistics") or payload.get("splits") or payload.get("categories") or []
        )
        for index, item in enumerate(_list_of_mappings(candidates)):
            values = item.get("stats") or item.get("values") or item
            metrics: dict[str, int | float | None] = {}
            if isinstance(values, Mapping):
                for key, value in values.items():
                    if isinstance(value, Mapping):
                        value = value.get("value", value.get("displayValue"))
                    metric = self._metric_key(str(key))
                    if metric and metric not in {"name", "displayname", "id"}:
                        metrics[metric] = _number(value)
            if not metrics:
                partial = True
                continue
            try:
                rows.append(
                    StatLine(
                        subject=query.subject,
                        game_id=query.game_id,
                        series_id=query.series_id,
                        season=query.season,
                        scope=query.scope,
                        metrics=metrics,
                        metric_definitions={key: key for key in metrics},
                        evidence_ids=[evidence_id],
                    )
                )
            except ValueError:
                partial = True
        return rows, partial

    @staticmethod
    def _play_values(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        # ``plays`` is normally an array, but summary responses and test
        # snapshots may contain a single play object.  Keep the same typed
        # projection for either shape, including the two nested ESPN package
        # wrappers.
        direct = _list_or_single_mapping(payload.get("plays"))
        if direct:
            return direct
        package = _first_mapping(payload.get("gamepackageJSON")) or {}
        values = _list_or_single_mapping(package.get("plays"))
        if values:
            return values
        content = _first_mapping(payload.get("content")) or {}
        return _list_or_single_mapping(content.get("plays"))

    def _normalise_plays(
        self,
        game_id: str,
        values: list[Mapping[str, Any]],
    ) -> tuple[PlayByPlayBundle, bool]:
        events: list[PlayEvent] = []
        partial = False
        for index, item in enumerate(values):
            try:
                events.append(self._play(item, game_id=game_id, provider_index=index))
            except (KeyError, TypeError, ValueError):
                partial = True
        sequences = [event.sequence for event in events]
        sequence_valid = (
            bool(events)
            and all(value is not None for value in sequences)
            and len(sequences) == len(set(sequences))
            and not partial
        )
        return (
            PlayByPlayBundle(
                game_id=game_id,
                events=events,
                sequence_valid=sequence_valid,
            ),
            partial,
        )

    def _play(
        self,
        item: Mapping[str, Any],
        *,
        game_id: str,
        provider_index: int,
    ) -> PlayEvent:
        period_raw = _first_mapping(item.get("period")) or {}
        period_value = _number(_first_non_none(period_raw.get("number"), item.get("period")))
        clock_raw = _first_mapping(item.get("clock"))
        if clock_raw:
            clock_value = _first_non_none(
                clock_raw.get("displayValue"),
                clock_raw.get("value"),
                clock_raw.get("secondsRemaining"),
            )
        else:
            clock_value = _first_non_none(item.get("clock"), item.get("clock_seconds_remaining"))
        text = str(item.get("text") or item.get("shortText") or "")
        type_raw = _first_mapping(item.get("type")) or {}
        type_text = str(type_raw.get("text") or type_raw.get("name") or "")
        participants = _list_or_single_mapping(item.get("participants"))
        event_type = self._event_type(type_text, text)
        shooter: EntityRef | None = None
        assister: EntityRef | None = None
        for participant in participants:
            try:
                ref = self._player_ref(participant)
            except ValueError:
                if "assist" in str(participant.get("type") or "").lower():
                    continue
                raise
            participant_type = str(participant.get("type") or participant.get("role") or "").lower()
            if "assist" in participant_type:
                assister = ref
            elif shooter is None and event_type in {PlayEventType.SHOT, PlayEventType.FREE_THROW}:
                shooter = ref
        if (
            shooter is None
            and item.get("athlete")
            and event_type in {PlayEventType.SHOT, PlayEventType.FREE_THROW}
        ):
            shooter = self._player_ref(item)
        sequence = _number(_first_non_none(item.get("sequenceNumber"), item.get("sequence")))
        points_value = (
            _first_non_none(item.get("scoreValue"), item.get("points"))
            if item.get("scoringPlay") is not False
            else None
        )
        points = _number(points_value)
        event_id = item.get("id") or item.get("playId") or f"{game_id}-{provider_index}"
        raw_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32] if text else None
        explicit_shot = item.get("shotType") or item.get("shot_type")
        shot_type = (
            self._shot_type(str(explicit_shot), "", event_type)
            if explicit_shot
            else self._shot_type(type_text, text, event_type)
        )
        return PlayEvent(
            event_id=str(event_id),
            game_id=game_id,
            sequence=int(sequence) if sequence is not None else None,
            provider_index=provider_index,
            period=int(period_value) if period_value is not None else 0,
            clock_seconds_remaining=Decimal(str(self._clock_seconds(clock_value))),
            event_type=event_type,
            shooter=shooter,
            assister=assister,
            shot_type=shot_type,
            points=int(points) if points is not None else None,
            home_score_after=self._optional_score(item.get("homeScore")),
            away_score_after=self._optional_score(item.get("awayScore")),
            wallclock_utc=instant(
                _first_non_none(item.get("wallclock"), item.get("wallclock_utc"))
            ),
            raw_text_hash=raw_hash,
        )

    @staticmethod
    def _optional_score(value: Any) -> int | None:
        parsed = _number(value)
        return int(parsed) if parsed is not None else None

    @staticmethod
    def _clock_seconds(value: Any) -> float:
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return float(value)
        text = str(value if value is not None else "").strip().upper()
        if text.startswith("PT") and text.endswith("S"):
            parsed = _number(text[2:-1])
            if parsed is not None:
                return float(parsed)
        match = _CLOCK_RE.fullmatch(text)
        if not match:
            raise ValueError("invalid play clock")
        minutes = int(match.group(1) or 0)
        seconds = int(match.group(2))
        fraction = float(f"0.{match.group(3)}") if match.group(3) else 0
        return minutes * 60 + seconds + fraction

    @staticmethod
    def _event_type(type_text: str, text: str) -> PlayEventType:
        value = f"{type_text} {text}".casefold()
        if "free throw" in value or "罚球" in value:
            return PlayEventType.FREE_THROW
        if any(
            token in value
            for token in (
                "jump",
                "layup",
                "dunk",
                "shot",
                "three point",
                "3-pt",
                "投篮",
                "上篮",
                "扣篮",
                "三分",
            )
        ):
            return PlayEventType.SHOT
        if "foul" in value or "犯规" in value:
            return PlayEventType.FOUL
        if "turnover" in value or "失误" in value:
            return PlayEventType.TURNOVER
        if "rebound" in value or "篮板" in value:
            return PlayEventType.REBOUND
        if "substitution" in value or "换人" in value:
            return PlayEventType.SUBSTITUTION
        return PlayEventType.OTHER

    @staticmethod
    def _shot_type(
        type_text: str,
        text: str,
        event_type: PlayEventType,
    ) -> ShotType:
        value = f"{type_text} {text}".casefold()
        if event_type is PlayEventType.FREE_THROW:
            return ShotType.FREE_THROW
        if event_type is not PlayEventType.SHOT:
            return ShotType.NONE
        if any(token in value for token in ("three point", "3-pt", "3pt", "三分")):
            return ShotType.THREE_POINT
        if any(
            token in value
            for token in (
                "jump",
                "layup",
                "dunk",
                "two point",
                "2-pt",
                "上篮",
                "扣篮",
                "两分",
            )
        ):
            return ShotType.TWO_POINT
        return ShotType.UNKNOWN


__all__ = [
    "DEFAULT_ALLOWED_HOSTS",
    "DEFAULT_BASE_URL",
    "ESPNAdapter",
]
