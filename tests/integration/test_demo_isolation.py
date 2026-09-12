"""Public-chat isolation for the deterministic product-demo snapshot."""

from __future__ import annotations

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.config import Settings
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway


def _hybrid_usecase(
    *,
    primary: FixtureProvider | None = None,
    snapshot: FixtureProvider | None = None,
    game_registry=None,
    game_origin_registry=None,
) -> tuple[ChatUseCase, FixtureProvider, FixtureProvider]:
    primary = primary or FixtureProvider(scenario="empty")
    snapshot = snapshot or FixtureProvider()
    usecase = ChatUseCase(
        primary,
        gateway=ProviderGateway(primary, fallback=snapshot, max_retries=0),
        settings=Settings(
            public_data_mode="hybrid",
            default_intelligence_mode="hybrid",
        ),
        game_registry=game_registry,
        game_origin_registry=game_origin_registry,
    )
    return usecase, primary, snapshot


def _snapshot_game(snapshot: FixtureProvider, game_id: str):
    snapshot._load()
    return next(game for game in snapshot._games if game.game_id == game_id)


@pytest.mark.asyncio
async def test_hybrid_rejects_guessed_fixture_selected_game_id() -> None:
    """Knowing a built-in id is not proof that the UI displayed its demo card."""

    usecase, primary, snapshot = _hybrid_usecase()

    result = await usecase.handle(
        {
            "message": "这场比赛谁赢了？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "hybrid",
        }
    )

    assert result.status in {"needs_clarification", "no_data"}
    assert result.data_origin == "none"
    assert "108–104" not in result.answer_markdown
    assert "凯尔特人" not in result.answer_markdown
    assert "雷霆" not in result.answer_markdown
    assert primary.calls == 0
    assert snapshot.calls == 0


@pytest.mark.asyncio
async def test_hybrid_rejects_registry_game_without_trusted_origin() -> None:
    """A typed registry row and its server-owned origin must be present together."""

    snapshot = FixtureProvider()
    game = _snapshot_game(snapshot, "2026-finals-g4")
    usecase, primary, snapshot = _hybrid_usecase(
        snapshot=snapshot,
        game_registry={game.game_id: game},
        game_origin_registry={},
    )

    result = await usecase.handle(
        {
            "message": "这场比赛谁赢了？",
            "selected_game_id": game.game_id,
            "intelligence_mode": "hybrid",
        }
    )

    assert result.status in {"needs_clarification", "no_data"}
    assert result.data_origin == "none"
    assert "108–104" not in result.answer_markdown
    assert primary.calls == 0
    assert snapshot.calls == 0


@pytest.mark.asyncio
async def test_hybrid_allows_exact_registered_demo_card_and_keeps_label() -> None:
    """A demo card previously projected by highlights remains a usable demo."""

    snapshot = FixtureProvider()
    game = _snapshot_game(snapshot, "2026-finals-g4")
    usecase, primary, snapshot = _hybrid_usecase(
        snapshot=snapshot,
        game_registry={game.game_id: game},
        game_origin_registry={game.game_id: "demo_snapshot"},
    )

    result = await usecase.handle(
        {
            "message": "这场比赛谁赢了？",
            "selected_game_id": game.game_id,
            "intelligence_mode": "hybrid",
        }
    )

    assert result.status == "completed"
    assert result.data_origin == "demo_snapshot"
    assert result.as_of_beijing is None
    assert "凯尔特人" in result.answer_markdown
    assert "108–104" in result.answer_markdown
    assert primary.operation_calls["get_game_summary"] == 1
    assert snapshot.operation_calls["get_game_summary"] == 1


@pytest.mark.asyncio
async def test_public_card_failure_never_falls_back_to_same_named_demo_game() -> None:
    """A public origin cannot be downgraded to a fixture when live detail fails."""

    snapshot = FixtureProvider()
    game = _snapshot_game(snapshot, "2026-finals-g4")
    primary = FixtureProvider(scenario="timeout")
    usecase, primary, snapshot = _hybrid_usecase(
        primary=primary,
        snapshot=snapshot,
        game_registry={game.game_id: game},
        game_origin_registry={game.game_id: "public"},
    )

    result = await usecase.handle(
        {
            "message": "这场比赛谁赢了？",
            "selected_game_id": game.game_id,
            "intelligence_mode": "hybrid",
        }
    )

    assert result.status == "failed"
    assert "108–104" not in result.answer_markdown
    assert snapshot.calls == 0


@pytest.mark.asyncio
async def test_registered_demo_card_does_not_authorize_unrelated_fixture_history() -> None:
    """Demo permission is scoped to the selected game, not the whole fixture DB."""

    snapshot = FixtureProvider()
    game = _snapshot_game(snapshot, "2026-finals-g4")
    usecase, _primary, snapshot = _hybrid_usecase(
        snapshot=snapshot,
        game_registry={game.game_id: game},
        game_origin_registry={game.game_id: "demo_snapshot"},
    )

    result = await usecase.handle(
        {
            "message": "1999 年总冠军是谁？",
            "selected_game_id": game.game_id,
            "intelligence_mode": "hybrid",
        }
    )

    assert result.status == "no_data"
    assert result.data_origin == "none"
    assert "马刺" not in result.answer_markdown
    assert snapshot.calls == 0


@pytest.mark.asyncio
async def test_hybrid_public_fact_query_never_uses_fixture_fallback() -> None:
    """A scoped public miss must stay a miss instead of becoming the fake Finals."""

    usecase, primary, snapshot = _hybrid_usecase()

    result = await usecase.handle(
        {
            "message": "2025-26 总决赛雷霆对凯尔特人 G4 谁赢了？",
            "intelligence_mode": "hybrid",
        }
    )

    assert result.status in {"no_data", "needs_clarification"}
    assert result.data_origin == "none"
    assert "108–104" not in result.answer_markdown
    assert "杰伦·布朗" not in result.answer_markdown
    assert primary.operation_calls["search_games"] == 1
    assert snapshot.calls == 0


@pytest.mark.asyncio
async def test_hybrid_primary_error_does_not_open_fixture_stats() -> None:
    """The no-demo rule also applies to non-game gateway operations."""

    primary = FixtureProvider(scenario="timeout")
    usecase, primary, snapshot = _hybrid_usecase(primary=primary)

    result = await usecase.handle(
        {
            "message": "杰伦·布朗 2025-26 赛季场均得分多少？",
            "intelligence_mode": "hybrid",
        }
    )

    assert result.status == "failed"
    assert "32" not in result.answer_markdown
    assert snapshot.calls == 0


@pytest.mark.asyncio
async def test_fixture_profile_still_supports_direct_selected_snapshot() -> None:
    """The stricter public boundary must not break deterministic fixture tests."""

    snapshot = FixtureProvider()
    usecase = ChatUseCase(
        snapshot,
        settings=Settings(
            public_data_mode="fixture",
            default_intelligence_mode="hybrid",
        ),
    )

    result = await usecase.handle(
        {
            "message": "这场比赛谁赢了？",
            "selected_game_id": "2026-finals-g4",
            "intelligence_mode": "hybrid",
        }
    )

    assert result.status == "completed"
    assert result.data_origin == "demo_snapshot"
    assert "凯尔特人" in result.answer_markdown
    assert "108–104" in result.answer_markdown
