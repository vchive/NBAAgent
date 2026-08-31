"""Application ports and runtime-neutral DTOs.

Concrete HTTP clients, stores, providers, and Hermes adapters implement these
protocols.  Keeping the interfaces here allows the fixture vertical slice to
run without network access or a model installation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.errors import ProviderError, RequestCancelledError
from ..domain.models import (
    AnswerBlock,
    ConversationContext,
    Evidence,
    FactBundle,
    Game,
    GameBundle,
    GameFilters,
    HistoryQuery,
    HistoryRecord,
    NewsItem,
    NewsQuery,
    PlayByPlayBundle,
    QueryIntent,
    SeasonLabel,
    Standing,
    StatLine,
    StatsQuery,
)


class RuntimeStatus(StrEnum):
    OK = "OK"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    UNSAFE = "UNSAFE"


class HermesStatus(StrEnum):
    OK = "OK"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    UNSAFE = "UNSAFE"


class PortModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ProviderResult[T](PortModel):
    data: T | None = None
    evidence: list[Evidence] = Field(default_factory=list, max_length=500)
    partial: bool = False
    # Set by ProviderGateway when the deterministic fallback provider supplied
    # the payload. This stays inside the application boundary so callers can
    # avoid presenting a stale snapshot as a live, date-sensitive answer.
    used_fallback: bool = False
    error: ProviderError | None = None
    retrieved_at_utc: datetime

    @field_validator("retrieved_at_utc")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at_utc must include a timezone")
        return value


class RequestBudget:
    """Request-scoped deadline and provider-operation budget."""

    def __init__(
        self,
        deadline_at_utc: datetime,
        *,
        max_provider_operations: int = 4,
        max_retries_per_operation: int = 2,
        clock: Any | None = None,
    ) -> None:
        if deadline_at_utc.tzinfo is None or deadline_at_utc.utcoffset() is None:
            raise ValueError("deadline_at_utc must include a timezone")
        if max_provider_operations < 0 or max_retries_per_operation < 0:
            raise ValueError("budget limits must be non-negative")
        self.deadline_at_utc = deadline_at_utc.astimezone(UTC)
        self.max_provider_operations = max_provider_operations
        self.max_retries_per_operation = max_retries_per_operation
        self.provider_operations = 0
        self._clock = clock
        # The gateway reserves an operation before invoking a provider.  A
        # concrete adapter may also call ``reserve_operation`` (which is
        # useful when it is used directly in tests); the one-shot hand-off
        # below prevents that adapter-level check from counting the same
        # downstream request twice.
        self._gateway_reservation = False

    def _now(self) -> datetime:
        if self._clock is None:
            return datetime.now(UTC)
        value = self._clock() if callable(self._clock) else self._clock.now_utc()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("budget clock must return an aware timestamp")
        return value.astimezone(UTC)

    def remaining_ms(self) -> int:
        remaining = (self.deadline_at_utc - self._now()).total_seconds() * 1000
        return max(0, int(remaining))

    @property
    def operations_used(self) -> int:
        return self.provider_operations

    def reserve_operation(self) -> bool:
        """Reserve one downstream operation if deadline/capacity permit."""

        if self._gateway_reservation:
            self._gateway_reservation = False
            return True
        if self.remaining_ms() <= 0 or self.provider_operations >= self.max_provider_operations:
            return False
        self.provider_operations += 1
        return True

    def reserve_gateway_operation(self) -> bool:
        """Reserve an operation on behalf of a gateway invocation.

        Providers are allowed to perform their own reservation as a direct
        port call.  The gateway uses this method so either style shares the
        same request-wide operation cap without double-counting.
        """

        if self.remaining_ms() <= 0 or self.provider_operations >= self.max_provider_operations:
            return False
        self.provider_operations += 1
        self._gateway_reservation = True
        return True

    def clear_gateway_reservation(self) -> None:
        """Clear an unconsumed hand-off after a provider that lacks the hook."""

        self._gateway_reservation = False

    consume_operation = reserve_operation

    def can_retry(self, retry_index: int) -> bool:
        return (
            retry_index >= 0
            and retry_index < self.max_retries_per_operation
            and self.remaining_ms() > 0
        )

    def raise_if_expired(self) -> None:
        if self.remaining_ms() <= 0:
            raise TimeoutError("request deadline exceeded")


class CancelToken:
    """Cooperative cancellation token propagated to providers and runtimes."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RequestCancelledError()

    async def wait(self) -> None:
        await self._event.wait()


class ToolPolicy(PortModel):
    tools: list[str] = Field(default_factory=list, max_length=0)
    shell: bool = False
    filesystem: Literal["none"] = "none"
    network: Literal["deny"] = "deny"
    mcp: bool = False
    skills: bool = False
    memory: bool = False
    subagents: bool = False
    max_turns: int = Field(default=1, ge=1, le=1)

    @model_validator(mode="after")
    def _locked(self) -> ToolPolicy:
        if self.tools or self.shell or self.mcp or self.skills or self.memory or self.subagents:
            raise ValueError("Hermes tool policy must keep all tools disabled")
        return self


class StylePolicy(PortModel):
    locale: str = "zh-CN"
    address_user_as: str = "您"
    tone: str = "official-neutral-data-driven"
    require_fact_labels: bool = True
    require_analysis_labels: bool = True
    max_sentences: int | None = Field(default=None, ge=1, le=100)


class ComposerInput(PortModel):
    contract_version: Literal["composer.v1"] = "composer.v1"
    request_id: UUID
    opaque_session_id: str = Field(min_length=1, max_length=128)
    deadline_at_utc: datetime
    remaining_ms: int = Field(ge=0)
    locale: Literal["zh-CN"] = "zh-CN"
    display_timezone: str = "Asia/Shanghai"
    sanitized_question: str = Field(min_length=1, max_length=2000)
    intent: QueryIntent
    fact_bundle: FactBundle
    style_policy: StylePolicy = Field(default_factory=StylePolicy)
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)

    @field_validator("deadline_at_utc")
    @classmethod
    def _aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at_utc must include a timezone")
        return value


class RuntimeUsage(PortModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class RuntimeResult(PortModel):
    status: RuntimeStatus
    draft_markdown: str | None = None
    blocks: list[AnswerBlock] = Field(default_factory=list, max_length=64)
    used_fact_ids: list[str] = Field(default_factory=list, max_length=256)
    finish_reason: str | None = Field(default=None, max_length=200)
    usage: RuntimeUsage | None = None
    latency_ms: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, max_length=120)


class CapabilityManifest(PortModel):
    hermes_version: str = "disabled"
    hermes_commit: str = ""
    policy_version: str = "v1"
    policy_hash: str = ""
    tools_hash: str = ""
    tools_enabled: list[str] = Field(default_factory=list, max_length=0)
    network_mode: Literal["deny", "model_egress_only"] = "deny"
    filesystem_mode: Literal["none"] = "none"
    sandbox_uid: int = Field(default=0, ge=0)
    read_only_fs: bool = True

    @model_validator(mode="after")
    def _no_tools(self) -> CapabilityManifest:
        if self.tools_enabled or self.filesystem_mode != "none":
            raise ValueError("Hermes capability manifest must disable tools/filesystem")
        return self


class SafetyPort(Protocol):
    async def classify(self, text: str): ...


class ContextPort(Protocol):
    async def load(
        self, session_id: UUID, version: int | None = None
    ) -> ConversationContext | None: ...

    async def save(self, context: ConversationContext, expected_version: int) -> None: ...


class ProviderPort(Protocol):
    async def search_games(
        self, filters: GameFilters, budget: RequestBudget
    ) -> ProviderResult[list[Game]]: ...

    async def get_game_summary(
        self, game_id: str, budget: RequestBudget
    ) -> ProviderResult[GameBundle]: ...

    async def get_play_by_play(
        self, game_id: str, budget: RequestBudget
    ) -> ProviderResult[PlayByPlayBundle]: ...

    async def get_player_stats(
        self, query: StatsQuery, budget: RequestBudget
    ) -> ProviderResult[list[StatLine]]: ...

    async def get_team_stats(
        self, query: StatsQuery, budget: RequestBudget
    ) -> ProviderResult[list[StatLine]]: ...

    async def get_standings(
        self, season: SeasonLabel, budget: RequestBudget
    ) -> ProviderResult[list[Standing]]: ...

    async def get_history(
        self, query: HistoryQuery, budget: RequestBudget
    ) -> ProviderResult[list[HistoryRecord]]: ...

    async def search_news(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]: ...


class AgentRuntimePort(Protocol):
    async def compose(self, input: ComposerInput, cancel: CancelToken) -> RuntimeResult: ...


class AnswerComposerPort(Protocol):
    async def compose(self, input: ComposerInput, cancel: CancelToken) -> RuntimeResult: ...


class OutputGuardPort(Protocol):
    async def validate(self, result: RuntimeResult, facts: FactBundle) -> RuntimeResult: ...


class EventSink(Protocol):
    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None: ...


__all__ = [
    "AgentRuntimePort",
    "AnswerComposerPort",
    "CapabilityManifest",
    "CancelToken",
    "ComposerInput",
    "ContextPort",
    "EventSink",
    "HermesStatus",
    "OutputGuardPort",
    "PortModel",
    "ProviderPort",
    "ProviderResult",
    "RequestBudget",
    "RuntimeResult",
    "RuntimeStatus",
    "SafetyPort",
    "StylePolicy",
    "ToolPolicy",
]
