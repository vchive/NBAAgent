"""Date-scoped scoreboard/highlights projection."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from apps.api.src.api.schemas import (
    HighlightAvailabilityDay,
    HighlightDetailResponse,
    HighlightGame,
    HighlightLeader,
    HighlightPlay,
    HighlightsAvailabilityResponse,
    HighlightsRangeResponse,
    HighlightsResponse,
)
from apps.api.src.application.ports import RequestBudget
from apps.api.src.domain.models import (
    DateRange,
    EvidenceState,
    Game,
    GameBundle,
    GameFilters,
    PlayEvent,
    PlayEventType,
    ShotType,
    StatLine,
)
from apps.api.src.domain.time_policy import (
    format_beijing,
    local_date_range,
    validate_timezone,
)


class FutureHighlightsDateError(ValueError):
    """Requested calendar date is later than today in the caller's timezone."""


class HighlightsAvailabilityRangeError(ValueError):
    """Calendar availability requests must stay within a bounded date range."""


MAX_AVAILABILITY_DAYS = 31
# A custom review window is intentionally bounded.  It is wide enough for a
# month-plus playoff run while preventing an accidental year-long fan-out of
# daily provider requests from the public demo.
MAX_HIGHLIGHTS_RANGE_DAYS = 93
RECENT_HIGHLIGHTS_LOOKBACK_DAYS = 120


class HighlightsProviderError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


class HighlightsService:
    def __init__(self, gateway: Any, *, clock: Any | None = None) -> None:
        self.gateway = gateway
        self.clock = clock

    def _now(self) -> datetime:
        if self.clock is None:
            return datetime.now(UTC)
        value = self.clock.now_utc() if hasattr(self.clock, "now_utc") else self.clock()
        return value.astimezone(UTC)

    async def for_date(
        self, day: date, *, timezone_name: str = "Asia/Shanghai"
    ) -> HighlightsResponse:
        zone = validate_timezone(timezone_name)
        timezone_name = zone.key
        local_today = self._now().astimezone(zone).date()
        if day > local_today:
            raise FutureHighlightsDateError("future highlight dates are not available")
        date_range = local_date_range(day, timezone_name)
        budget = RequestBudget(
            self._now().replace(microsecond=0) + timedelta(seconds=8),
            max_provider_operations=2,
            clock=self.clock,
        )
        result = await self.gateway.search_games(
            GameFilters(date_range=date_range),
            budget=budget,
        )
        if result.error is not None:
            kind = getattr(result.error.kind, "value", str(result.error.kind))
            mapping = {
                "TIMEOUT": ("UPSTREAM_TIMEOUT", "数据暂时不可用，请稍后重试。", True, 504),
                "RATE_LIMITED": (
                    "UPSTREAM_RATE_LIMITED",
                    "数据服务暂时繁忙，请稍后重试。",
                    True,
                    429,
                ),
                "AUTH": ("UPSTREAM_AUTH", "数据服务暂时不可用，请稍后再试。", False, 502),
            }
            code, message, retryable, status_code = mapping.get(
                kind,
                (
                    "INVALID_UPSTREAM_DATA",
                    "公开数据格式异常，暂时无法核验。",
                    False,
                    502,
                ),
            )
            raise HighlightsProviderError(code, message, retryable, status_code)
        games = result.data or []
        public_games = [self._public_game(game) for game in games if isinstance(game, Game)]
        evidence_state = (
            EvidenceState.NONE
            if not public_games
            else EvidenceState.PARTIAL
            if result.partial
            else EvidenceState.VERIFIED
        )
        as_of = format_beijing(result.retrieved_at_utc) if public_games else None
        # Pydantic wire schema accepts lowercase values; use its coercion to keep
        # the domain model uppercase internally.
        return HighlightsResponse(
            date=day.isoformat(),
            timezone=timezone_name,
            games=public_games,
            as_of_beijing=as_of,
            evidence_state=evidence_state.value.lower(),
        )

    async def for_range(
        self,
        start_day: date,
        end_day: date,
        *,
        timezone_name: str = "Asia/Shanghai",
    ) -> HighlightsRangeResponse:
        """Return every game in a bounded local-date interval."""

        return await self._range(
            start_day,
            end_day,
            timezone_name=timezone_name,
            limit=None,
            max_days=MAX_HIGHLIGHTS_RANGE_DAYS,
        )

    async def recent(
        self,
        *,
        limit: int = 5,
        timezone_name: str = "Asia/Shanghai",
        reference_day: date | None = None,
    ) -> HighlightsRangeResponse:
        """Return the latest completed games, newest first.

        The provider is queried in bounded slices from newest to oldest and
        stops once enough games have been collected.  The wider lookback keeps
        the review panel useful during the off-season while avoiding an
        unbounded historical scan.
        """

        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise HighlightsAvailabilityRangeError("recent limit must be between 1 and 20")
        zone = validate_timezone(timezone_name)
        end_day = reference_day or self._now().astimezone(zone).date()
        start_day = end_day - timedelta(days=RECENT_HIGHLIGHTS_LOOKBACK_DAYS - 1)
        return await self._range(
            start_day,
            end_day,
            timezone_name=zone.key,
            limit=limit,
            newest_first=True,
            max_days=RECENT_HIGHLIGHTS_LOOKBACK_DAYS,
        )

    async def _range(
        self,
        start_day: date,
        end_day: date,
        *,
        timezone_name: str,
        limit: int | None,
        newest_first: bool = False,
        max_days: int = MAX_HIGHLIGHTS_RANGE_DAYS,
    ) -> HighlightsRangeResponse:
        zone = validate_timezone(timezone_name)
        timezone_name = zone.key
        if not isinstance(start_day, date) or isinstance(start_day, datetime):
            raise HighlightsAvailabilityRangeError("range requires calendar dates")
        if not isinstance(end_day, date) or isinstance(end_day, datetime):
            raise HighlightsAvailabilityRangeError("range requires calendar dates")
        if end_day < start_day:
            raise HighlightsAvailabilityRangeError("range must be ordered")
        span = (end_day - start_day).days + 1
        if span > max_days:
            raise HighlightsAvailabilityRangeError(
                f"highlight range is limited to {max_days} days"
            )
        local_today = self._now().astimezone(zone).date()
        if end_day > local_today:
            raise FutureHighlightsDateError("future highlight dates are not available")

        provider = getattr(self.gateway, "provider", self.gateway)
        provider_limit = getattr(provider, "max_date_slices", None)
        if provider_limit is None:
            slice_limit = span
        else:
            try:
                slice_limit = int(provider_limit) - 1
            except (TypeError, ValueError):
                slice_limit = span
        slice_limit = max(1, min(slice_limit, max_days))
        days = [start_day + timedelta(days=index) for index in range(span)]
        chunks = [days[index : index + slice_limit] for index in range(0, span, slice_limit)]
        if newest_first:
            chunks.reverse()

        # Each chunk reserves one gateway hand-off plus one provider operation
        # per local date for adapters such as ESPN.  The generous bounded cap
        # is still request-scoped and cannot grow beyond the 93-day API limit.
        budget = RequestBudget(
            self._now().replace(microsecond=0) + timedelta(seconds=20),
            max_provider_operations=max(8, span + 2 * len(chunks) + 2),
            max_retries_per_operation=0,
            clock=self.clock,
        )
        games_by_id: dict[str, Game] = {}
        retrieved: list[datetime] = []
        had_success = False
        had_unknown = False
        first_error: Any | None = None
        for chunk in chunks:
            first = local_date_range(chunk[0], timezone_name)
            last = local_date_range(chunk[-1], timezone_name)
            date_range = DateRange(
                start_inclusive=first.start_inclusive,
                end_exclusive=last.end_exclusive,
            )
            try:
                result = await self.gateway.search_games(
                    GameFilters(date_range=date_range),
                    budget=budget,
                )
            except Exception:
                had_unknown = True
                continue
            if result.error is not None or not isinstance(result.data, list):
                first_error = first_error or result.error
                had_unknown = True
                continue
            had_success = True
            had_unknown = had_unknown or bool(result.partial)
            if isinstance(result.retrieved_at_utc, datetime):
                retrieved.append(result.retrieved_at_utc)
            for game in result.data:
                if not isinstance(game, Game):
                    had_unknown = True
                    continue
                game_day = game.start_utc.astimezone(zone).date()
                if start_day <= game_day <= end_day:
                    games_by_id[game.game_id] = game
            if limit is not None and len(games_by_id) >= limit:
                break

        if not had_success and first_error is not None:
            kind = getattr(first_error.kind, "value", str(first_error.kind))
            mapping = {
                "TIMEOUT": ("UPSTREAM_TIMEOUT", "数据暂时不可用，请稍后重试。", True, 504),
                "RATE_LIMITED": (
                    "UPSTREAM_RATE_LIMITED",
                    "数据服务暂时繁忙，请稍后重试。",
                    True,
                    429,
                ),
                "AUTH": ("UPSTREAM_AUTH", "数据服务暂时不可用，请稍后再试。", False, 502),
            }
            code, message, retryable, status_code = mapping.get(
                kind,
                ("INVALID_UPSTREAM_DATA", "公开数据格式异常，暂时无法核验。", False, 502),
            )
            raise HighlightsProviderError(code, message, retryable, status_code)

        games = sorted(games_by_id.values(), key=lambda item: item.start_utc, reverse=True)
        if limit is not None:
            games = games[:limit]
        public_games = [self._public_game(game) for game in games]
        evidence_state = (
            EvidenceState.PARTIAL
            if had_unknown
            else EvidenceState.VERIFIED
            if public_games
            else EvidenceState.NONE
        )
        return HighlightsRangeResponse(
            timezone=timezone_name,
            from_date=start_day.isoformat(),
            to_date=end_day.isoformat(),
            games=public_games,
            as_of_beijing=format_beijing(max(retrieved)) if public_games and retrieved else None,
            evidence_state=evidence_state.value.lower(),
        )

    async def detail(
        self, game_id: str, *, timezone_name: str = "Asia/Shanghai"
    ) -> HighlightDetailResponse:
        """Load the selected game's safe scoreboard and replay projection.

        The date projection intentionally stays cheap and only lists games.  A
        second, bounded call loads the summary/PBP for the selected card so a
        multi-game slate does not fan out into one upstream request per game.
        """

        zone = validate_timezone(timezone_name)
        timezone_name = zone.key
        budget = RequestBudget(
            self._now().replace(microsecond=0) + timedelta(seconds=8),
            max_provider_operations=2,
            clock=self.clock,
        )
        result = await self.gateway.get_game_summary(game_id, budget=budget)
        if result.error is not None:
            kind = getattr(result.error.kind, "value", str(result.error.kind))
            mapping = {
                "NOT_FOUND": ("NOT_FOUND", "该场比赛暂无可用详情。", False, 404),
                "TIMEOUT": ("UPSTREAM_TIMEOUT", "数据暂时不可用，请稍后重试。", True, 504),
                "RATE_LIMITED": (
                    "UPSTREAM_RATE_LIMITED",
                    "数据服务暂时繁忙，请稍后重试。",
                    True,
                    429,
                ),
                "AUTH": ("UPSTREAM_AUTH", "数据服务暂时不可用，请稍后再试。", False, 502),
            }
            code, message, retryable, status_code = mapping.get(
                kind,
                ("INVALID_UPSTREAM_DATA", "公开数据格式异常，暂时无法核验。", False, 502),
            )
            raise HighlightsProviderError(code, message, retryable, status_code)
        bundle = result.data
        if not isinstance(bundle, GameBundle):
            raise HighlightsProviderError(
                "INVALID_UPSTREAM_DATA", "公开数据格式异常，暂时无法核验。", False, 502
            )
        plays = self._public_plays(bundle)
        leaders = self._public_leaders(bundle.leaders or bundle.stat_lines)
        evidence_state = EvidenceState.PARTIAL if result.partial else EvidenceState.VERIFIED
        return HighlightDetailResponse(
            game=self._public_game(bundle.game),
            leaders=leaders,
            plays=plays,
            as_of_beijing=format_beijing(result.retrieved_at_utc),
            evidence_state=evidence_state.value.lower(),
        )

    async def availability(
        self,
        start_day: date,
        end_day: date,
        *,
        timezone_name: str = "Asia/Shanghai",
    ) -> HighlightsAvailabilityResponse:
        """Return a bounded, tri-state calendar projection.

        A successful empty provider response is represented as ``empty``.  A
        provider error or partial response is represented as ``unknown`` for
        dates that cannot be proven empty.  Future dates are never queried and
        remain ``unknown`` with ``is_future=true``; the browser can disable
        them without making an unsupported claim about the schedule.

        The fixture provider can answer the whole 31-day interval in one
        operation.  Adapters that expose a smaller ``max_date_slices`` value
        (the ESPN adapter currently uses seven) are queried in bounded chunks.
        This keeps the endpoint useful for fixtures while remaining honest when
        a live adapter cannot establish every date.
        """

        zone = validate_timezone(timezone_name)
        timezone_name = zone.key
        if not isinstance(start_day, date) or isinstance(start_day, datetime):
            raise HighlightsAvailabilityRangeError("availability range requires calendar dates")
        if not isinstance(end_day, date) or isinstance(end_day, datetime):
            raise HighlightsAvailabilityRangeError("availability range requires calendar dates")
        span = (end_day - start_day).days + 1
        if span <= 0:
            raise HighlightsAvailabilityRangeError("availability range must be ordered")
        if span > MAX_AVAILABILITY_DAYS:
            raise HighlightsAvailabilityRangeError(
                f"availability range is limited to {MAX_AVAILABILITY_DAYS} days"
            )

        local_today = self._now().astimezone(zone).date()
        requested_days = [start_day + timedelta(days=index) for index in range(span)]
        future_days = {day for day in requested_days if day > local_today}
        statuses: dict[date, tuple[str, int | None]] = {
            day: ("unknown", None) for day in requested_days
        }

        # Do not ask a provider for future dates.  Besides avoiding needless
        # calls, this preserves the existing highlights contract which rejects
        # a future date as an invalid user selection.
        past_days = [day for day in requested_days if day <= local_today]
        retrieved: list[datetime] = []
        had_success = False
        had_unknown = bool(future_days)
        if past_days:
            # ``HighlightsService`` normally receives ``ProviderGateway``;
            # accepting a direct adapter as well keeps the application seam
            # easy to exercise in contract tests.
            provider = getattr(self.gateway, "provider", self.gateway)
            provider_limit = getattr(provider, "max_date_slices", None)
            if provider_limit is None:
                slice_limit = len(past_days)
            else:
                try:
                    # A local calendar interval usually crosses one extra UTC
                    # date at each endpoint (for example, Beijing midnight is
                    # 16:00 UTC).  Leave one adapter slice of headroom so a
                    # seven-slice ESPN limit can safely cover six local days.
                    slice_limit = int(provider_limit) - 1
                except (TypeError, ValueError):
                    slice_limit = len(past_days)
            slice_limit = max(1, min(slice_limit, MAX_AVAILABILITY_DAYS))
            chunks = [
                past_days[index : index + slice_limit]
                for index in range(0, len(past_days), slice_limit)
            ]

            # One adapter-level operation is reserved per local day by the
            # current ESPN implementation, while a gateway invocation also
            # reserves its hand-off slot.  Disable retries here: an availability
            # probe must not turn a calendar render into an unbounded retry fan-
            # out.  Unknown days are safer than stale/guessed availability.
            budget = RequestBudget(
                self._now().replace(microsecond=0) + timedelta(seconds=8),
                # Leave one gateway reservation for the primary and (in a
                # hybrid profile) one deterministic fallback per chunk.  The
                # adapter-level date slices are covered by the local-day count
                # plus the two UTC boundary slices represented by each chunk.
                max_provider_operations=max(4, len(past_days) + 2 * len(chunks) + 2),
                max_retries_per_operation=0,
                clock=self.clock,
            )
            for chunk in chunks:
                chunk_start, chunk_end = chunk[0], chunk[-1]
                first = local_date_range(chunk_start, timezone_name)
                last = local_date_range(chunk_end, timezone_name)
                date_range = DateRange(
                    start_inclusive=first.start_inclusive,
                    end_exclusive=last.end_exclusive,
                )
                try:
                    result = await self.gateway.search_games(
                        GameFilters(date_range=date_range),
                        budget=budget,
                    )
                except Exception:
                    # ProviderGateway normally converts adapter exceptions to a
                    # typed ProviderResult.  Keep the projection defensive for
                    # injected test providers and never expose exception text.
                    had_unknown = True
                    continue

                error = getattr(result, "error", None)
                raw_games = getattr(result, "data", None)
                partial = bool(getattr(result, "partial", False))
                if error is not None or not isinstance(raw_games, list):
                    had_unknown = True
                    continue

                retrieved_at = getattr(result, "retrieved_at_utc", None)
                if isinstance(retrieved_at, datetime):
                    retrieved.append(retrieved_at)

                had_success = True
                valid_games = [game for game in raw_games if isinstance(game, Game)]
                malformed_rows = len(valid_games) != len(raw_games)
                games_by_day: dict[date, int] = {}
                seen_game_ids: set[str] = set()
                for game in valid_games:
                    if game.game_id in seen_game_ids:
                        continue
                    seen_game_ids.add(game.game_id)
                    game_day = game.start_utc.astimezone(zone).date()
                    if game_day in chunk:
                        games_by_day[game_day] = games_by_day.get(game_day, 0) + 1

                # A known game is enough to mark a day available even when the
                # chunk is partial.  Dates with no known game are only marked
                # empty when the whole chunk completed successfully.
                for day in chunk:
                    if games_by_day.get(day, 0):
                        statuses[day] = ("available", games_by_day[day])
                    elif partial or malformed_rows:
                        statuses[day] = ("unknown", None)
                        had_unknown = True
                    else:
                        statuses[day] = ("empty", 0)

        response_days = [
            HighlightAvailabilityDay(
                date=day.isoformat(),
                status=statuses[day][0],
                game_count=statuses[day][1],
                is_future=day in future_days,
            )
            for day in requested_days
        ]
        if retrieved:
            as_of = format_beijing(max(retrieved))
        else:
            as_of = None
        if had_unknown:
            evidence_state = EvidenceState.PARTIAL if had_success else EvidenceState.NONE
        elif any(status == "available" for status, _count in statuses.values()):
            evidence_state = EvidenceState.VERIFIED
        else:
            evidence_state = EvidenceState.NONE
        return HighlightsAvailabilityResponse(
            timezone=timezone_name,
            from_date=start_day.isoformat(),
            to_date=end_day.isoformat(),
            days=response_days,
            as_of_beijing=as_of,
            evidence_state=evidence_state.value.lower(),
        )

    @staticmethod
    def _public_game(game: Game) -> HighlightGame:
        return HighlightGame(
            game_id=game.game_id,
            start_utc=game.start_utc,
            home_name=game.home.display_name,
            home_abbreviation=next(
                (alias for alias in game.home.aliases if len(alias) <= 4 and alias.isascii()),
                None,
            ),
            away_name=game.away.display_name,
            away_abbreviation=next(
                (alias for alias in game.away.aliases if len(alias) <= 4 and alias.isascii()),
                None,
            ),
            status=game.status.value.lower(),
            home_score=game.home_score,
            away_score=game.away_score,
            series_game_number=game.series_game_number,
        )

    @staticmethod
    def _public_leaders(rows: list[StatLine]) -> list[HighlightLeader]:
        values: list[HighlightLeader] = []
        for row in rows[:16]:
            subject = getattr(row, "subject", None)
            name = getattr(subject, "display_name", None)
            if not isinstance(name, str) or not name.strip():
                continue
            metrics = getattr(row, "metrics", {}) or {}

            def metric(name: str) -> int | None:
                value = metrics.get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return int(value)
                return None

            values.append(
                HighlightLeader(
                    player_name=name.strip(),
                    points=metric("points"),
                    rebounds=metric("rebounds"),
                    assists=metric("assists"),
                )
            )
        return values

    @staticmethod
    def _public_plays(bundle: GameBundle) -> list[HighlightPlay]:
        if bundle.plays is None:
            return []
        home = bundle.game.home
        away = bundle.game.away
        home_alias = next(
            (alias for alias in home.aliases if len(alias) <= 4 and alias.isascii()), None
        )
        away_alias = next(
            (alias for alias in away.aliases if len(alias) <= 4 and alias.isascii()), None
        )
        events = sorted(
            bundle.plays.events,
            key=lambda item: (
                item.period,
                -float(item.clock_seconds_remaining),
                item.provider_index,
            ),
        )
        output: list[HighlightPlay] = []
        previous_home: int | None = None
        previous_away: int | None = None
        for event in events[:2000]:
            team = None
            if event.home_score_after is not None and event.away_score_after is not None:
                if previous_home is not None and event.home_score_after > previous_home:
                    team = home_alias
                elif previous_away is not None and event.away_score_after > previous_away:
                    team = away_alias
                previous_home = event.home_score_after
                previous_away = event.away_score_after
            player = event.shooter.display_name if event.shooter is not None else None
            action = HighlightsService._play_action(event)
            detail = (
                f"比分更新至 {event.away_score_after}–{event.home_score_after}"
                if event.home_score_after is not None and event.away_score_after is not None
                else None
            )
            output.append(
                HighlightPlay(
                    period=event.period,
                    clock=HighlightsService._format_clock(event.clock_seconds_remaining),
                    team=team,
                    player_name=player,
                    action=action,
                    detail=detail,
                    home_score=event.home_score_after,
                    away_score=event.away_score_after,
                )
            )
        return output

    @staticmethod
    def _format_clock(seconds: Any) -> str:
        total = max(0.0, float(seconds))
        minutes = int(total // 60)
        remainder = total - minutes * 60
        return f"{minutes}:{remainder:04.1f}"

    @staticmethod
    def _play_action(event: PlayEvent) -> str:
        if event.event_type is PlayEventType.SHOT:
            label = {
                ShotType.THREE_POINT: "三分",
                ShotType.TWO_POINT: "两分",
                ShotType.UNKNOWN: "投篮",
                ShotType.NONE: "投篮",
                ShotType.FREE_THROW: "罚球",
            }.get(event.shot_type, "投篮")
            return f"{label}{'命中' if event.points is not None else '出手'}"
        if event.event_type is PlayEventType.FREE_THROW:
            return f"罚球{'命中' if event.points is not None else '出手'}"
        return {
            PlayEventType.FOUL: "犯规",
            PlayEventType.TURNOVER: "失误",
            PlayEventType.REBOUND: "篮板",
            PlayEventType.SUBSTITUTION: "换人",
            PlayEventType.OTHER: "比赛事件",
        }.get(event.event_type, "比赛事件")


__all__ = [
    "FutureHighlightsDateError",
    "HighlightsAvailabilityRangeError",
    "HighlightsProviderError",
    "HighlightsService",
    "MAX_AVAILABILITY_DAYS",
    "MAX_HIGHLIGHTS_RANGE_DAYS",
    "RECENT_HIGHLIGHTS_LOOKBACK_DAYS",
]
