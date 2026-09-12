from __future__ import annotations

from datetime import UTC, datetime

from apps.api.src.application.parser import TEAMS
from apps.api.src.domain.models import (
    EntityKind,
    Evidence,
    Freshness,
    Game,
    GameBundle,
    GameFilters,
    GameStatus,
    NewsItem,
    NewsQuery,
    SeasonLabel,
    SourceClass,
    StatLine,
    StatScope,
    TrustLevel,
)
from apps.api.src.infrastructure.game_index import GameIndex


def _team(team_id: str):
    return next(team for team in TEAMS if team.canonical_id == team_id)


def _evidence(record: str = "168859") -> Evidence:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    return Evidence(
        evidence_id=f"hupu:game:{record}",
        source_class=SourceClass.ESTABLISHED_SPORTS,
        source_ref=f"game:{record}",
        url=f"https://nba.hupu.com/games/boxscore/{record}",
        fetched_at_utc=now,
        data_as_of_utc=now,
        trust=TrustLevel.MEDIUM,
        freshness=Freshness.FRESH,
    )


def _bundle(*, score: tuple[int, int] = (90, 94), with_stats: bool = True) -> GameBundle:
    season = SeasonLabel(start_year=2025, end_year=2026, label="2025-26")
    game = Game(
        game_id="hupu:168859",
        season=season,
        start_utc=datetime(2026, 6, 14, 0, 30, tzinfo=UTC),
        home=_team("sas"),
        away=_team("nyk"),
        status=GameStatus.FINAL,
        home_score=score[0],
        away_score=score[1],
        series_id="2025-26-finals-nyk-sas",
        series_game_number=5,
        duration_seconds=9_960,
        attendance=18_984,
    )
    stats = []
    if with_stats:
        stats = [
            StatLine(
                subject=_team("nyk").model_copy(
                    update={
                        "kind": EntityKind.PLAYER,
                        "canonical_id": "jalen-brunson-150988",
                        "display_name": "杰伦-布伦森",
                    }
                ),
                game_id=game.game_id,
                scope=StatScope.GAME,
                metrics={"points": 45, "rebounds": 3, "assists": 3},
                evidence_ids=["hupu:game:168859"],
            )
        ]
    return GameBundle(game=game, stat_lines=stats, leaders=stats)


def test_structured_filters_survive_restart_and_are_order_independent(tmp_path) -> None:
    path = tmp_path / "index.sqlite3"
    index = GameIndex(path)
    assert index.upsert_bundle(_bundle(), [_evidence()], origin="public") == "inserted"
    index.close()

    reopened = GameIndex(path)
    result = reopened.search_games(
        GameFilters(
            season=SeasonLabel(start_year=2025, end_year=2026, label="2025-26"),
            team_ids=["nyk", "sas"],
            status=GameStatus.FINAL,
            series_game_number=5,
        )
    )
    assert [game.game_id for game in result.data] == ["hupu:168859"]
    summary = reopened.get_game_summary("hupu:168859")
    assert summary is not None
    assert summary.data.game.duration_seconds == 9_960
    assert summary.data.stat_lines[0].metrics["points"] == 45
    reopened.close()


def test_index_rejects_demo_origin_and_final_score_conflict(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    assert (
        index.upsert_bundle(_bundle(), [_evidence()], origin="demo_snapshot")
        == "rejected_origin"
    )
    assert index.counts()["games"] == 0
    assert index.upsert_bundle(_bundle(), [_evidence()], origin="public") == "inserted"
    assert (
        index.upsert_bundle(_bundle(score=(91, 94)), [_evidence()], origin="public")
        == "rejected_conflict"
    )
    assert index.get_game_summary("hupu:168859").data.game.home_score == 90


def test_bm25_uses_canonical_terms_for_two_character_chinese_team(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    now = datetime(2026, 9, 4, tzinfo=UTC)
    relevant = NewsItem(
        news_id="doc-finals",
        title="尼克斯击败马刺夺冠",
        summary="2025-26 总决赛第五场结束后，尼克斯以系列赛四比一夺冠。",
        subject_refs=[_team("nyk"), _team("sas")],
        evidence_id="search:finals",
    )
    irrelevant = NewsItem(
        news_id="doc-thunder",
        title="雷霆赛季回顾",
        summary="雷霆完成常规赛总结。",
        subject_refs=[_team("okc")],
        evidence_id="search:thunder",
    )
    evidence = Evidence(
        evidence_id="search:finals",
        source_class=SourceClass.SEARCH,
        source_ref="search:finals",
        url="https://example.com/finals",
        fetched_at_utc=now,
        trust=TrustLevel.LOW,
        freshness=Freshness.UNKNOWN,
    )
    other_evidence = evidence.model_copy(
        update={
            "evidence_id": "search:thunder",
            "source_ref": "search:thunder",
            "url": "https://example.com/thunder",
        }
    )
    assert index.upsert_document(relevant, evidence, origin="public")
    assert index.upsert_document(irrelevant, other_evidence, origin="public")

    result = index.search_documents(
        NewsQuery(
            subject_refs=[_team("nyk"), _team("sas")],
            keywords=["2026尼克斯-马刺 总决赛"],
            limit=5,
        )
    )
    assert [item.news_id for item in result.data] == ["doc-finals"]
    assert result.partial is True
    assert index.counts()["documents"] == 2


def test_document_upsert_is_bounded_and_idempotent(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3", max_document_bytes=1_024)
    evidence = _evidence("doc")
    item = NewsItem(
        news_id="doc-1",
        title="尼克斯与马刺",
        summary="复盘" * 2_000,
        subject_refs=[_team("nyk"), _team("sas")],
        evidence_id=evidence.evidence_id,
    )
    assert index.upsert_document(item, evidence, origin="public")
    assert index.upsert_document(item, evidence, origin="public")
    assert index.counts()["documents"] == 1
    stored = index.search_documents(
        NewsQuery(subject_refs=[_team("nyk"), _team("sas")], keywords=["复盘"], limit=1)
    )
    assert len((stored.data[0].summary or "").encode("utf-8")) <= 1_024


def test_document_search_prefers_question_topic_within_same_team(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    evidence = _evidence("topic").model_copy(
        update={"source_class": SourceClass.SEARCH}
    )
    for news_id, title, summary in (
        ("generic", "尼克斯夺冠", "尼克斯赢得总冠军。"),
        ("roster", "尼克斯内线补强", "球队阵容调整聚焦内线补强和球员续约。"),
        ("history", "尼克斯队史", "球队历史与主场介绍。"),
    ):
        assert index.upsert_document(
            NewsItem(
                news_id=news_id,
                title=title,
                summary=summary,
                subject_refs=[_team("nyk")],
                evidence_id=evidence.evidence_id,
            ),
            evidence.model_copy(update={"evidence_id": f"search:{news_id}"}),
            origin="public",
        )

    result = index.search_documents(
        NewsQuery(
            subject_refs=[_team("nyk")],
            keywords=["尼克斯阵容调整新闻"],
            limit=3,
        )
    )

    assert [item.news_id for item in result.data] == ["roster"]


def test_document_search_keeps_topic_centered_on_queried_team(tmp_path) -> None:
    index = GameIndex(tmp_path / "index.sqlite3")
    evidence = _evidence("subject-topic").model_copy(
        update={"source_class": SourceClass.SEARCH}
    )
    documents = (
        (
            "cross-team-drift",
            "尼克斯4比1击败马刺夺得总冠军，篮网阵容调整幅度超过六成",
            (
                "纽约尼克斯拿下总冠军，布鲁克林篮网球迷的遗憾被触发。"
                "篮网在休赛期完成了七笔签约和交易，阵容调整幅度超过六成，"
                "新赛季阵容框架已经确定。"
            ),
        ),
        (
            "knicks-roster",
            "尼克斯中锋仅剩两人，低成本补强能否填补内线空缺？",
            (
                "尼克斯夺冠后面临薪资压力，球队放走多名角色球员。"
                "尼克斯今夏的内线流失说明夺冠阵容需要调整，低成本补强更现实。"
            ),
        ),
        (
            "team-profile",
            "纽约尼克斯队",
            "尼克斯是NBA球队，近年来持续通过选秀和交易补强阵容。",
        ),
    )
    for news_id, title, summary in documents:
        assert index.upsert_document(
            NewsItem(
                news_id=news_id,
                title=title,
                summary=summary,
                subject_refs=[_team("nyk")],
                evidence_id=f"search:{news_id}",
            ),
            evidence.model_copy(update={"evidence_id": f"search:{news_id}"}),
            origin="public",
        )

    result = index.search_documents(
        NewsQuery(
            subject_refs=[_team("nyk")],
            keywords=["尼克斯夺冠后有哪些阵容调整新闻？"],
            limit=3,
        )
    )

    assert [item.news_id for item in result.data] == [
        "knicks-roster",
        "cross-team-drift",
    ]
