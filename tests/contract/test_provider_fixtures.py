"""Versioned ESPN-shaped fixture contract coverage."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from apps.api.src.application.ports import RequestBudget
from apps.api.src.domain.models import GameFilters
from apps.api.src.domain.time_policy import beijing_date_range
from apps.api.src.providers.espn_adapter import ESPNAdapter

FIXTURES = Path(__file__).parents[2] / "apps/api/src/providers/fixtures/espn"


def _budget() -> RequestBudget:
    return RequestBudget(datetime.now(UTC) + timedelta(seconds=5), max_provider_operations=4)


@pytest.mark.asyncio
async def test_versioned_espn_snapshots_map_schedule_summary_and_pbp() -> None:
    scoreboard = json.loads((FIXTURES / "scoreboard.json").read_text(encoding="utf-8"))
    summary = json.loads((FIXTURES / "summary.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json=scoreboard)
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=summary)
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ESPNAdapter(client=client)
    try:
        games = await adapter.search_games(
            GameFilters(date_range=beijing_date_range(datetime(2026, 6, 12, tzinfo=UTC))),
            _budget(),
        )
        assert games.error is None and len(games.data or []) == 1
        assert games.data[0].game_id == "401-fixture-g4"
        assert games.data[0].home_score == 108 and games.data[0].away_score == 104

        bundle = await adapter.get_game_summary("401-fixture-g4", _budget())
        assert bundle.error is None and bundle.data is not None
        assert bundle.data.leaders[0].metrics["points"] == 32
        assert bundle.data.plays is not None
        assert bundle.data.plays.events[0].points == 1
    finally:
        await client.aclose()


def test_espn_snapshot_is_versionable_and_not_empty() -> None:
    payload = json.loads((FIXTURES / "scoreboard.json").read_text(encoding="utf-8"))
    assert payload["events"] and payload["events"][0]["id"].startswith("401-")
