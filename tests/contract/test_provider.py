"""Contract checks for the allow-listed public-data adapter.

The tests use an ``httpx`` mock transport, so they never contact the public
Internet.  They intentionally exercise the provider boundary rather than the
chat renderer: raw ESPN-shaped fields must become canonical objects and
failure responses must be typed/safe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from apps.api.src.application.ports import RequestBudget
from apps.api.src.domain.errors import ProviderErrorKind
from apps.api.src.domain.models import (
    EntityKind,
    GameFilters,
    PlayEventType,
    ShotType,
)
from apps.api.src.domain.time_policy import beijing_date_range
from apps.api.src.providers.espn_adapter import ESPNAdapter


def _budget(seconds: int = 5) -> RequestBudget:
    return RequestBudget(datetime.now(UTC) + timedelta(seconds=seconds))


def _scoreboard() -> dict:
    return {
        "events": [
            {
                "id": "401-test",
                "date": "2026-06-12T01:30:00Z",
                "season": {"year": 2026, "displayName": "2025-26"},
                "status": {"type": {"state": "post", "completed": True}},
                "competitions": [
                    {
                        "venue": {
                            "fullName": "TD Garden",
                            "address": {
                                "city": "Boston",
                                "state": "MA",
                                "country": "USA",
                            },
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "108",
                                "team": {
                                    "id": "2",
                                    "displayName": "Boston Celtics",
                                    "abbreviation": "BOS",
                                },
                            },
                            {
                                "homeAway": "away",
                                "score": "104",
                                "team": {
                                    "id": "25",
                                    "displayName": "Oklahoma City Thunder",
                                    "abbreviation": "OKC",
                                },
                            },
                        ],
                        "series": {"id": "series-test", "gameNumber": 4},
                        "notes": [{"headline": "NBA Finals - Game 4"}],
                    }
                ],
            }
        ]
    }


def _summary() -> dict:
    event = _scoreboard()["events"][0]
    return {
        "header": {
            "id": event["id"],
            "date": event["date"],
            "season": event["season"],
            "status": event["status"],
            "competitions": event["competitions"],
        },
        "boxscore": {
            "players": [
                {
                    "statistics": [
                        {
                            "labels": ["MIN", "PTS", "REB", "AST"],
                            "athletes": [
                                {
                                    "athlete": {"id": "1", "displayName": "Jaylen Brown"},
                                    "stats": ["36", "32", "8", "5"],
                                }
                            ],
                        }
                    ]
                }
            ]
        },
        "plays": [
            {
                "id": "play-1",
                "sequenceNumber": 1,
                "period": {"number": 4},
                "clock": {"displayValue": "0:05.0"},
                "type": {"text": "Free Throw"},
                "text": "Jaylen Brown free throw made",
                "participants": [
                    {
                        "athlete": {"id": "1", "displayName": "Jaylen Brown"},
                        "type": "shooter",
                    }
                ],
                "scoringPlay": True,
                "scoreValue": 1,
                "homeScore": 108,
                "awayScore": 104,
            }
        ],
    }


@pytest.mark.asyncio
async def test_scoreboard_and_summary_are_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json=_scoreboard())
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=_summary())
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ESPNAdapter(client=client)
    try:
        result = await adapter.search_games(
            GameFilters(date_range=beijing_date_range(datetime(2026, 6, 12, tzinfo=UTC))),
            _budget(),
        )
        assert result.error is None
        assert len(result.data or []) == 1
        game = result.data[0]
        assert game.status.value == "FINAL"
        assert game.home.kind is EntityKind.TEAM
        assert game.series_game_number == 4
        assert game.venue is not None
        assert game.venue.name == "TD Garden"
        assert game.venue.city == "Boston"
        assert game.venue.state == "MA"
        assert game.venue.country == "USA"
        assert result.evidence[0].source_class.value == "ESTABLISHED_SPORTS"

        summary = await adapter.get_game_summary("401-test", _budget())
        assert summary.error is None
        assert summary.data is not None
        assert summary.data.game.status.value == "FINAL"
        assert summary.data.game.venue is not None
        assert summary.data.game.venue.name == "TD Garden"
        assert summary.data.leaders[0].metrics["points"] == 32
        assert summary.data.plays is not None
        assert summary.data.plays.events[0].event_type is PlayEventType.FREE_THROW
        assert summary.data.plays.events[0].shot_type is ShotType.FREE_THROW
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_adapter_maps_http_failures_without_leaking_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"}, text="provider details")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ESPNAdapter(client=client)
    try:
        result = await adapter.get_play_by_play("401-test", _budget())
        assert result.data is None
        assert result.error is not None
        assert result.error.kind is ProviderErrorKind.RATE_LIMITED
        assert result.error.retryable is True
        assert result.error.retry_after_seconds == 7
        assert "provider details" not in result.error.safe_message
    finally:
        await client.aclose()


def test_adapter_rejects_unallowlisted_or_credentialed_base_url() -> None:
    with pytest.raises(ValueError):
        ESPNAdapter(base_url="http://site.api.espn.com/apis/site/v2")
    with pytest.raises(ValueError):
        ESPNAdapter(base_url="https://evil.example/nba", allowed_hosts=("site.api.espn.com",))
    with pytest.raises(ValueError):
        ESPNAdapter(base_url="https://site.api.espn.com/nba?token=secret")


def test_adapter_accepts_singleton_leader_and_play_mappings() -> None:
    """Provider payload wrappers may collapse one-item arrays to objects."""

    adapter = ESPNAdapter()
    leader = adapter._leaders(
        {
            "leaders": {
                "name": "points",
                "leaders": {
                    "athlete": {"id": "1", "displayName": "Jaylen Brown"},
                    "value": "32",
                },
            }
        },
        [],
        "game-1",
        evidence_id="evidence-1",
    )
    assert len(leader) == 1
    assert leader[0].metrics["points"] == 32

    play = {
        "id": "play-1",
        "period": {"number": 4},
        "clock": {"displayValue": "0:05"},
        "text": "Jaylen Brown free throw made",
    }
    assert adapter._play_values({"plays": play}) == [play]
    assert adapter._play_values({"gamepackageJSON": {"plays": play}}) == [play]
    assert adapter._play_values({"content": {"plays": play}}) == [play]
