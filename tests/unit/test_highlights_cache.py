from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from apps.api.src.api.schemas import (
    HighlightDetailResponse,
    HighlightGame,
    HighlightLeader,
    HighlightPlay,
    HighlightsRangeResponse,
)
from apps.api.src.infrastructure.highlights_cache import SQLiteHighlightsCache, stable_cache_key

NOW = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)


def _game(*, game_id: str = "g1", status: str = "final") -> HighlightGame:
    return HighlightGame(
        game_id=game_id,
        start_utc=datetime(2026, 6, 12, 1, 30, tzinfo=UTC),
        home_name="凯尔特人",
        home_abbreviation="BOS",
        away_name="雷霆",
        away_abbreviation="OKC",
        status=status,
        home_score=108 if status == "final" else None,
        away_score=104 if status == "final" else None,
        series_game_number=4,
        venue_name="TD Garden",
        venue_city="Boston",
    )


def _recent(*, game_id: str = "g1") -> HighlightsRangeResponse:
    return HighlightsRangeResponse(
        timezone="Asia/Shanghai",
        **{"from": "2026-05-05", "to": "2026-09-01"},
        games=[_game(game_id=game_id)],
        as_of_beijing="2026-09-01 09:00",
        evidence_state="verified",
    )


def _detail(*, plays: int = 2, home_score: int = 108) -> HighlightDetailResponse:
    game = _game().model_copy(update={"home_score": home_score})
    return HighlightDetailResponse(
        game=game,
        leaders=[
            HighlightLeader(player_name="杰伦·布朗", points=32, rebounds=8, assists=5)
        ],
        plays=[
            HighlightPlay(
                period=4,
                clock=f"0:{5 - index:02d}",
                team="BOS",
                player_name="杰伦·布朗",
                action="投篮命中",
                detail="关键回合",
                home_score=107 + index,
                away_score=104,
            )
            for index in range(plays)
        ],
        as_of_beijing="2026-09-01 09:00",
        evidence_state="verified",
    )


def test_stable_cache_key_is_bounded_and_rejects_unvalidated_parts() -> None:
    assert stable_cache_key("recent", "Asia/Shanghai", "2026-09-01", 5) == (
        "recent:v2:Asia/Shanghai:2026-09-01:5"
    )
    try:
        stable_cache_key("recent", "bad\nvalue")
    except ValueError as exc:
        assert "cache key" in str(exc)
    else:
        raise AssertionError("control characters must be rejected")


def test_typed_round_trip_and_stale_policy(tmp_path) -> None:
    cache = SQLiteHighlightsCache(tmp_path / "highlights.sqlite3")
    key = stable_cache_key("recent", "Asia/Shanghai", "2026-09-01", 5)

    assert cache.set(key, "recent", _recent(), ttl_seconds=60, now=NOW)
    fresh = cache.get(key, HighlightsRangeResponse, now=NOW + timedelta(seconds=59))
    assert fresh is not None and fresh.stale is False
    assert fresh.value.games[0].home_score == 108
    assert fresh.value.games[0].venue_name == "TD Garden"
    assert fresh.value.games[0].venue_city == "Boston"
    assert cache.get(key, HighlightsRangeResponse, now=NOW + timedelta(seconds=61)) is None
    stale = cache.get(
        key,
        HighlightsRangeResponse,
        now=NOW + timedelta(seconds=61),
        allow_stale=True,
    )
    assert stale is not None and stale.stale is True


def test_internal_cache_probe_does_not_inflate_foreground_hit_metrics(tmp_path) -> None:
    cache = SQLiteHighlightsCache(tmp_path / "highlights.sqlite3")
    key = stable_cache_key("recent", "Asia/Shanghai", "2026-09-01", 5)
    assert cache.set(key, "recent", _recent(), ttl_seconds=1, now=NOW)

    hit = cache.get(
        key,
        HighlightsRangeResponse,
        now=NOW + timedelta(seconds=2),
        allow_stale=True,
        count_metrics=False,
    )

    assert hit is not None and hit.stale is True
    counters = cache.counters()
    assert counters["persistent_cache_read_count"] == 0
    assert counters["persistent_cache_hit_count"] == 0
    assert counters["persistent_cache_stale_hit_count"] == 0


def test_cache_persists_across_reopen(tmp_path) -> None:
    path = tmp_path / "highlights.sqlite3"
    key = stable_cache_key("recent", "Asia/Shanghai", "2026-09-01", 5)
    first = SQLiteHighlightsCache(path)
    assert first.set(key, "recent", _recent(), ttl_seconds=60, now=NOW)
    first.close()

    second = SQLiteHighlightsCache(path)
    hit = second.get(key, HighlightsRangeResponse, now=NOW + timedelta(seconds=1))
    assert hit is not None
    assert hit.value.games[0].game_id == "g1"


def test_corrupt_row_is_isolated_and_treated_as_miss(tmp_path) -> None:
    path = tmp_path / "highlights.sqlite3"
    key = stable_cache_key("recent", "Asia/Shanghai", "2026-09-01", 5)
    cache = SQLiteHighlightsCache(path)
    assert cache.set(key, "recent", _recent(), ttl_seconds=60, now=NOW)
    cache.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE highlights_cache SET payload_json = ? WHERE cache_key = ?",
            ("{not-json", key),
        )
        connection.commit()

    reopened = SQLiteHighlightsCache(path)
    assert reopened.get(key, HighlightsRangeResponse, now=NOW) is None
    assert reopened.count() == 0
    assert reopened.counters()["persistent_cache_error_count"] == 1


def test_payload_and_entry_limits_are_enforced(tmp_path) -> None:
    too_small = SQLiteHighlightsCache(
        tmp_path / "small.sqlite3", max_payload_bytes=128
    )
    key = stable_cache_key("recent", "Asia/Shanghai", "2026-09-01", 5)
    assert not too_small.set(key, "recent", _recent(), ttl_seconds=60, now=NOW)

    bounded = SQLiteHighlightsCache(tmp_path / "bounded.sqlite3", max_entries=2)
    for index in range(3):
        assert bounded.set(
            stable_cache_key("recent", "Asia/Shanghai", f"2026-09-0{index + 1}", 5),
            "recent",
            _recent(game_id=f"g{index}"),
            ttl_seconds=60,
            now=NOW + timedelta(seconds=index),
        )
    assert bounded.count() == 2


def test_refresh_lease_coalesces_and_expires(tmp_path) -> None:
    cache = SQLiteHighlightsCache(tmp_path / "lease.sqlite3", lease_seconds=30)
    key = stable_cache_key("recent", "Asia/Shanghai", "2026-09-01", 5)

    token = cache.acquire_refresh(key, now=NOW)
    assert token
    assert cache.acquire_refresh(key, now=NOW + timedelta(seconds=1)) is None
    replacement = cache.acquire_refresh(key, now=NOW + timedelta(seconds=31))
    assert replacement and replacement != token
    assert not cache.release_refresh(key, token)
    assert cache.release_refresh(key, replacement)


def test_detail_completeness_cannot_move_backwards_or_change_final_score(tmp_path) -> None:
    cache = SQLiteHighlightsCache(tmp_path / "detail.sqlite3")
    key = stable_cache_key("detail", "Asia/Shanghai", "g1")
    assert cache.set(key, "detail", _detail(plays=2), ttl_seconds=60, now=NOW)
    assert not cache.set(
        key,
        "detail",
        _detail(plays=0),
        ttl_seconds=60,
        now=NOW + timedelta(seconds=1),
    )
    assert not cache.set(
        key,
        "detail",
        _detail(plays=3, home_score=109),
        ttl_seconds=60,
        now=NOW + timedelta(seconds=2),
    )

    hit = cache.get(key, HighlightDetailResponse, now=NOW + timedelta(seconds=3))
    assert hit is not None
    assert hit.value.game.home_score == 108
    assert len(hit.value.plays) == 2
    assert cache.counters()["persistent_cache_rejected_write_count"] == 2


def test_unwritable_database_fails_open(tmp_path) -> None:
    cache = SQLiteHighlightsCache(tmp_path / "missing" / "nested" / "highlights.sqlite3")
    # Parent directories are deliberately not auto-created by the storage
    # object; application startup owns the configured mount point.
    assert cache.available is False
    assert cache.get("recent:v2:x", HighlightsRangeResponse, now=NOW) is None
    assert not cache.set(
        "recent:v2:x", "recent", _recent(), ttl_seconds=60, now=NOW
    )


def test_previous_projection_schema_key_is_never_rehydrated(tmp_path) -> None:
    path = tmp_path / "highlights.sqlite3"
    cache = SQLiteHighlightsCache(path)
    current_key = stable_cache_key("recent", "Asia/Shanghai", "2026-09-01", 5)
    assert cache.set(current_key, "recent", _recent(), ttl_seconds=60, now=NOW)
    cache.close()

    previous_key = current_key.replace("recent:v2:", "recent:v1:", 1)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE highlights_cache SET cache_key = ?, schema_version = 1 WHERE cache_key = ?",
            (previous_key, current_key),
        )
        connection.commit()

    reopened = SQLiteHighlightsCache(path)
    assert reopened.get(current_key, HighlightsRangeResponse, now=NOW) is None
    assert reopened.get(previous_key, HighlightsRangeResponse, now=NOW) is None
