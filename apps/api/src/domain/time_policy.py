"""Time, season, and play-by-play window policy.

All comparison/storage timestamps are timezone-aware UTC instants.  Relative
phrases are interpreted in the caller's IANA timezone (Asia/Shanghai by
default), while user-facing formatting uses Beijing time.  Keeping this logic
in one module prevents provider-local dates from leaking into answers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    DateRange,
    PlayByPlayBundle,
    PlayEvent,
    SeasonLabel,
    TimeContext,
    TimeWindow,
    TimeWindowScope,
)

DEFAULT_INPUT_TIMEZONE = "Asia/Shanghai"
DEFAULT_DISPLAY_TIMEZONE = "Asia/Shanghai"
BEIJING = ZoneInfo(DEFAULT_DISPLAY_TIMEZONE)


class Clock(Protocol):
    """Minimal injectable clock used by parsers and deterministic tests."""

    def now_utc(self) -> datetime: ...


def ensure_aware(value: datetime) -> datetime:
    """Return ``value`` after asserting it carries a usable timezone."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


def to_utc(value: datetime) -> datetime:
    """Convert an aware timestamp to UTC."""

    return ensure_aware(value).astimezone(UTC)


def to_timezone(value: datetime, timezone_name: str) -> datetime:
    """Convert an aware timestamp to an IANA timezone."""

    return to_utc(value).astimezone(validate_timezone(timezone_name))


def to_beijing(value: datetime) -> datetime:
    return to_timezone(value, DEFAULT_DISPLAY_TIMEZONE)


def validate_timezone(timezone_name: str) -> ZoneInfo:
    """Validate and return an IANA ``ZoneInfo`` object."""

    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValueError("timezone must be a non-empty IANA name")
    try:
        return ZoneInfo(timezone_name.strip())
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid IANA timezone: {timezone_name}") from exc


def format_beijing(value: datetime, *, include_seconds: bool = False) -> str:
    """Format a timestamp for the public answer envelope."""

    local = to_beijing(value)
    pattern = "%Y-%m-%d %H:%M:%S" if include_seconds else "%Y-%m-%d %H:%M"
    return local.strftime(pattern)


def now_utc(clock: Clock | None = None) -> datetime:
    """Read an aware UTC instant from an injectable or system clock."""

    if clock is None:
        return datetime.now(UTC)
    value = clock() if callable(clock) else clock.now_utc()
    return to_utc(value)


@dataclass(frozen=True)
class SystemClock:
    """Production clock implementation."""

    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def now(self) -> datetime:
        return self.now_utc()


@dataclass(frozen=True)
class FixedClock:
    """Deterministic clock for unit/evaluation tests."""

    instant: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "instant", to_utc(self.instant))

    def now_utc(self) -> datetime:
        return self.instant

    def now(self) -> datetime:
        return self.instant


@dataclass(frozen=True)
class SeasonClock:
    """Convenience facade bundling an injectable clock and input timezone.

    The parser can use the standalone functions in this module, while callers
    that prefer an object-oriented policy can inject ``SeasonClock``.  No
    network lookup is performed; an official active-season signal may be passed
    explicitly to the methods when available.
    """

    clock: Clock | None = None
    timezone_name: str = DEFAULT_DISPLAY_TIMEZONE

    def now_utc(self) -> datetime:
        return now_utc(self.clock)

    def current(self, *, active_season: SeasonLabel | None = None) -> SeasonLabel:
        return current_season(
            self.clock,
            timezone_name=self.timezone_name,
            active_season=active_season,
        )

    # Names used by different parser prototypes.
    current_season = current

    def for_date(
        self,
        value: date | datetime,
        *,
        active_season: SeasonLabel | None = None,
    ) -> SeasonLabel:
        return season_label_for_date(
            value,
            timezone_name=self.timezone_name,
            active_season=active_season,
        )

    season_for_date = for_date

    def previous_completed(self) -> SeasonLabel:
        return previous_completed_season(self.clock, timezone_name=self.timezone_name)

    previous_completed_season = previous_completed

    def date_range(self, day: date | datetime) -> DateRange:
        return local_date_range(day, self.timezone_name)

    def resolve_date(self, phrase: str) -> date | None:
        return resolve_relative_date(phrase, self.clock, timezone_name=self.timezone_name)

    def resolve_season(self, phrase: str) -> SeasonLabel | None:
        return resolve_season_phrase(phrase, self.clock, timezone_name=self.timezone_name)


def _as_local_date(value: date | datetime, timezone_name: str) -> date:
    validate_timezone(timezone_name)
    if isinstance(value, datetime):
        return to_timezone(value, timezone_name).date()
    if isinstance(value, date):
        return value
    raise TypeError("expected date or datetime")


def local_date_range(
    day: date | datetime,
    timezone_name: str = DEFAULT_INPUT_TIMEZONE,
) -> DateRange:
    """Build a half-open UTC interval for one local calendar day.

    The interval is intentionally calculated in local time before converting
    to UTC, so DST transitions (for non-Beijing caller zones) remain correct.
    """

    zone = validate_timezone(timezone_name)
    local_day = _as_local_date(day, timezone_name)
    start_local = datetime.combine(local_day, time.min, tzinfo=zone)
    end_local = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=zone)
    return DateRange(
        start_inclusive=start_local.astimezone(UTC),
        end_exclusive=end_local.astimezone(UTC),
    )


def beijing_date_range(day: date | datetime) -> DateRange:
    return local_date_range(day, DEFAULT_DISPLAY_TIMEZONE)


date_range_for_local_date = local_date_range


_SEASON_RE = re.compile(r"^(?P<start>\d{4})[-/](?P<short>\d{2}|\d{4})$")


def make_season_label(start_year: int) -> SeasonLabel:
    """Construct a canonical cross-calendar-year season label."""

    return SeasonLabel(
        start_year=start_year,
        end_year=start_year + 1,
        label=f"{start_year:04d}-{(start_year + 1) % 100:02d}",
    )


def parse_season_label(value: str | SeasonLabel) -> SeasonLabel:
    if isinstance(value, SeasonLabel):
        return value
    if not isinstance(value, str):
        raise TypeError("season label must be text or SeasonLabel")
    text = value.strip().replace("赛季", "").strip()
    match = _SEASON_RE.fullmatch(text)
    if not match:
        raise ValueError("season must use YYYY-YY format")
    start = int(match.group("start"))
    short = match.group("short")
    end = int(short) if len(short) == 4 else (start // 100) * 100 + int(short)
    if end != start + 1:
        raise ValueError("season end year must equal start year + 1")
    return make_season_label(start)


def season_label_for_date(
    value: date | datetime,
    *,
    timezone_name: str = DEFAULT_DISPLAY_TIMEZONE,
    active_season: SeasonLabel | None = None,
) -> SeasonLabel:
    """Map a date to an NBA cross-year season.

    October–June belongs to the season spanning those years.  During the
    July–September offseason, an explicitly supplied official active season is
    preferred; absent that signal, the upcoming season is returned (the public
    UI can label it as “即将开始”).
    """

    local_day = _as_local_date(value, timezone_name)
    if active_season is not None:
        return active_season
    if local_day.month >= 10:
        return make_season_label(local_day.year)
    if local_day.month <= 6:
        return make_season_label(local_day.year - 1)
    return make_season_label(local_day.year)


def current_season(
    clock: Clock | None = None,
    *,
    timezone_name: str = DEFAULT_DISPLAY_TIMEZONE,
    active_season: SeasonLabel | None = None,
) -> SeasonLabel:
    return season_label_for_date(
        now_utc(clock), timezone_name=timezone_name, active_season=active_season
    )


def previous_completed_season(
    clock: Clock | None = None,
    *,
    timezone_name: str = DEFAULT_DISPLAY_TIMEZONE,
) -> SeasonLabel:
    current = current_season(clock, timezone_name=timezone_name)
    return make_season_label(current.start_year - 1)


def resolve_season_phrase(
    phrase: str,
    clock: Clock | None = None,
    *,
    timezone_name: str = DEFAULT_DISPLAY_TIMEZONE,
) -> SeasonLabel | None:
    """Resolve common Chinese season phrases, returning ``None`` if unknown."""

    text = phrase.strip()
    if not text:
        return None
    if any(token in text for token in ("本赛季", "当前赛季", "今年")):
        return current_season(clock, timezone_name=timezone_name)
    if "上赛季" in text:
        return previous_completed_season(clock, timezone_name=timezone_name)
    # A full calendar date such as ``2026-06-12`` starts with the same
    # ``YYYY-MM`` shape as a season label.  Require a boundary after the
    # candidate season so the month/day suffix cannot be mistaken for a
    # season end year (which would otherwise raise from ``parse_season_label``).
    match = re.search(
        r"(?<!\d)\d{4}[-/]\d{2,4}(?:赛季)?(?!\d)(?![-/]\d{1,2}(?:日)?)",
        text,
    )
    return parse_season_label(match.group(0)) if match else None


def resolve_relative_date(
    phrase: str,
    clock: Clock | None = None,
    *,
    timezone_name: str = DEFAULT_INPUT_TIMEZONE,
) -> date | None:
    """Resolve explicit dates and a small, deterministic relative vocabulary."""

    text = phrase.strip()
    if not text:
        return None
    match = re.search(r"(?<!\d)(\d{4})[-年/.](\d{1,2})[-月/.](\d{1,2})日?", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError as exc:
            raise ValueError("invalid calendar date") from exc
    base = to_timezone(now_utc(clock), timezone_name).date()
    if any(token in text for token in ("今天", "今日")):
        return base
    if any(token in text for token in ("昨天", "昨日")):
        return base - timedelta(days=1)
    if "前天" in text:
        return base - timedelta(days=2)
    if any(token in text for token in ("明天", "明日")):
        return base + timedelta(days=1)
    return None


def resolve_relative_date_range(
    phrase: str,
    clock: Clock | None = None,
    *,
    timezone_name: str = DEFAULT_INPUT_TIMEZONE,
) -> DateRange | None:
    """Resolve date expressions that may span more than one local day.

    ``resolve_relative_date`` intentionally keeps its historical single-day
    contract. Schedule questions, however, commonly use ``本周``/``下周`` or
    ``未来 3 天``; leaving those phrases unresolved makes the planner search
    the entire provider archive. This companion keeps the old API stable
    while giving the parser an exact, half-open local-date interval.
    """

    text = " ".join(str(phrase or "").strip().split()).lower()
    if not text:
        return None
    single = resolve_relative_date(text, clock, timezone_name=timezone_name)
    if single is not None:
        return local_date_range(single, timezone_name)
    base = to_timezone(now_utc(clock), timezone_name).date()
    if re.search(r"(?:^|\s)(?:本周|这周|this week)(?:\s|$)|本周|这周", text):
        start = base - timedelta(days=base.weekday())
        return DateRange(
            start_inclusive=local_date_range(start, timezone_name).start_inclusive,
            end_exclusive=local_date_range(
                start + timedelta(days=7), timezone_name
            ).start_inclusive,
        )
    if re.search(r"(?:下周|下个星期|next week)", text):
        start = base + timedelta(days=7 - base.weekday())
        return DateRange(
            start_inclusive=local_date_range(start, timezone_name).start_inclusive,
            end_exclusive=local_date_range(
                start + timedelta(days=7), timezone_name
            ).start_inclusive,
        )
    match = re.search(r"(?:未来|接下来)\s*(\d{1,2})\s*天", text)
    if match:
        days = int(match.group(1))
        if not 1 <= days <= 31:
            raise ValueError("date range must contain 1..31 days")
        return DateRange(
            start_inclusive=local_date_range(base, timezone_name).start_inclusive,
            end_exclusive=local_date_range(
                base + timedelta(days=days), timezone_name
            ).start_inclusive,
        )
    return None


def build_time_context(
    instant: datetime,
    *,
    input_timezone: str = DEFAULT_INPUT_TIMEZONE,
    display_timezone: str = DEFAULT_DISPLAY_TIMEZONE,
    season: SeasonLabel | None = None,
    relative_phrase: str | None = None,
) -> TimeContext:
    return TimeContext(
        instant_utc=to_utc(instant),
        input_timezone=input_timezone,
        display_timezone=display_timezone,
        season=season,
        relative_phrase=relative_phrase,
    )


def game_end_window(seconds: int | float = 5) -> TimeWindow:
    if seconds < 0 or seconds > 60:
        raise ValueError("seconds must be between 0 and 60")
    return TimeWindow(
        start_seconds=0,
        end_seconds=seconds,
        scope=TimeWindowScope.GAME_END,
    )


def period_end_window(
    seconds: int | float = 5,
    *,
    all_periods: bool = False,
) -> TimeWindow:
    """Build a window relative to the end of a period.

    ``all_periods=True`` is reserved for an explicit “每节/各节” request.
    It is opt-in so an ordinary period query that omits its period remains a
    validation error instead of silently broadening its scope.
    """
    if seconds < 0 or seconds > 60:
        raise ValueError("seconds must be between 0 and 60")
    return TimeWindow(
        start_seconds=0,
        end_seconds=seconds,
        scope=TimeWindowScope.PERIOD_END,
        all_periods=all_periods,
    )


def order_pbp_events(
    events: Iterable[PlayEvent],
    *,
    sequence_valid: bool,
) -> list[PlayEvent]:
    """Apply the canonical PBP ordering/tie-break policy."""

    values = list(events)
    if sequence_valid:
        if any(event.sequence is None for event in values):
            raise ValueError("sequence_valid requires sequence on every event")
        return sorted(
            values,
            key=lambda event: (event.period, int(event.sequence or 0), event.provider_index),
        )
    return sorted(
        values,
        key=lambda event: (
            event.period,
            -float(event.clock_seconds_remaining),
            event.provider_index,
        ),
    )


def select_pbp_window(
    bundle: PlayByPlayBundle,
    window: TimeWindow,
    *,
    period: int | None = None,
) -> list[PlayEvent]:
    """Select and order events from a complete PBP bundle.

    ``GAME_END`` always chooses the highest period present in the *complete*
    bundle, including overtime.  ``PERIOD_END`` requires an explicit period
    unless the typed window carries ``all_periods=True`` (the explicit
    “每节/各节” form); silently falling back to the previous period would
    produce a false answer.  Both endpoints of the clock interval are
    inclusive.
    """

    if not bundle.events:
        return []
    if window.scope is TimeWindowScope.GAME_END:
        target_period = max(event.period for event in bundle.events)
    else:
        all_periods = bool(getattr(window, "all_periods", False))
        if period is None and not all_periods:
            raise ValueError("PERIOD_END requires a positive period")
        if period is not None and period < 1:
            raise ValueError("PERIOD_END requires a positive period")
        # An explicit period still wins if a caller supplies one alongside an
        # all-period window (e.g. when rendering one tab of a multi-period
        # result).  Otherwise retain every distinct period and sort globally
        # using the canonical tie-break policy below.
        target_period = period
    selected = [
        event
        for event in bundle.events
        if (target_period is None or event.period == target_period)
        and window.start_seconds <= event.clock_seconds_remaining <= window.end_seconds
    ]
    return order_pbp_events(selected, sequence_valid=bundle.sequence_valid)


# Descriptive aliases used by callers/tests.
filter_pbp_events = select_pbp_window
select_last_seconds = select_pbp_window


__all__ = [
    "BEIJING",
    "Clock",
    "DEFAULT_DISPLAY_TIMEZONE",
    "DEFAULT_INPUT_TIMEZONE",
    "FixedClock",
    "SeasonClock",
    "SystemClock",
    "UTC",
    "beijing_date_range",
    "build_time_context",
    "current_season",
    "date_range_for_local_date",
    "ensure_aware",
    "filter_pbp_events",
    "format_beijing",
    "game_end_window",
    "local_date_range",
    "make_season_label",
    "now_utc",
    "order_pbp_events",
    "parse_season_label",
    "period_end_window",
    "previous_completed_season",
    "resolve_relative_date",
    "resolve_relative_date_range",
    "resolve_season_phrase",
    "season_label_for_date",
    "select_last_seconds",
    "select_pbp_window",
    "to_beijing",
    "to_timezone",
    "to_utc",
    "validate_timezone",
]
