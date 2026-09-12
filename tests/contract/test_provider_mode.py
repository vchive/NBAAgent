from __future__ import annotations

import pytest

from apps.api.src.config import Settings
from apps.api.src.main import create_app
from apps.api.src.providers.aliyun_iqs_adapter import AliyunIQSSearchAdapter
from apps.api.src.providers.baidu_adapter import BaiduSearchAdapter
from apps.api.src.providers.ddg_adapter import DuckDuckGoAdapter
from apps.api.src.providers.espn_adapter import ESPNAdapter
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.indexed_provider import IndexedProvider
from apps.api.src.providers.qianfan_search_adapter import QianfanSearchAdapter
from apps.api.src.providers.search_augmented_provider import SearchAugmentedProvider


def test_fixture_mode_is_deterministic_by_default() -> None:
    app = create_app(settings=Settings(public_data_mode="fixture"))
    assert isinstance(app.state.provider, FixtureProvider)
    assert app.state.fallback_provider is None


def test_live_mode_uses_allowlisted_public_adapter() -> None:
    app = create_app(settings=Settings(public_data_mode="live"))
    assert isinstance(app.state.provider, ESPNAdapter)
    assert app.state.fallback_provider is None


def test_hybrid_mode_uses_public_stack_without_fixture_fallback() -> None:
    app = create_app(settings=Settings(public_data_mode="hybrid"))
    assert isinstance(app.state.provider, ESPNAdapter)
    assert app.state.fallback_provider is None
    assert app.state.chat_use_case.gateway.fallback is None


@pytest.mark.parametrize("mode", ["live", "hybrid"])
def test_public_search_stack_is_iqs_then_qianfan_then_baidu_then_ddg(
    mode: str,
) -> None:
    settings = Settings(
        public_data_mode=mode,
        aliyun_iqs_search_enabled=True,
        aliyun_iqs_api_key="iqs-test-key",
        qianfan_search_enabled=True,
        qianfan_search_api_key="test-key",
        baidu_search_enabled=True,
        ddg_search_enabled=True,
    )
    app = create_app(settings=settings)
    assert isinstance(app.state.provider, SearchAugmentedProvider)
    iqs = app.state.provider.search_provider
    assert isinstance(iqs, AliyunIQSSearchAdapter)
    qianfan = iqs.fallback_provider
    assert isinstance(qianfan, QianfanSearchAdapter)
    assert isinstance(qianfan.fallback_provider, BaiduSearchAdapter)
    assert isinstance(qianfan.fallback_provider.fallback_provider, DuckDuckGoAdapter)
    assert app.state.fallback_provider is None
    assert app.state.chat_use_case.gateway.fallback is None


def test_iqs_secret_is_hidden_and_validated() -> None:
    configured = Settings(aliyun_iqs_api_key="iqs-super-secret")
    assert "iqs-super-secret" not in repr(configured)

    with pytest.raises(ValueError, match="single token"):
        Settings(aliyun_iqs_api_key="invalid key").validate()


def test_live_mode_wraps_public_stack_with_queryable_index(tmp_path) -> None:
    app = create_app(
        settings=Settings(
            public_data_mode="live",
            game_index_enabled=True,
            game_index_db=str(tmp_path / "index.sqlite3"),
            hupu_enrichment_enabled=True,
        )
    )
    assert isinstance(app.state.provider, IndexedProvider)
    assert app.state.game_index.status == "ok"


def test_fixture_mode_never_opens_or_populates_public_index(tmp_path) -> None:
    path = tmp_path / "index.sqlite3"
    app = create_app(
        settings=Settings(
            public_data_mode="fixture",
            game_index_enabled=True,
            game_index_db=str(path),
        )
    )
    assert isinstance(app.state.provider, FixtureProvider)
    assert app.state.game_index is None
    assert not path.exists()
