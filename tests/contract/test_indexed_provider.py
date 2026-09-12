from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.api.src.application.highlights import HighlightsService
from apps.api.src.application.parser import TEAMS
from apps.api.src.application.ports import ProviderResult, RequestBudget
from apps.api.src.domain.errors import ProviderError, ProviderErrorKind
from apps.api.src.domain.models import (
    Evidence,
    Freshness,
    Game,
    GameBundle,
    GameFilters,
    GameStatus,
    NewsItem,
    NewsQuery,
    PlayByPlayBundle,
    PlayEvent,
    PlayEventType,
    SeasonLabel,
    ShotType,
    SourceClass,
    TrustLevel,
)
from apps.api.src.infrastructure.game_index import GameIndex
from apps.api.src.providers.gateway import ProviderGateway
from apps.api.src.providers.indexed_provider import IndexedProvider


def _team(team_id: str):
    return next(team for team in TEAMS if team.canonical_id == team_id)


def _budget() -> RequestBudget:
    return RequestBudget(
        datetime.now(UTC) + timedelta(seconds=5),
        max_provider_operations=4,
        max_retries_per_operation=0,
    )


def _game(game_id: str = "hupu:168859") -> Game:
    return Game(
        game_id=game_id,
        season=SeasonLabel(start_year=2025, end_year=2026, label="2025-26"),
        start_utc=datetime(2026, 6, 14, 0, 30, tzinfo=UTC),
        home=_team("sas"),
        away=_team("nyk"),
        status=GameStatus.FINAL,
        home_score=90,
        away_score=94,
    )


def _evidence(*, fixture: bool = False) -> Evidence:
    return Evidence(
        evidence_id="fixture:game" if fixture else "hupu:game:168859",
        source_class=SourceClass.FIXTURE if fixture else SourceClass.ESTABLISHED_SPORTS,
        source_ref="fixture" if fixture else "hupu:game:168859",
        url="https://example.com/fixture" if fixture else "https://nba.hupu.com/games/boxscore/168859",
        fetched_at_utc=datetime.now(UTC),
        trust=TrustLevel.LOW if fixture else TrustLevel.MEDIUM,
        freshness=Freshness.UNKNOWN,
    )


class Primary:
    def __init__(self, result=None):
        self.result = result
        self.calls: list[str] = []

    async def search_games(self, _filters, _budget):
        self.calls.append("search_games")
        return self.result or ProviderResult(
            data=[], evidence=[], retrieved_at_utc=datetime.now(UTC)
        )

    async def get_game_summary(self, _game_id, _budget):
        self.calls.append("get_game_summary")
        return self.result or ProviderResult(
            data=None,
            error=ProviderError(
                kind=ProviderErrorKind.NOT_FOUND,
                retryable=False,
                safe_message="not found",
            ),
            retrieved_at_utc=datetime.now(UTC),
        )

    async def get_play_by_play(self, _game_id, _budget):
        self.calls.append("get_play_by_play")
        return self.result

    async def get_player_stats(self, _query, _budget):
        self.calls.append("get_player_stats")
        return self.result

    async def get_team_stats(self, _query, _budget):
        self.calls.append("get_team_stats")
        return self.result

    async def get_standings(self, _season, _budget):
        self.calls.append("get_standings")
        return self.result

    async def get_history(self, _query, _budget):
        self.calls.append("get_history")
        return self.result

    async def search_news(self, _query, _budget):
        self.calls.append("search_news")
        return self.result or ProviderResult(
            data=[], evidence=[], retrieved_at_utc=datetime.now(UTC)
        )


@pytest.mark.asyncio
async def test_matchup_uses_local_index_without_primary_call(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    index.upsert_bundle(GameBundle(game=_game()), [_evidence()], origin="public")
    primary = Primary()
    provider = IndexedProvider(primary, index)

    result = await provider.search_games(
        GameFilters(
            season=SeasonLabel(start_year=2025, end_year=2026, label="2025-26"),
            team_ids=["nyk", "sas"],
        ),
        _budget(),
    )
    assert result.error is None
    assert [game.game_id for game in result.data] == ["hupu:168859"]
    assert primary.calls == []


@pytest.mark.asyncio
async def test_recent_highlights_reads_full_local_window_before_live_slices(tmp_path) -> None:
    """Off-season network slices must not hide completed indexed games."""

    class AdvancingClock:
        def __init__(self) -> None:
            self.current = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)

        def now_utc(self) -> datetime:
            return self.current

        def advance(self, seconds: int) -> None:
            self.current += timedelta(seconds=seconds)

    class SlowEmptyPrimary(Primary):
        max_date_slices = 7

        def __init__(self, clock) -> None:
            super().__init__()
            self.clock = clock

        async def search_games(self, _filters, _budget):
            self.calls.append("search_games")
            self.clock.advance(21)
            return ProviderResult(
                data=[], evidence=[], retrieved_at_utc=self.clock.now_utc()
            )

    index = GameIndex(tmp_path / "index.sqlite3")
    for offset in range(5):
        game = _game(f"hupu:{168859 - offset}").model_copy(
            update={"start_utc": datetime(2026, 6, 14 - offset, 0, 30, tzinfo=UTC)}
        )
        index.upsert_bundle(GameBundle(game=game), [_evidence()], origin="public")

    clock = AdvancingClock()
    primary = SlowEmptyPrimary(clock)
    gateway = ProviderGateway(IndexedProvider(primary, index), max_retries=0)
    service = HighlightsService(gateway, clock=clock)

    result = await service.recent(
        limit=5,
        timezone_name="Asia/Shanghai",
        reference_day=datetime(2026, 9, 9, tzinfo=UTC).date(),
    )

    assert [game.game_id for game in result.games] == [
        "hupu:168859",
        "hupu:168858",
        "hupu:168857",
        "hupu:168856",
        "hupu:168855",
    ]
    assert result.data_origin == "public"
    assert primary.calls == []


@pytest.mark.asyncio
async def test_public_primary_result_is_persisted_but_fixture_is_not(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    public_result = ProviderResult(
        data=[_game("espn:1")],
        evidence=[_evidence()],
        retrieved_at_utc=datetime.now(UTC),
    )
    provider = IndexedProvider(Primary(public_result), index)
    await provider.search_games(GameFilters(team_ids=["nyk"]), _budget())
    assert index.counts()["games"] == 1

    fixture_result = ProviderResult(
        data=[_game("fixture:2")],
        evidence=[_evidence(fixture=True)],
        retrieved_at_utc=datetime.now(UTC),
    )
    provider = IndexedProvider(Primary(fixture_result), index)
    await provider.search_games(GameFilters(team_ids=["okc"]), _budget())
    assert index.counts()["games"] == 1


@pytest.mark.asyncio
async def test_known_schedule_game_is_enriched_and_updated(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    index.upsert_bundle(GameBundle(game=_game()), [_evidence()], origin="public")
    enriched = _game().model_copy(update={"duration_seconds": 9_960})

    class Details:
        calls = 0

        async def get_game_summary(self, _game_id, _budget):
            self.calls += 1
            return ProviderResult(
                data=GameBundle(game=enriched),
                evidence=[_evidence()],
                retrieved_at_utc=datetime.now(UTC),
            )

    detail = Details()
    primary = Primary()
    provider = IndexedProvider(primary, index, detail_provider=detail)
    result = await provider.get_game_summary("hupu:168859", _budget())
    assert result.data.game.duration_seconds == 9_960
    assert detail.calls == 1
    assert primary.calls == []
    assert index.get_game_summary("hupu:168859").data.game.duration_seconds == 9_960


@pytest.mark.asyncio
async def test_incomplete_indexed_non_hupu_game_fetches_primary_details(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    game = _game("espn:1")
    index.upsert_bundle(GameBundle(game=game), [_evidence()], origin="public")
    enriched = game.model_copy(update={"duration_seconds": 9_960})
    primary = Primary(
        ProviderResult(
            data=GameBundle(game=enriched),
            evidence=[_evidence()],
            retrieved_at_utc=datetime.now(UTC),
        )
    )

    result = await IndexedProvider(primary, index).get_game_summary("espn:1", _budget())

    assert result.data.game.duration_seconds == 9_960
    assert primary.calls == ["get_game_summary"]
    assert index.get_game_summary("espn:1").data.game.duration_seconds == 9_960


@pytest.mark.asyncio
async def test_remote_play_by_play_is_persisted_and_reused(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    game = _game("espn:2")
    index.upsert_bundle(GameBundle(game=game), [_evidence()], origin="public")
    event = PlayEvent(
        event_id="espn:2:1",
        game_id="espn:2",
        provider_index=1,
        period=4,
        clock_seconds_remaining=5,
        event_type=PlayEventType.FREE_THROW,
        shot_type=ShotType.FREE_THROW,
        points=1,
        home_score_after=90,
        away_score_after=95,
    )
    pbp = PlayByPlayBundle(game_id="espn:2", events=[event])
    primary = Primary(
        ProviderResult(
            data=pbp,
            evidence=[_evidence()],
            retrieved_at_utc=datetime.now(UTC),
        )
    )
    provider = IndexedProvider(primary, index)

    first = await provider.get_play_by_play("espn:2", _budget())
    second = await provider.get_play_by_play("espn:2", _budget())

    assert first.data.events[0].event_id == "espn:2:1"
    assert second.data.events[0].event_id == "espn:2:1"
    assert primary.calls == ["get_play_by_play"]


@pytest.mark.asyncio
async def test_hupu_play_by_play_uses_detail_provider_and_reuses_index(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    index.upsert_bundle(GameBundle(game=_game()), [_evidence()], origin="public")
    event = PlayEvent(
        event_id="hupu:168859:play:1",
        game_id="hupu:168859",
        provider_index=0,
        sequence=0,
        period=4,
        clock_seconds_remaining=2,
        event_type=PlayEventType.SHOT,
        shot_type=ShotType.THREE_POINT,
        shooter=_game().home,
        points=None,
        home_score_after=90,
        away_score_after=94,
        action_text="维克托·文班亚马三分跳投不中",
    )
    result = ProviderResult(
        data=PlayByPlayBundle(
            game_id="hupu:168859", events=[event], sequence_valid=True
        ),
        evidence=[_evidence()],
        retrieved_at_utc=datetime.now(UTC),
    )

    class Details:
        calls = 0

        async def get_play_by_play(self, _game_id, _budget):
            self.calls += 1
            return result

    detail = Details()
    primary = Primary()
    provider = IndexedProvider(primary, index, detail_provider=detail)

    first = await provider.get_play_by_play("hupu:168859", _budget())
    second = await provider.get_play_by_play("hupu:168859", _budget())

    assert first.data.events[0].action_text.endswith("不中")
    assert second.data.events[0].action_text.endswith("不中")
    assert detail.calls == 1
    assert primary.calls == []
    assert index.counts()["plays"] == 1


@pytest.mark.asyncio
async def test_index_failure_fails_open_to_primary(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    index.close()
    expected = ProviderResult(
        data=[_game("espn:1")],
        evidence=[_evidence()],
        retrieved_at_utc=datetime.now(UTC),
    )
    primary = Primary(expected)
    result = await IndexedProvider(primary, index).search_games(
        GameFilters(team_ids=["nyk", "sas"]), _budget()
    )
    assert result.data[0].game_id == "espn:1"
    assert primary.calls == ["search_games"]


@pytest.mark.asyncio
async def test_search_documents_infers_subjects_and_reuses_historical_cache(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    item = NewsItem(
        news_id="search:finals",
        title="尼克斯击败马刺夺冠",
        summary="2025-26 总决赛尼克斯四比一战胜马刺。",
        subject_refs=[_team("nyk"), _team("sas")],
        evidence_id="search:finals",
    )
    evidence = _evidence().model_copy(
        update={
            "evidence_id": "search:finals",
            "source_class": SourceClass.SEARCH,
            "source_ref": "search",
        }
    )
    index.upsert_document(item, evidence, origin="public")
    primary = Primary()
    provider = IndexedProvider(primary, index)
    result = await provider.search_news(
        NewsQuery(keywords=["2026尼克斯-马刺"], limit=5), _budget()
    )
    assert [value.news_id for value in result.data] == ["search:finals"]
    assert {ref.canonical_id for ref in result.data[0].subject_refs} == {"nyk", "sas"}
    assert result.partial is True
    assert primary.calls == []


@pytest.mark.asyncio
async def test_document_index_adds_entities_found_in_remote_content(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    evidence = _evidence().model_copy(
        update={
            "evidence_id": "search:cross-team",
            "source_class": SourceClass.SEARCH,
            "source_ref": "search",
        }
    )
    item = NewsItem(
        news_id="search:cross-team",
        title="尼克斯夺冠后，篮网调整阵容",
        summary="篮网完成多笔交易，尼克斯核心阵容保持稳定。",
        subject_refs=[_team("nyk")],
        evidence_id=evidence.evidence_id,
    )
    primary = Primary(
        ProviderResult(
            data=[item],
            evidence=[evidence],
            partial=True,
            retrieved_at_utc=datetime.now(UTC),
        )
    )

    await IndexedProvider(primary, index).search_news(
        NewsQuery(subject_refs=[_team("nyk")], keywords=["尼克斯阵容新闻"], limit=5),
        _budget(),
    )

    stored = index.search_documents(
        NewsQuery(subject_refs=[_team("bkn")], keywords=["篮网阵容调整"], limit=5)
    )
    assert [value.news_id for value in stored.data] == ["search:cross-team"]


@pytest.mark.asyncio
async def test_search_web_uses_local_historical_documents_without_remote_call(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    evidence = _evidence().model_copy(
        update={
            "evidence_id": "search:historical",
            "source_class": SourceClass.SEARCH,
            "source_ref": "search",
        }
    )
    item = NewsItem(
        news_id="search:historical",
        title="2026 尼克斯马刺总决赛回顾",
        summary="尼克斯与马刺完成总决赛交锋。",
        subject_refs=[_team("nyk"), _team("sas")],
        evidence_id=evidence.evidence_id,
    )
    index.upsert_document(item, evidence, origin="public")

    class WebPrimary(Primary):
        async def search_web(self, _query, _budget):
            self.calls.append("search_web")
            raise AssertionError("historical BM25 hit should avoid remote search")

    primary = WebPrimary()
    result = await IndexedProvider(primary, index).search_web(
        NewsQuery(keywords=["2026尼克斯-马刺"], limit=5), _budget()
    )
    assert result.error is None
    assert [value.news_id for value in result.data] == ["search:historical"]
    assert primary.calls == []


@pytest.mark.asyncio
async def test_search_web_persists_remote_partial_documents(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    evidence = _evidence().model_copy(
        update={
            "evidence_id": "search:remote",
            "source_class": SourceClass.SEARCH,
            "source_ref": "search",
        }
    )
    item = NewsItem(
        news_id="search:remote",
        title="尼克斯马刺比赛复盘",
        summary="公开报道梳理比赛过程与关键回合。",
        subject_refs=[],
        evidence_id=evidence.evidence_id,
    )
    remote = ProviderResult(
        data=[item],
        evidence=[evidence],
        partial=True,
        retrieved_at_utc=datetime.now(UTC),
    )
    primary = Primary(remote)
    result = await IndexedProvider(primary, index).search_web(
        NewsQuery(keywords=["尼克斯马刺比赛复盘"], limit=5), _budget()
    )
    assert result.error is None
    assert [value.news_id for value in result.data] == ["search:remote"]
    assert index.counts()["documents"] == 1
