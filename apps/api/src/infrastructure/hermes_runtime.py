"""Constrained Hermes-lite seam and deterministic mock runtime.

The production project can point this adapter at a sidecar later.  In the
fixture profile it deliberately behaves like a tiny renderer and has no tools,
network, filesystem, memory, or subprocess access.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time

from apps.api.src.application.ports import (
    CancelToken,
    CapabilityManifest,
    ComposerInput,
    RuntimeResult,
    RuntimeStatus,
    ToolPolicy,
)
from apps.api.src.application.template_composer import TemplateComposer


class TemplateRuntime:
    def __init__(self, *, composer: TemplateComposer | None = None) -> None:
        self.composer = composer or TemplateComposer()

    async def compose(self, input: ComposerInput, cancel: CancelToken) -> RuntimeResult:
        started = time.monotonic()
        cancel.raise_if_cancelled()
        draft = self.composer.compose(input.intent, input.fact_bundle, retrieved_at=None)
        return RuntimeResult(
            status=RuntimeStatus.OK,
            draft_markdown=draft.markdown,
            blocks=draft.blocks,
            used_fact_ids=[fact.fact_id for fact in input.fact_bundle.facts],
            finish_reason="template",
            latency_ms=int((time.monotonic() - started) * 1000),
        )


class HermesRuntimeAdapter:
    """A narrow adapter that validates the capability boundary before compose."""

    def __init__(
        self,
        *,
        fallback: TemplateRuntime | None = None,
        mode: str = "off",
        timeout_ms: int = 2500,
        endpoint: str = "",
    ) -> None:
        self.fallback = fallback or TemplateRuntime()
        self.mode = mode.lower()
        if self.mode not in {"off", "embedded_spike", "sidecar"}:
            raise ValueError("Hermes mode must be off, embedded_spike, or sidecar")
        self.timeout_ms = timeout_ms
        self.endpoint = endpoint
        self.tool_policy = ToolPolicy()
        policy_text = json.dumps(
            self.tool_policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        self.manifest = CapabilityManifest(
            policy_hash=hashlib.sha256(policy_text.encode()).hexdigest(),
            tools_hash=hashlib.sha256(b"[]").hexdigest(),
            network_mode="deny",
            filesystem_mode="none",
            read_only_fs=True,
        )
        self.status = "disabled" if self.mode == "off" else "ok"
        self.fallback_reason: str | None = None
        self._policy_hash = hashlib.sha256(policy_text.encode()).hexdigest()
        self._tools_hash = hashlib.sha256(b"[]").hexdigest()

    def capability_self_test(self) -> bool:
        try:
            return (
                self.tool_policy == ToolPolicy()
                and self.manifest.tools_enabled == []
                and self.manifest.network_mode == "deny"
                and self.manifest.filesystem_mode == "none"
                and self.manifest.policy_hash == self._policy_hash
                and self.manifest.tools_hash == self._tools_hash
            )
        except Exception:
            return False

    @staticmethod
    def _unavailable(reason: str) -> RuntimeResult:
        """Build a non-leaking result for a capability-boundary rejection."""

        return RuntimeResult(
            status=RuntimeStatus.UNAVAILABLE,
            draft_markdown=None,
            blocks=[],
            used_fact_ids=[],
            finish_reason=reason,
            latency_ms=0,
            error_code="COMPOSER_UNAVAILABLE",
        )

    async def compose(self, input: ComposerInput, cancel: CancelToken) -> RuntimeResult:
        cancel.raise_if_cancelled()
        # ``off`` is an explicit local profile: use the deterministic fallback and
        # never attempt to inspect or invoke a sidecar.  For enabled profiles, all
        # contract checks happen before any runtime work so malformed/unsafe input
        # cannot cross the capability boundary.
        if self.mode == "off":
            self.fallback_reason = "disabled_or_policy_mismatch"
            return await self.fallback.compose(input, cancel)
        if input.contract_version != "composer.v1":
            self.fallback_reason = "contract_version"
            return self._unavailable(self.fallback_reason)
        if input.tool_policy != self.tool_policy:
            self.fallback_reason = "tool_policy_mismatch"
            return self._unavailable(self.fallback_reason)
        if not self.capability_self_test():
            self.fallback_reason = "capability_self_test"
            return self._unavailable(self.fallback_reason)
        # A model may only be called with an explicitly verified or partial fact
        # bundle.  ``NONE`` (including an empty bundle) is returned to the caller
        # as UNAVAILABLE, which then selects the deterministic answer path.
        evidence_state = getattr(
            input.fact_bundle.evidence_state, "value", input.fact_bundle.evidence_state
        )
        if (
            str(evidence_state).upper() not in {"VERIFIED", "PARTIAL"}
            or not input.fact_bundle.facts
        ):
            self.fallback_reason = "facts_missing"
            return self._unavailable(self.fallback_reason)
        if any(
            getattr(fact.verification, "value", fact.verification) not in {"VERIFIED", "PARTIAL"}
            or not fact.evidence_ids
            for fact in input.fact_bundle.facts
        ):
            self.fallback_reason = "unverified_fact"
            return self._unavailable(self.fallback_reason)
        # The sidecar must never receive a raw URL, provider field, or prompt
        # instruction even when a caller accidentally bypasses the orchestrator.
        if re.search(r"https?://|www\.", input.sanitized_question, re.IGNORECASE) or re.search(
            r"(?:source_ref|evidence_id|provider_url|system\s+prompt|tool\s*call|ignore\s+previous)",
            input.sanitized_question,
            re.IGNORECASE,
        ):
            self.fallback_reason = "unsanitized_question"
            return self._unavailable(self.fallback_reason)
        if input.remaining_ms <= 20:
            self.fallback_reason = "deadline"
            return RuntimeResult(
                status=RuntimeStatus.TIMEOUT,
                draft_markdown=None,
                blocks=[],
                used_fact_ids=[],
                finish_reason="deadline",
                latency_ms=0,
                error_code="COMPOSER_UNAVAILABLE",
            )
        cancel.raise_if_cancelled()
        # The local mock does not make an external call.  A real sidecar can be
        # implemented behind this method without exposing a ProviderPort.
        try:
            return await asyncio.wait_for(
                self.fallback.compose(input, cancel), timeout=max(self.timeout_ms, 1) / 1000
            )
        except asyncio.TimeoutError:
            self.fallback_reason = "timeout"
            return RuntimeResult(
                status=RuntimeStatus.TIMEOUT,
                finish_reason="timeout",
                latency_ms=self.timeout_ms,
                error_code="COMPOSER_UNAVAILABLE",
            )


__all__ = ["HermesRuntimeAdapter", "TemplateRuntime"]
