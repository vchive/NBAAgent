from __future__ import annotations

from apps.api.src.config import Settings
from apps.api.src.main import create_app
from apps.api.src.providers.espn_adapter import ESPNAdapter
from apps.api.src.providers.fixture_provider import FixtureProvider


def test_fixture_mode_is_deterministic_by_default() -> None:
    app = create_app(settings=Settings(public_data_mode="fixture"))
    assert isinstance(app.state.provider, FixtureProvider)
    assert app.state.fallback_provider is None


def test_live_mode_uses_allowlisted_public_adapter() -> None:
    app = create_app(settings=Settings(public_data_mode="live"))
    assert isinstance(app.state.provider, ESPNAdapter)
    assert app.state.fallback_provider is None


def test_hybrid_mode_keeps_fixture_fallback_behind_gateway() -> None:
    app = create_app(settings=Settings(public_data_mode="hybrid"))
    assert isinstance(app.state.provider, ESPNAdapter)
    assert isinstance(app.state.fallback_provider, FixtureProvider)
    assert app.state.chat_use_case.gateway.fallback is app.state.fallback_provider
