"""Deterministic tests for timezone, season, and PBP policy."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from apps.api.src.domain.models import (
    PlayByPlayBundle,
    PlayEvent,
    PlayEventType,
)
from apps.api.src.domain.time_policy import (
    FixedClock,
    beijing_date_range,
    current_season,
    format_beijing,
    game_end_window,
    local_date_range,
    make_season_label,
    order_pbp_events,
    parse_season_label,
    period_end_window,
    previous_completed_season,
    resolve_relative_date,
    resolve_relative_date_range,
    season_label_for_date,
    select_pbp_window,
    to_utc,
)


def _event(
    event_id: str,
    clock: str,
    *,
    period: int = 4,
    sequence: int | None = None,
    provider_index: int = 0,
) -> PlayEvent:
    return PlayEvent(
        event_id=event_id,
        game_id="g1",
        sequence=sequence,
        provider_index=provider_index,
        period=period,
        clock_seconds_remaining=Decimal(clock),
        event_type=PlayEventType.SHOT,
    )


def test_beijing_local_date_uses_half_open_utc_interval() -> None:
    result = beijing_date_range(date(2026, 6, 12))
    assert result.start_inclusive == datetime(2026, 6, 11, 16, tzinfo=UTC)
    assert result.end_exclusive == datetime(2026, 6, 12, 16, tzinfo=UTC)
    # A UTC instant near midnight belongs to the following Beijing calendar day.
    assert (
        local_date_range(datetime(2026, 6, 11, 16, tzinfo=UTC)).start_inclusive
        == result.start_inclusive
    )


def test_timezone_conversion_rejects_naive_and_formats_beijing() -> None:
    instant = datetime(2026, 6, 11, 16, 5, 9, tzinfo=UTC)
    assert to_utc(instant) == instant
    assert format_beijing(instant) == "2026-06-12 00:05"
    with pytest.raises(ValueError):
        to_utc(datetime(2026, 6, 12, 0, 0))


def test_season_boundaries_and_offseason_policy() -> None:
    assert season_label_for_date(date(2025, 10, 1)) == make_season_label(2025)
    assert season_label_for_date(date(2026, 6, 30)) == make_season_label(2025)
    # July–September is the offseason: default to the upcoming season.
    assert season_label_for_date(date(2026, 8, 1)) == make_season_label(2026)
    clock = FixedClock(datetime(2026, 8, 27, 12, tzinfo=UTC))
    assert current_season(clock) == make_season_label(2026)
    assert previous_completed_season(clock) == make_season_label(2025)


def test_parse_and_resolve_relative_season_dates() -> None:
    assert parse_season_label("2025-26") == make_season_label(2025)
    assert parse_season_label("2025/2026") == make_season_label(2025)
    clock = FixedClock(datetime(2026, 6, 12, 12, tzinfo=UTC))
    assert resolve_relative_date("今天的比赛", clock) == date(2026, 6, 12)
    assert resolve_relative_date("昨天赛果", clock) == date(2026, 6, 11)
    assert resolve_relative_date("2025-10-31 赛程", clock) == date(2025, 10, 31)
    assert resolve_relative_date("下周", clock) is None


def test_schedule_relative_ranges_are_half_open_and_timezone_aware() -> None:
    clock = FixedClock(datetime(2026, 8, 30, 10, tzinfo=UTC))  # Sunday Beijing
    next_week = resolve_relative_date_range("下周有比赛吗", clock)
    assert next_week is not None
    assert next_week.start_inclusive == datetime(2026, 8, 30, 16, tzinfo=UTC)
    assert next_week.end_exclusive == datetime(2026, 9, 6, 16, tzinfo=UTC)
    future = resolve_relative_date_range("未来 3 天有比赛吗", clock)
    assert future is not None
    assert future.start_inclusive == datetime(2026, 8, 29, 16, tzinfo=UTC)
    assert future.end_exclusive == datetime(2026, 9, 1, 16, tzinfo=UTC)


def test_game_end_window_chooses_overtime_final_period() -> None:
    events = [
        _event("r4", "4", period=4, sequence=10, provider_index=0),
        _event("ot6", "6", period=5, sequence=20, provider_index=1),
        _event("ot5", "5", period=5, sequence=21, provider_index=2),
        _event("ot0", "0", period=5, sequence=22, provider_index=3),
    ]
    bundle = PlayByPlayBundle(game_id="g1", events=events, sequence_valid=True)
    selected = select_pbp_window(bundle, game_end_window(5))
    assert [event.event_id for event in selected] == ["ot5", "ot0"]


def test_period_end_requires_period_and_includes_both_clock_endpoints() -> None:
    events = [
        _event("p6", "6", sequence=None, provider_index=0),
        _event("p5", "5", sequence=None, provider_index=1),
        _event("p0", "0", sequence=None, provider_index=2),
    ]
    bundle = PlayByPlayBundle(game_id="g1", events=events, sequence_valid=False)
    selected = select_pbp_window(bundle, period_end_window(5), period=4)
    assert [event.event_id for event in selected] == ["p5", "p0"]
    with pytest.raises(ValueError):
        select_pbp_window(bundle, period_end_window(5))


def test_all_period_window_selects_each_period_without_cross_period_fallback() -> None:
    events = [
        _event("q1-5", "5", period=1, sequence=1, provider_index=0),
        _event("q1-0", "0", period=1, sequence=2, provider_index=1),
        _event("q2-4", "4", period=2, sequence=3, provider_index=2),
        _event("q4-5", "5", period=4, sequence=4, provider_index=3),
        _event("ot-0", "0", period=5, sequence=5, provider_index=4),
    ]
    bundle = PlayByPlayBundle(game_id="g1", events=events, sequence_valid=True)
    selected = select_pbp_window(bundle, period_end_window(5, all_periods=True))
    assert [event.event_id for event in selected] == [
        "q1-5",
        "q1-0",
        "q2-4",
        "q4-5",
        "ot-0",
    ]
    # Supplying a tab/period still narrows an all-period window explicitly.
    assert [
        event.event_id
        for event in select_pbp_window(bundle, period_end_window(5, all_periods=True), period=2)
    ] == ["q2-4"]


def test_sequence_invalid_order_uses_clock_then_provider_index() -> None:
    events = [
        _event("late", "1", provider_index=2),
        _event("early", "5", provider_index=1),
        _event("tie", "5", provider_index=0),
    ]
    ordered = order_pbp_events(events, sequence_valid=False)
    assert [event.event_id for event in ordered] == ["tie", "early", "late"]
