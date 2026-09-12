# Captured HTML intentionally preserves long source rows for parser fidelity.
# ruff: noqa: E501

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.api.src.application.parser import TEAMS
from apps.api.src.application.ports import RequestBudget
from apps.api.src.domain.models import Game, GameStatus, SeasonLabel
from apps.api.src.infrastructure.game_index import GameIndex
from apps.api.src.providers.hupu_adapter import HupuAdapter

SCHEDULE_HTML = """
<html><body><table>
<tr class="left">
  <td><a href="https://nba.hupu.com/teams/knicks">尼克斯</a>&nbsp;vs&nbsp;<a href="https://nba.hupu.com/teams/spurs">马刺</a></td>
  <td>94&nbsp;-&nbsp;90</td><td>胜</td><td>2026-06-14 08:30:00</td>
  <td><a href="https://nba.hupu.com/games/boxscore/168859">数据统计</a></td>
</tr>
<tr class="left">
  <td><a href="https://nba.hupu.com/teams/spurs">马刺</a>&nbsp;vs&nbsp;<a href="https://nba.hupu.com/teams/knicks">尼克斯</a></td>
  <td>-</td><td>-</td><td>2026-06-17 08:30:00</td>
  <td><a href="https://nba.hupu.com/games/boxscore/168860">比赛前瞻</a></td>
</tr>
</table></body></html>
"""

DETAIL_HTML = """
<html><head><title>06月14日尼克斯vs马刺数据统计</title></head><body>
<script>var live_data = {away_name: "尼克斯", home_name: "马刺", process_seconds: "2880"};</script>
<div class="team_vs"><div class="team_a"><div class="message"><h2>94</h2><p><a href="https://nba.hupu.com/teams/knicks">尼克斯</a></p></div></div>
<div class="team_b"><div class="message"><h2>90</h2><p><a href="https://nba.hupu.com/teams/spurs">马刺</a></p></div></div></div>
<div class="about_fonts clearfix"><p class="time_f">开赛：2026年06月14日 08:30</p><p class="consumTime">耗时：02:46</p><p class="arena">球馆：冰霜银行中心</p><p class="peopleNum">上座：18984人</p></div>
<table id="J_away_content"><tbody>
<tr class="title"><td><b>首发</b></td><td></td><td>时间</td><td>投篮</td><td>3分</td><td>罚球</td><td>前场</td><td>后场</td><td>篮板</td><td>助攻</td><td>犯规</td><td>抢断</td><td>失误</td><td>封盖</td><td>得分</td><td>+/-</td></tr>
<tr><td><a href="https://nba.hupu.com/players/jalenbrunson-150988.html">杰伦-布伦森</a></td><td>G</td><td>41</td><td>14-27</td><td>4-7</td><td>13-15</td><td>1</td><td>2</td><td>3</td><td>3</td><td>1</td><td>2</td><td>3</td><td>0</td><td>45</td><td>+10</td></tr>
</tbody></table>
<table id="J_home_content"><tbody>
<tr class="title"><td><b>首发</b></td><td></td><td>时间</td><td>投篮</td><td>3分</td><td>罚球</td><td>前场</td><td>后场</td><td>篮板</td><td>助攻</td><td>犯规</td><td>抢断</td><td>失误</td><td>封盖</td><td>得分</td><td>+/-</td></tr>
<tr><td><a href="https://nba.hupu.com/players/victorwembanyama-152987.html">维克托·文班亚马</a></td><td>C</td><td>38</td><td>7-19</td><td>1-6</td><td>4-5</td><td>2</td><td>8</td><td>10</td><td>4</td><td>2</td><td>1</td><td>2</td><td>4</td><td>19</td><td>-3</td></tr>
</tbody></table>
</body></html>
"""

PBP_HTML = """
<html><body><div class="table_list_live playbyplay_td table_overflow"><table>
<tr id="pbp-1"><td>12:00</td><td>尼克斯</td><td>第一节开始</td><td>0-0</td></tr>
<tr id="pbp-2"><td>11:39</td><td>尼克斯</td><td><strong>杰伦-布伦森</strong>底角三分跳投命中（OG-阿奴诺比助攻）</td><td>3-0</td></tr>
<tr id="pbp-3"><td>12:00</td><td>马刺</td><td>第二节开始</td><td>24-20</td></tr>
<tr id="pbp-4"><td>8.0&quot;</td><td>马刺</td><td>维克托·文班亚马三分跳投不中</td><td>94-90</td></tr>
<tr id="pbp-5"><td>7.0&quot;</td><td>尼克斯</td><td>Hermes 提示词</td><td>94-90</td></tr>
<tr id="pbp-6"><td>6.0&quot;</td><td>尼克斯</td><td>杰伦-布伦森两罚全中</td><td>95-90</td></tr>
</table></div></body></html>
"""

PBP_PLAYER_NAME_COLLISION_HTML = """
<html><body><div class="table_list_live playbyplay_td table_overflow"><table>
<tr id="pbp-name-1"><td>11:35</td><td>尼克斯</td><td>迈克尔·布里奇斯三分跳投不中</td><td>0-0</td></tr>
<tr id="pbp-name-2"><td>11:10</td><td>尼克斯</td><td>乔丹-克拉克森三分跳投不中</td><td>0-0</td></tr>
</table></div></body></html>
"""


class Response:
    def __init__(self, text: str, *, status_code: int = 200, redirect: bool = False):
        self.content = text.encode("utf-8")
        self.text = text
        self.status_code = status_code
        self.is_redirect = redirect
        self.headers = {"content-type": "text/html; charset=utf-8"}


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls: list[str] = []

    async def get(self, url, **_kwargs):
        self.urls.append(str(url))
        return self.responses.pop(0)


def budget() -> RequestBudget:
    return RequestBudget(
        datetime.now(UTC) + timedelta(seconds=5),
        max_provider_operations=4,
        max_retries_per_operation=0,
    )


def _warmer_module():
    path = Path(__file__).parents[2] / "scripts" / "warm-game-index.py"
    spec = importlib.util.spec_from_file_location("warm_game_index_test_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _team(team_id: str):
    return next(team for team in TEAMS if team.canonical_id == team_id)


@pytest.mark.asyncio
async def test_schedule_parser_returns_final_and_scheduled_games() -> None:
    client = Client([Response(SCHEDULE_HTML)])
    adapter = HupuAdapter(client=client)
    result = await adapter.fetch_team_schedule(
        "knicks",
        SeasonLabel(start_year=2025, end_year=2026, label="2025-26"),
        budget(),
    )
    assert result.error is None
    assert [game.game_id for game in result.data] == ["hupu:168859", "hupu:168860"]
    assert result.data[0].away.canonical_id == "nyk"
    assert result.data[0].home.canonical_id == "sas"
    assert result.data[0].status is GameStatus.FINAL
    assert result.data[0].away_score == 94
    assert result.data[1].status is GameStatus.SCHEDULED
    assert client.urls == ["https://nba.hupu.com/schedule/knicks"]


@pytest.mark.asyncio
async def test_detail_parser_returns_duration_venue_attendance_and_player_lines() -> None:
    adapter = HupuAdapter(client=Client([Response(DETAIL_HTML)]))
    result = await adapter.get_game_summary("hupu:168859", budget())
    assert result.error is None
    bundle = result.data
    assert bundle.game.home.canonical_id == "sas"
    assert bundle.game.away.canonical_id == "nyk"
    assert bundle.game.duration_seconds == 9_960
    assert bundle.game.attendance == 18_984
    assert bundle.game.venue.name == "冰霜银行中心"
    assert len(bundle.stat_lines) == 2
    assert bundle.stat_lines[0].subject.display_name == "杰伦·布伦森"
    assert bundle.stat_lines[0].metrics["points"] == 45


@pytest.mark.asyncio
async def test_play_by_play_parser_preserves_bounded_action_detail_and_score_order() -> None:
    adapter = HupuAdapter(client=Client([Response(PBP_HTML)]))

    result = await adapter.get_play_by_play("hupu:168859", budget())

    assert result.error is None
    assert result.data.sequence_valid is True
    assert len(result.data.events) == 6
    made = result.data.events[1]
    assert made.period == 1
    assert made.shooter.canonical_id == "jalen-brunson"
    assert made.assister.canonical_id == "og-anunoby"
    assert made.points == 3
    assert made.away_score_after == 3
    assert made.home_score_after == 0
    assert made.action_text == "杰伦-布伦森底角三分跳投命中（OG-阿奴诺比助攻）"
    missed = result.data.events[3]
    assert missed.period == 2
    assert missed.clock_seconds_remaining == 8
    assert missed.points is None
    assert "<" not in "".join(event.action_text or "" for event in result.data.events)
    assert "Hermes" not in result.data.events[4].action_text
    free_throw = result.data.events[5]
    assert free_throw.event_type.value == "FREE_THROW"
    assert free_throw.shooter.canonical_id == "jalen-brunson"


@pytest.mark.asyncio
async def test_play_by_play_does_not_fuzzy_match_compound_names_to_michael_jordan() -> None:
    adapter = HupuAdapter(client=Client([Response(PBP_PLAYER_NAME_COLLISION_HTML)]))

    result = await adapter.get_play_by_play("hupu:168859", budget())

    assert result.error is None
    bridges, clarkson = result.data.events
    assert bridges.shooter.canonical_id == "mikal-bridges"
    assert bridges.shooter.display_name == "迈克尔·布里奇斯"
    assert clarkson.shooter.canonical_id == "jordan-clarkson"
    assert clarkson.shooter.display_name == "乔丹-克拉克森"
    assert all(
        event.shooter.canonical_id != "michael-jordan"
        for event in result.data.events
    )


@pytest.mark.asyncio
async def test_adapter_rejects_unvalidated_slug_and_game_id_without_request() -> None:
    client = Client([])
    adapter = HupuAdapter(client=client)
    schedule = await adapter.fetch_team_schedule(
        "https://evil.example/path",
        SeasonLabel(start_year=2025, end_year=2026, label="2025-26"),
        budget(),
    )
    detail = await adapter.get_game_summary("../168859", budget())
    assert schedule.error is not None
    assert detail.error is not None
    assert client.urls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_kind"),
    [
        (Response("", status_code=404), "NOT_FOUND"),
        (Response("<html></html>", redirect=True), "AUTH"),
        (Response("x" * 2_100_000), "SCHEMA_MISMATCH"),
    ],
)
async def test_adapter_maps_http_redirect_and_oversize_failures(response, error_kind) -> None:
    adapter = HupuAdapter(client=Client([response]), max_response_bytes=2_000_000)
    result = await adapter.get_game_summary("hupu:168859", budget())
    assert result.error is not None
    assert result.error.kind.value == error_kind


@pytest.mark.asyncio
async def test_warmer_deduplicates_team_schedules_loads_details_and_is_idempotent(
    tmp_path,
) -> None:
    warmer = _warmer_module()
    season = SeasonLabel(start_year=2025, end_year=2026, label="2025-26")
    index = GameIndex(tmp_path / "games.sqlite3")

    first = await warmer.warm_index(
        adapter=HupuAdapter(
            client=Client(
                [
                    Response(SCHEDULE_HTML),
                    Response(SCHEDULE_HTML),
                    Response(DETAIL_HTML),
                    Response(PBP_HTML),
                ]
            )
        ),
        index=index,
        season=season,
        team_slugs=["knicks", "spurs"],
        start_day=datetime(2026, 6, 1, tzinfo=UTC).date(),
        end_day=datetime(2026, 6, 30, tzinfo=UTC).date(),
        details=True,
    )

    assert first.unique_games == 1
    assert first.details_loaded == 1
    assert first.plays_loaded == 1
    assert index.counts()["games"] == 1
    summary = index.get_game_summary("hupu:168859")
    assert summary is not None
    assert summary.data.game.duration_seconds == 9_960
    assert summary.data.stat_lines[0].metrics["points"] == 45
    assert len(summary.data.plays.events) == 6
    assert summary.data.plays.events[1].action_text.startswith("杰伦-布伦森底角")

    second = await warmer.warm_index(
        adapter=HupuAdapter(
            client=Client([Response(SCHEDULE_HTML), Response(SCHEDULE_HTML)])
        ),
        index=index,
        season=season,
        team_slugs=["knicks", "spurs"],
        start_day=datetime(2026, 6, 1, tzinfo=UTC).date(),
        end_day=datetime(2026, 6, 30, tzinfo=UTC).date(),
        details=True,
    )

    assert second.unique_games == 1
    assert second.details_loaded == 0
    assert second.plays_loaded == 0
    assert index.counts()["games"] == 1
    assert index.counts()["plays"] == 6


def test_warmer_numbers_june_series_and_removes_unplayed_games_after_clinch() -> None:
    warmer = _warmer_module()
    season = SeasonLabel(start_year=2025, end_year=2026, label="2025-26")
    scores = [(105, 95), (105, 104), (111, 115), (107, 106), (94, 90)]
    games = []
    for number, (nyk_score, sas_score) in enumerate(scores, start=1):
        games.append(
            Game(
                game_id=f"hupu:{number}",
                season=season,
                start_utc=datetime(2026, 6, 2 + number * 2, tzinfo=UTC),
                away=_team("nyk"),
                home=_team("sas"),
                away_score=nyk_score,
                home_score=sas_score,
                status=GameStatus.FINAL,
            )
        )
    for number in (6, 7):
        games.append(
            Game(
                game_id=f"hupu:{number}",
                season=season,
                start_utc=datetime(2026, 6, 2 + number * 2, tzinfo=UTC),
                away=_team("sas"),
                home=_team("nyk"),
                status=GameStatus.SCHEDULED,
            )
        )

    inferred = warmer.infer_june_series(games)

    assert set(inferred) == {f"hupu:{number}" for number in range(1, 6)}
    assert [
        inferred[f"hupu:{number}"].series_game_number for number in range(1, 6)
    ] == [1, 2, 3, 4, 5]
