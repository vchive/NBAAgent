from __future__ import annotations

from apps.api.src.application.runtime_selector import RuntimeSelector
from apps.api.src.domain.models import ChatRequest, IntelligenceMode, IntentName


def test_chat_request_accepts_lowercase_intelligence_mode() -> None:
    request = ChatRequest.model_validate({"message": "G4 比分", "intelligence_mode": "full"})
    assert request.intelligence_mode is IntelligenceMode.FULL


def test_full_mode_selects_hermes_for_objective_intent_only_when_enabled() -> None:
    template = object()
    hermes = object()
    selector = RuntimeSelector(
        template_runtime=template,
        hermes_runtime=hermes,
        profile="hybrid",
        full_intelligence_enabled=True,
    )
    assert selector.for_intent(IntentName.DATA, "full") is hermes
    assert selector.for_intent(IntentName.DATA, "hybrid") is template


def test_disabled_full_mode_cannot_be_enabled_by_request() -> None:
    selector = RuntimeSelector(
        template_runtime=object(),
        hermes_runtime=object(),
        profile="hybrid",
        full_intelligence_enabled=False,
    )
    assert selector.for_intent(IntentName.DATA, IntelligenceMode.FULL) is selector.template_runtime
