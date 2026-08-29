"""Select the safest answer runtime for an intent."""

from __future__ import annotations

from apps.api.src.domain.models import IntelligenceMode, IntentName


class RuntimeSelector:
    def __init__(
        self,
        *,
        template_runtime,
        hermes_runtime=None,
        profile: str = "template",
        full_intelligence_enabled: bool = False,
        default_intelligence_mode: str = "hybrid",
    ) -> None:
        self.template_runtime = template_runtime
        self.hermes_runtime = hermes_runtime
        self.profile = profile.lower()
        self.full_intelligence_enabled = bool(full_intelligence_enabled)
        self.default_intelligence_mode = str(default_intelligence_mode or "hybrid").lower()

    def for_intent(
        self,
        intent_name: IntentName | str,
        requested_mode: IntelligenceMode | str | None = None,
    ):
        name = str(getattr(intent_name, "value", intent_name)).upper()
        mode = getattr(requested_mode, "value", requested_mode)
        mode = str(mode or self.default_intelligence_mode).lower()
        # The feature flag is authoritative. A browser/request cannot enable
        # full mode when the service operator has disabled it.
        full = mode == "full" and self.full_intelligence_enabled
        if (
            self.profile in {"hermes", "hybrid"}
            and self.hermes_runtime is not None
            and (full or name in {"TACTICAL", "RECAP"})
        ):
            return self.hermes_runtime
        return self.template_runtime

    select = for_intent


__all__ = ["RuntimeSelector"]
