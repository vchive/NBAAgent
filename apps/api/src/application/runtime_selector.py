"""Select the safest answer runtime for an intent."""

from __future__ import annotations

from apps.api.src.domain.models import IntentName


class RuntimeSelector:
    def __init__(self, *, template_runtime, hermes_runtime=None, profile: str = "template") -> None:
        self.template_runtime = template_runtime
        self.hermes_runtime = hermes_runtime
        self.profile = profile.lower()

    def for_intent(self, intent_name: IntentName | str):
        name = getattr(intent_name, "value", intent_name)
        if (
            self.profile in {"hermes", "hybrid"}
            and name in {"TACTICAL", "RECAP"}
            and self.hermes_runtime is not None
        ):
            return self.hermes_runtime
        return self.template_runtime

    select = for_intent


__all__ = ["RuntimeSelector"]
