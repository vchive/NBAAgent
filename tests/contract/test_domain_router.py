"""Routing-boundary tests for the default flower product domain."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from apps.api.src.application.domain_router import DomainRouter
from apps.api.src.domain.models import EntityKind, EntityRef, Game, GameStatus, SeasonLabel


class _UseCase:
    def __init__(self, name: str, *, game: Game | None = None, origin: str | None = None):
        self.name = name
        self.game_registry = {game.game_id: game} if game is not None else {}
        self.game_origin_registry = (
            {game.game_id: origin} if game is not None and origin is not None else {}
        )

    async def handle(self, request, **kwargs):  # pragma: no cover - route identity only
        return self.name


def _game() -> Game:
    team_a = EntityRef(kind=EntityKind.TEAM, canonical_id="a", display_name="甲队")
    team_b = EntityRef(kind=EntityKind.TEAM, canonical_id="b", display_name="乙队")
    return Game(
        game_id="public-g1",
        season=SeasonLabel(start_year=2025, end_year=2026, label="2025-26"),
        start_utc=datetime(2026, 6, 1, 12, tzinfo=UTC),
        home=team_a,
        away=team_b,
        status=GameStatus.FINAL,
        home_score=100,
        away_score=90,
    )


def test_flower_vocabulary_has_precedence_over_broad_sports_words() -> None:
    flower = _UseCase("flower")
    nba = _UseCase("nba")
    router = DomainRouter(flower, nba)

    assert router.select({"message": "花园里的比赛活动怎么安排浇水？"}) is flower
    assert router.select({"message": "月季复盘一下最近的养护"}) is flower


def test_unambiguous_sports_word_routes_to_legacy_compatibility() -> None:
    flower = _UseCase("flower")
    nba = _UseCase("nba")
    router = DomainRouter(flower, nba)

    assert router.select({"message": "NBA 最近赛程"}) is nba
    assert router.select({"message": "这场比赛谁赢了？"}) is nba


def test_unknown_selected_game_id_cannot_switch_a_flower_turn_to_nba() -> None:
    flower = _UseCase("flower")
    nba = _UseCase("nba")
    router = DomainRouter(flower, nba)

    assert router.select(
        {"message": "月季怎么养？", "selected_game_id": "guessed-fixture-id"}
    ) is flower


def test_registered_card_requires_explicit_server_origin() -> None:
    flower = _UseCase("flower")
    untrusted = _UseCase("nba-untrusted", game=_game(), origin=None)
    trusted = _UseCase("nba-trusted", game=_game(), origin="public")

    # The same assertion is exercised with the untrusted and trusted use cases
    # as separate routers because the origin registry belongs to the server's
    # legacy use case.
    assert DomainRouter(flower, untrusted).select(
        {"message": "这个怎么处理？", "selected_game_id": "public-g1"}
    ) is flower
    assert DomainRouter(flower, trusted).select(
        {"message": "这个怎么处理？", "selected_game_id": "public-g1"}
    ) is trusted


def test_legacy_default_domain_remains_explicitly_supported() -> None:
    flower = _UseCase("flower")
    nba = _UseCase("nba")
    router = DomainRouter(flower, nba, default_domain="basketball")
    assert router.select({"message": "月季怎么养？"}) is nba


def test_session_pin_keeps_ambiguous_nba_follow_up_in_legacy_route() -> None:
    flower = _UseCase("flower")
    nba = _UseCase("nba")
    router = DomainRouter(flower, nba)
    session_id = uuid4()

    # First turn is an explicit NBA compatibility request.  The fake result
    # carries the same session id so the router can remember the domain even
    # when the request body omitted it on the first turn.
    async def run_first():
        return await router.handle(
            {"message": "NBA 最近赛程", "session_id": session_id}
        )

    import asyncio

    asyncio.run(run_first())
    assert router.select({"message": "为什么赢？", "session_id": session_id}) is nba
    assert router.select({"message": "我问了几个问题", "session_id": session_id}) is nba


def test_explicit_flower_follow_up_can_switch_a_pinned_nba_session() -> None:
    flower = _UseCase("flower")
    nba = _UseCase("nba")
    router = DomainRouter(flower, nba)
    session_id = uuid4()

    import asyncio

    asyncio.run(router.handle({"message": "NBA 比分", "session_id": session_id}))
    assert router.select({"message": "月季怎么浇水？", "session_id": session_id}) is flower
