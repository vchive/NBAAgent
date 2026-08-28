from __future__ import annotations

import pytest

from apps.api.src.config import Settings


def test_siliconflow_defaults_are_explicit_and_valid() -> None:
    settings = Settings()
    settings.validate()

    assert settings.siliconflow_base_url == "https://api.siliconflow.cn/v1"
    assert settings.siliconflow_model == "deepseek-ai/DeepSeek-V4-Flash"
    assert settings.siliconflow_max_tokens == 800


def test_live_model_requires_an_enabled_runtime_profile() -> None:
    with pytest.raises(ValueError, match="HERMES_LITE_MODE"):
        Settings(llm_mode="live", runtime_profile="hybrid").validate()

    with pytest.raises(ValueError, match="RUNTIME_PROFILE"):
        Settings(
            llm_mode="live",
            runtime_profile="template",
            hermes_lite_mode="embedded_spike",
        ).validate()


def test_enum_like_environment_values_are_case_insensitive_for_injected_settings() -> None:
    settings = Settings(
        public_data_mode="FIXTURE",
        llm_mode="MOCK",
        runtime_profile="TEMPLATE",
        hermes_lite_mode="OFF",
    )
    settings.validate()


def test_sidecar_profile_does_not_require_a_direct_provider_url() -> None:
    # The reserved sidecar topology owns its own egress settings; the direct
    # SiliconFlow URL is not parsed by the in-process adapter in this mode.
    Settings(
        llm_mode="live",
        runtime_profile="hybrid",
        hermes_lite_mode="sidecar",
        siliconflow_base_url="http://sidecar-placeholder.invalid/v1",
    ).validate()
