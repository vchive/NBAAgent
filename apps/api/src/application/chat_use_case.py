"""Shared synchronous/SSE chat orchestration."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from apps.api.src.application.context_manager import ContextManager
from apps.api.src.application.parser import IntentParser, ParseResult
from apps.api.src.application.ports import (
    CancelToken,
    ComposerInput,
    RequestBudget,
    RuntimeResult,
    RuntimeStatus,
    StylePolicy,
    ToolPolicy,
)
from apps.api.src.application.query_planner import QueryPlan, QueryPlanner
from apps.api.src.application.runtime_selector import RuntimeSelector
from apps.api.src.application.template_composer import TemplateComposer
from apps.api.src.domain.derivation import (
    DerivedResult,
    derive_game_totals,
    derive_leaders,
    derive_pbp,
    derive_series,
)
from apps.api.src.domain.errors import AgentError, OutputBlockedError, ProviderErrorKind
from apps.api.src.domain.models import (
    AnswerBlock,
    AnswerBlockType,
    ChatRequest,
    DraftAnswer,
    EntityKind,
    EntityRef,
    EvidenceState,
    FactAssertion,
    FactBundle,
    Game,
    GameBundle,
    HistoryRecord,
    IntentName,
    NewsItem,
    SafetyOutcome,
    Standing,
    VerificationState,
)
from apps.api.src.domain.safety import OutputGuard, OutputGuardError, SafetyGuard
from apps.api.src.domain.time_policy import SystemClock, format_beijing, now_utc
from apps.api.src.domain.verifier import (
    verify_bundle,
    verify_game,
    verify_premise,
    verify_stat_lines,
)
from apps.api.src.infrastructure.admission import AdmissionController
from apps.api.src.infrastructure.hermes_runtime import HermesRuntimeAdapter, TemplateRuntime
from apps.api.src.infrastructure.session_store import InMemorySessionStore
from apps.api.src.infrastructure.telemetry import QueryTelemetry, TelemetrySink, hash_text


@dataclass(slots=True)
class ChatResult:
    request_id: UUID
    session_id: UUID
    status: str
    answer_markdown: str
    blocks: list[Any] = field(default_factory=list)
    as_of_beijing: str | None = None
    evidence_state: str = "none"
    corrections: list[Any] = field(default_factory=list)
    follow_up: str | None = None
    latency_ms: int = 0
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        def dump(value: Any) -> Any:
            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json")
            if isinstance(value, UUID):
                return str(value)
            if isinstance(value, list):
                return [dump(item) for item in value]
            if isinstance(value, dict):
                return {key: dump(item) for key, item in value.items()}
            return value

        payload = {
            "request_id": str(self.request_id),
            "session_id": str(self.session_id),
            "status": self.status,
            "answer_markdown": self.answer_markdown,
            "blocks": dump(self.blocks),
            "as_of_beijing": self.as_of_beijing,
            "evidence_state": self.evidence_state,
            "corrections": dump(self.corrections),
            "follow_up": self.follow_up,
            "latency_ms": self.latency_ms,
        }
        if self.error is not None:
            payload = {
                "request_id": str(self.request_id),
                "session_id": str(self.session_id),
                "status": "failed",
                "error": dump(self.error),
            }
        return payload


class _NullSink:
    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        return None


async def _emit(sink: Any, name: str, payload: Mapping[str, Any]) -> None:
    if sink is None:
        return
    result = sink.emit(name, payload) if hasattr(sink, "emit") else sink(name, payload)
    if inspect.isawaitable(result):
        await result


class ChatUseCase:
    """Evidence-first state machine used by both HTTP endpoints."""

    def __init__(
        self,
        provider: Any,
        *,
        session_store: InMemorySessionStore | None = None,
        cache: Any | None = None,
        safety_guard: SafetyGuard | None = None,
        output_guard: type[OutputGuard] | OutputGuard | None = None,
        settings: Any | None = None,
        clock: Any | None = None,
        telemetry: TelemetrySink | None = None,
        runtime: Any | None = None,
        hermes_runtime: Any | None = None,
        gateway: Any | None = None,
        admission: AdmissionController | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock or SystemClock()
        self.safety_guard = safety_guard or SafetyGuard()
        self.output_guard = output_guard or OutputGuard
        self.session_store = session_store or InMemorySessionStore(
            ttl_seconds=getattr(settings, "session_ttl_seconds", 86_400),
            max_turns=getattr(settings, "max_session_turns", 8),
            clock=self.clock,
        )
        self.context_manager = ContextManager(
            self.session_store,
            ttl_seconds=getattr(settings, "session_ttl_seconds", 86_400),
            max_turns=getattr(settings, "max_session_turns", 8),
            max_summary_bytes=getattr(settings, "max_session_bytes", 16_384),
            clock=self.clock,
        )
        if gateway is None:
            from apps.api.src.infrastructure.cache import InMemoryTTLCache
            from apps.api.src.providers.gateway import ProviderGateway

            gateway = ProviderGateway(
                provider,
                cache=cache
                or InMemoryTTLCache(max_entries=getattr(settings, "cache_max_entries", 10_000)),
                max_retries=getattr(settings, "provider_max_retries", 2),
            )
        self.gateway = gateway
        self.provider = provider
        self.parser = IntentParser(clock=self.clock)
        self.planner = QueryPlanner()
        self.template_composer = TemplateComposer()
        self.runtime = runtime or TemplateRuntime(composer=self.template_composer)
        self.hermes_runtime = hermes_runtime or HermesRuntimeAdapter(
            fallback=self.runtime,
            mode=getattr(settings, "hermes_lite_mode", "off"),
            timeout_ms=getattr(settings, "hermes_lite_timeout_ms", 2500),
            endpoint=getattr(settings, "hermes_lite_endpoint", ""),
        )
        self.runtime_selector = RuntimeSelector(
            template_runtime=self.runtime,
            hermes_runtime=self.hermes_runtime,
            profile=getattr(settings, "runtime_profile", "template"),
        )
        self.telemetry = telemetry or TelemetrySink()
        self.admission = admission or AdmissionController(
            max_inflight=getattr(settings, "max_inflight_requests", 32),
            queue_max_depth=getattr(settings, "queue_max_depth", 64),
            queue_wait_ms=getattr(settings, "queue_wait_deadline_ms", 1000),
        )

    def _now(self) -> datetime:
        return now_utc(self.clock)

    async def _classify(self, text: str):
        value = self.safety_guard.classify(text)
        if inspect.isawaitable(value):
            value = await value
        return value

    async def _emit_replay(self, sink: Any, result: ChatResult) -> None:
        """Replay an idempotent result on an SSE sink.

        A duplicate synchronous request is projected by the HTTP route, but a
        duplicate POST-SSE request still needs a complete event sequence.  The
        idempotency record stores the original envelope, so replaying it here
        keeps the stream deterministic and prevents a second provider call.
        """

        await _emit(
            sink,
            "run.started",
            {"request_id": result.request_id, "session_id": result.session_id},
        )
        if result.error is not None or result.status == "failed":
            await _emit(sink, "run.error", result.to_dict())
        else:
            if result.status == "blocked":
                await _emit(sink, "safety.blocked", {"message": result.answer_markdown})
            elif result.status == "needs_clarification":
                await _emit(
                    sink,
                    "clarification.required",
                    {"question": result.follow_up or result.answer_markdown},
                )
            await _emit(sink, "message.completed", result.to_dict())

    async def handle(
        self,
        request: ChatRequest | Mapping[str, Any],
        *,
        event_sink: Any | None = None,
        cancel: CancelToken | None = None,
        request_id: UUID | None = None,
    ) -> ChatResult:
        started = time.monotonic()
        sink = event_sink or _NullSink()
        token = cancel or CancelToken()
        try:
            req = (
                request if isinstance(request, ChatRequest) else ChatRequest.model_validate(request)
            )
        except Exception:
            request_id, session_id = uuid4(), uuid4()
            return ChatResult(
                request_id,
                session_id,
                "failed",
                "请求格式不正确。",
                latency_ms=int((time.monotonic() - started) * 1000),
                error={
                    "code": "INVALID_PAYLOAD",
                    "retryable": False,
                    "message": "请求格式不正确，请缩短问题或补充必要条件。",
                },
            )
        request_id = request_id or uuid4()
        session_id = req.session_id or uuid4()
        telemetry = QueryTelemetry(
            request_id=request_id,
            session_hash=InMemorySessionStore.hash_session(session_id),
            message_hash=hash_text(req.message),
            deadline_at_utc=self._now()
            + timedelta(milliseconds=getattr(self.settings, "request_deadline_ms", 10_000)),
        )
        client_id = req.client_message_id
        owner = True
        if client_id:
            owner, record = await self.session_store.reserve_idempotency(
                session_id,
                client_id,
                request_id,
                message_hash=telemetry.message_hash,
            )
            if not owner:
                # Reusing a client id with different content is a payload
                # conflict, not a replay.  Refuse it before touching the
                # provider/cache while retaining the original idempotency
                # record for legitimate retries.
                if (
                    record.message_hash is not None
                    and record.message_hash != telemetry.message_hash
                ):
                    await _emit(
                        sink,
                        "run.started",
                        {"request_id": request_id, "session_id": session_id},
                    )
                    conflict = type(
                        "IdempotencyConflict",
                        (),
                        {
                            "kind": ProviderErrorKind.SCHEMA_MISMATCH,
                            "retryable": False,
                            "safe_message": "该请求标识已用于其他问题，请换一个请求标识。",
                        },
                    )()
                    return await self._technical_failure(
                        request_id,
                        session_id,
                        conflict,
                        telemetry,
                        started,
                        sink,
                        client_id=None,
                        code="INVALID_PAYLOAD",
                    )
                replay = await self.session_store.replay_or_wait(session_id, client_id, timeout=10)
                if isinstance(replay, ChatResult):
                    await self._emit_replay(sink, replay)
                    return replay
                if isinstance(replay, dict):
                    replay_result = ChatResult(
                        request_id=UUID(str(replay["request_id"])),
                        session_id=UUID(str(replay["session_id"])),
                        status=replay.get("status", "failed"),
                        answer_markdown=replay.get("answer_markdown", ""),
                        blocks=replay.get("blocks", []),
                        as_of_beijing=replay.get("as_of_beijing"),
                        evidence_state=replay.get("evidence_state", "none"),
                        corrections=replay.get("corrections", []),
                        follow_up=replay.get("follow_up"),
                        latency_ms=replay.get("latency_ms", 0),
                        error=replay.get("error"),
                    )
                    await self._emit_replay(sink, replay_result)
                    return replay_result
                busy = ChatResult(
                    request_id,
                    session_id,
                    "failed",
                    "请求仍在处理中，请稍后重试。",
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error={
                        "code": "SERVICE_BUSY",
                        "retryable": True,
                        "message": "请求仍在处理中，请稍后重试。",
                    },
                )
                await self._emit_replay(sink, busy)
                return busy
        try:
            await _emit(sink, "run.started", {"request_id": request_id, "session_id": session_id})
            telemetry.transition("RECEIVED")
            token.raise_if_cancelled()
            safety = await self._classify(req.message)
            telemetry.safety_category = safety.category.value
            telemetry.transition("SAFETY_CHECKED", safety=safety.outcome.value)
            if safety.outcome is not SafetyOutcome.ALLOW:
                from apps.api.src.domain.safety import SafetyGuard as SG

                draft = SG.as_draft(safety)
                status = "blocked" if safety.outcome is SafetyOutcome.BLOCK else "no_data"
                result = self._result_from_draft(
                    request_id, session_id, status, draft, started, as_of=None
                )
                telemetry.finish(outcome=status, total_latency_ms=result.latency_ms)
                telemetry.provider_call_count = telemetry.cache_read_count = (
                    telemetry.cache_write_count
                ) = 0
                self.telemetry.record(telemetry)
                if client_id:
                    await self.session_store.complete_idempotency(session_id, client_id, result)
                if status == "blocked":
                    await _emit(sink, "safety.blocked", {"message": draft.markdown})
                else:
                    await _emit(sink, "run.status", {"stage": "scope", "text": "已确认问题范围"})
                await _emit(sink, "message.completed", result.to_dict())
                return result

            token.raise_if_cancelled()
            context = await self.context_manager.ensure(
                session_id, req.client_timezone or "Asia/Shanghai"
            )
            if (
                req.client_timezone
                and context.turn_count == 0
                and context.timezone != req.client_timezone
            ):
                context = context.model_copy(update={"timezone": req.client_timezone})
            telemetry.transition("CONTEXT_RESOLVED")
            parser = IntentParser(clock=self.clock, input_timezone=context.timezone)
            try:
                parsed = parser.parse(req.message, context)
            except (TypeError, ValueError):
                # Calendar/season/window syntax is user input.  A malformed
                # date (for example ``2026-02-30``) must be reported as a
                # non-retryable 400 payload error, never as an upstream 503.
                invalid = type(
                    "InvalidPayloadError",
                    (),
                    {
                        "kind": ProviderErrorKind.SCHEMA_MISMATCH,
                        "retryable": False,
                        "safe_message": "日期、赛季或时间格式不正确，请检查后重试。",
                    },
                )()
                return await self._technical_failure(
                    request_id,
                    session_id,
                    invalid,
                    telemetry,
                    started,
                    sink,
                    client_id,
                    code="INVALID_PAYLOAD",
                )
            telemetry.intent_category = parsed.intent.category.value
            telemetry.intent_name = parsed.intent.intent_name.value
            telemetry.transition("PARSED")
            # Prediction wording is in scope for a basketball assistant, but a
            # future champion is not an already-verifiable historical fact.
            # Short-circuit this branch before planning/provider access so a
            # latest-title fixture can never be presented as a forecast.  The
            # response remains useful by explaining the evidence boundary and
            # offering a fact-backed trend-analysis follow-up.
            prediction_metric_names = {
                getattr(metric, "name", "") for metric in parsed.intent.metrics
            }
            if prediction_metric_names & {
                "championship_prediction",
                "game_outcome_prediction",
            }:
                if "game_outcome_prediction" in prediction_metric_names:
                    prediction_message = (
                        "比赛结果尚未发生，我不能把历史赛果当作确定预测。"
                        "如您指定比赛，我可以基于已核验的战绩、排名和近期表现整理趋势依据，"
                        "但不会给出确定的胜负结论。"
                    )
                else:
                    prediction_message = (
                        "未来冠军尚未产生，我不能把历史冠军当作预测。"
                        "如您指定赛季或球队，我可以基于已核验的战绩、排名和系列赛数据做趋势分析，"
                        "但不会给出确定的夺冠结论。"
                    )
                prediction_follow_up = (
                    "请指定比赛，我再整理已核验的胜负趋势依据。"
                    if "game_outcome_prediction" in prediction_metric_names
                    else "请指定赛季或球队，我再整理已核验的趋势依据。"
                )
                draft = self.template_composer.no_data(
                    message=prediction_message,
                    follow_up=prediction_follow_up,
                )
                result = self._result_from_draft(
                    request_id,
                    session_id,
                    "no_data",
                    draft,
                    started,
                    as_of=None,
                )
                telemetry.evidence_state = "none"
                telemetry.finish(outcome="no_data", total_latency_ms=result.latency_ms)
                # No provider or cache call is made on this branch; retaining
                # explicit zeroes makes the invariant observable in telemetry.
                telemetry.provider_call_count = 0
                telemetry.cache_read_count = 0
                telemetry.cache_write_count = 0
                telemetry.cache_hit_count = 0
                self.telemetry.record(telemetry)
                if client_id:
                    await self.session_store.complete_idempotency(session_id, client_id, result)
                await _emit(sink, "run.status", {"stage": "scope", "text": "已确认问题范围"})
                await _emit(sink, "message.completed", result.to_dict())
                return result
            if parsed.missing_slots or parsed.ambiguity_reasons:
                question = self._clarification(parsed)
                from apps.api.src.domain.models import AnswerBlock, AnswerBlockType, DraftAnswer

                draft = DraftAnswer(
                    markdown=question,
                    blocks=[AnswerBlock(type=AnswerBlockType.TEXT, content=question)],
                    evidence_state=EvidenceState.NONE,
                    follow_up=question,
                )
                result = self._result_from_draft(
                    request_id, session_id, "needs_clarification", draft, started, as_of=None
                )
                telemetry.finish(outcome="needs_clarification", total_latency_ms=result.latency_ms)
                self.telemetry.record(telemetry)
                if client_id:
                    await self.session_store.complete_idempotency(session_id, client_id, result)
                await _emit(sink, "clarification.required", {"question": question})
                await _emit(sink, "message.completed", result.to_dict())
                return result
            plan = self.planner.build(parsed.intent)
            telemetry.transition("PLAN_READY")
            if plan is None:
                question = "请补充具体的球队、球员或比赛，我再帮您核对。"
                from apps.api.src.domain.models import AnswerBlock, AnswerBlockType, DraftAnswer

                draft = DraftAnswer(
                    markdown=question,
                    blocks=[AnswerBlock(type=AnswerBlockType.TEXT, content=question)],
                    evidence_state=EvidenceState.NONE,
                    follow_up=question,
                )
                result = self._result_from_draft(
                    request_id, session_id, "needs_clarification", draft, started, as_of=None
                )
                telemetry.finish(outcome="needs_clarification", total_latency_ms=result.latency_ms)
                self.telemetry.record(telemetry)
                if client_id:
                    await self.session_store.complete_idempotency(session_id, client_id, result)
                await _emit(sink, "clarification.required", {"question": question})
                await _emit(sink, "message.completed", result.to_dict())
                return result
            await _emit(sink, "run.status", {"stage": "retrieving", "text": "正在查找相关比赛数据"})
            telemetry.transition("RETRIEVING")
            deadline = self._now() + timedelta(
                milliseconds=getattr(self.settings, "request_deadline_ms", 10_000)
            )
            budget = RequestBudget(
                deadline,
                max_provider_operations=getattr(self.settings, "max_provider_operations", 4),
                max_retries_per_operation=getattr(self.settings, "provider_max_retries", 2),
                clock=self.clock,
            )
            admission_result, lease, queue_wait = await self._await_with_cancel(
                self.admission.acquire(
                    timeout_ms=getattr(self.settings, "queue_wait_deadline_ms", 1000)
                ),
                token,
            )
            telemetry.admission_result = admission_result.value
            telemetry.queue_wait_ms = queue_wait
            if lease is None:
                return await self._technical_failure(
                    request_id,
                    session_id,
                    type(
                        "E",
                        (),
                        {
                            "kind": ProviderErrorKind.TIMEOUT,
                            "retryable": True,
                            "safe_message": "当前请求较多，请稍后重试。",
                        },
                    )(),
                    telemetry,
                    started,
                    sink,
                    client_id,
                    code="SERVICE_BUSY",
                )
            before = self._gateway_counters()
            try:
                provider_result = await self._call_plan(plan, budget, token)
            finally:
                await lease.release()
            after = self._gateway_counters()
            telemetry.provider_call_count = max(
                0, after.get("provider_call_count", 0) - before.get("provider_call_count", 0)
            )
            telemetry.cache_read_count = max(
                0, after.get("cache_read_count", 0) - before.get("cache_read_count", 0)
            )
            telemetry.cache_write_count = max(
                0, after.get("cache_write_count", 0) - before.get("cache_write_count", 0)
            )
            telemetry.cache_hit_count = max(
                0, after.get("cache_hit_count", 0) - before.get("cache_hit_count", 0)
            )
            if provider_result.error is not None:
                return await self._technical_failure(
                    request_id,
                    session_id,
                    provider_result.error,
                    telemetry,
                    started,
                    sink,
                    client_id,
                )
            telemetry.transition("NORMALIZED")
            data = provider_result.data
            if data is None or data == []:
                draft = self.template_composer.no_data()
                result = self._result_from_draft(
                    request_id, session_id, "no_data", draft, started, as_of=None
                )
                telemetry.evidence_state = "none"
                telemetry.finish(outcome="no_data", total_latency_ms=result.latency_ms)
                self.telemetry.record(telemetry)
                if client_id:
                    await self.session_store.complete_idempotency(session_id, client_id, result)
                await _emit(sink, "message.completed", result.to_dict())
                return result
            await _emit(sink, "run.status", {"stage": "verifying", "text": "正在核对比赛数据"})
            facts, game, bundle, derived = self._facts_for(
                parsed,
                data,
                provider_result.evidence,
                provider_partial=bool(provider_result.partial),
            )
            # A provider may return usable rows together with a schema warning
            # or omitted fields.  Preserve that uncertainty all the way to
            # the public envelope; a successful normalizer result must never
            # upgrade a partial upstream response to VERIFIED.
            if provider_result.partial and facts.evidence_state is EvidenceState.VERIFIED:
                facts = FactBundle(
                    facts=facts.facts,
                    missing=facts.missing,
                    corrections=facts.corrections,
                    evidence_state=EvidenceState.PARTIAL,
                )
            telemetry.evidence_state = facts.evidence_state.value.lower()
            telemetry.transition(
                "VERIFIED" if facts.evidence_state is EvidenceState.VERIFIED else "UNVERIFIED"
            )
            if derived is not None and derived.facts:
                facts = FactBundle(
                    facts=[*facts.facts, *derived.facts],
                    missing=[*facts.missing, *derived.missing],
                    evidence_state=(
                        EvidenceState.PARTIAL
                        if derived.partial or facts.evidence_state is EvidenceState.PARTIAL
                        else facts.evidence_state
                    ),
                )
            if parsed.intent.premise_claims:
                corrections = verify_premise(parsed.intent.premise_claims, facts)
            else:
                corrections = []
            telemetry.transition("DERIVED")
            await _emit(sink, "run.status", {"stage": "composing", "text": "正在整理回答"})
            token.raise_if_cancelled()
            draft = await self._compose(
                request_id=request_id,
                session_id=session_id,
                parsed=parsed,
                facts=facts,
                game=game,
                bundle=bundle,
                derived=derived,
                retrieved_at=provider_result.retrieved_at_utc,
                corrections=corrections,
                budget=budget,
                token=token,
                telemetry=telemetry,
            )
            try:
                guarded = self.output_guard.validate(draft, facts)
            except (OutputGuardError, ValueError, TypeError):
                telemetry.error_code = "OUTPUT_BLOCKED"
                return await self._technical_failure(
                    request_id,
                    session_id,
                    type(
                        "E",
                        (),
                        {
                            "kind": ProviderErrorKind.SCHEMA_MISMATCH,
                            "retryable": False,
                            "safe_message": "回答未通过安全校验，请换一种问法。",
                        },
                    )(),
                    telemetry,
                    started,
                    sink,
                    client_id,
                    code="OUTPUT_BLOCKED",
                )
            telemetry.transition("COMPOSED")
            telemetry.transition("OUTPUT_GUARDED")
            try:
                await self.context_manager.commit(
                    context, intent=parsed.intent, facts=facts, answer=guarded.markdown
                )
            except Exception:
                # A context write failure must not invalidate a verified answer; the next
                # turn will ask for clarification rather than crossing sessions.
                pass
            result = self._result_from_draft(
                request_id,
                session_id,
                "completed",
                guarded,
                started,
                as_of=format_beijing(provider_result.retrieved_at_utc),
            )
            telemetry.finish(outcome="completed", total_latency_ms=result.latency_ms)
            self.telemetry.record(telemetry)
            if client_id:
                await self.session_store.complete_idempotency(session_id, client_id, result)
            # Deltas are emitted only after verification/composition.
            for chunk in _chunks(guarded.markdown, 120):
                await _emit(sink, "message.delta", {"text": chunk})
            await _emit(sink, "message.completed", result.to_dict())
            return result
        except asyncio.CancelledError:
            token.cancel()
            if client_id:
                await self.session_store.fail_idempotency(session_id, client_id)
            raise
        except AgentError as exc:
            result = ChatResult(
                request_id,
                session_id,
                "failed",
                exc.safe_message,
                latency_ms=int((time.monotonic() - started) * 1000),
                error={
                    "code": exc.code.value,
                    "retryable": exc.retryable,
                    "message": exc.safe_message,
                },
            )
            telemetry.error_code = exc.code.value
            telemetry.finish(outcome="failed", total_latency_ms=result.latency_ms)
            self.telemetry.record(telemetry)
            if client_id:
                await self.session_store.fail_idempotency(session_id, client_id)
            await _emit(sink, "run.error", result.to_dict())
            return result
        except Exception:
            result = ChatResult(
                request_id,
                session_id,
                "failed",
                "服务暂时不可用，请稍后重试。",
                latency_ms=int((time.monotonic() - started) * 1000),
                error={
                    "code": "SERVICE_BUSY",
                    "retryable": True,
                    "message": "服务暂时不可用，请稍后重试。",
                },
            )
            telemetry.error_code = "SERVICE_BUSY"
            telemetry.finish(outcome="failed", total_latency_ms=result.latency_ms)
            self.telemetry.record(telemetry)
            if client_id:
                await self.session_store.fail_idempotency(session_id, client_id)
            await _emit(sink, "run.error", result.to_dict())
            return result

    async def _call_plan(self, plan: QueryPlan, budget: RequestBudget, token: CancelToken):
        token.raise_if_cancelled()
        method = getattr(self.gateway, plan.operation)
        return await self._await_with_cancel(
            method(*plan.args, **plan.kwargs, budget=budget), token
        )

    @staticmethod
    async def _await_with_cancel(awaitable: Any, token: CancelToken) -> Any:
        """Cancel an in-flight provider/runtime operation when the request token fires."""

        token.raise_if_cancelled()
        operation = asyncio.ensure_future(awaitable)
        cancellation = asyncio.create_task(token.wait())
        try:
            done, _ = await asyncio.wait(
                {operation, cancellation}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancellation in done:
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                token.raise_if_cancelled()
            return await operation
        except asyncio.CancelledError:
            # Cancellation of the owning request (for example an SSE client
            # disconnect) does not necessarily set the cooperative token.  In
            # either case, make sure the downstream awaitable is stopped before
            # propagating cancellation to the state machine.
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            raise
        finally:
            if not cancellation.done():
                cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)

    async def _compose(
        self,
        *,
        request_id: UUID,
        session_id: UUID,
        parsed: ParseResult,
        facts: FactBundle,
        game: Game | None,
        bundle: GameBundle | None,
        derived: DerivedResult | None,
        retrieved_at: datetime,
        corrections: list[Any],
        budget: RequestBudget,
        token: CancelToken,
        telemetry: QueryTelemetry,
    ) -> DraftAnswer:
        """Select Hermes only for analysis intents, with a deterministic fallback.

        The runtime receives no raw provider data, URL, session identifier, cache,
        or tool handle. Objective answers keep using the richer deterministic
        renderer so factual formatting cannot drift.
        """

        base = self.template_composer.compose(
            parsed.intent,
            facts,
            game=game,
            bundle=bundle,
            derived=derived,
            retrieved_at=retrieved_at,
            corrections=corrections,
        )
        selected = self.runtime_selector.for_intent(parsed.intent.intent_name)
        if selected is self.runtime:
            return base

        telemetry.hermes_mode = getattr(
            self.hermes_runtime,
            "mode",
            getattr(self.settings, "hermes_lite_mode", "off"),
        )
        composer_input = ComposerInput(
            request_id=request_id,
            opaque_session_id=InMemorySessionStore.hash_session(session_id),
            deadline_at_utc=budget.deadline_at_utc,
            remaining_ms=budget.remaining_ms(),
            sanitized_question=self._runtime_question(parsed),
            intent=parsed.intent,
            fact_bundle=facts,
            style_policy=StylePolicy(),
            tool_policy=getattr(self.hermes_runtime, "tool_policy", ToolPolicy()),
        )
        try:
            runtime_value = await self._await_with_cancel(
                selected.compose(composer_input, token), token
            )
            runtime_result = (
                runtime_value
                if isinstance(runtime_value, RuntimeResult)
                else RuntimeResult.model_validate(runtime_value)
            )
        except AgentError:
            raise
        except Exception:
            telemetry.hermes_status = "unavailable"
            telemetry.fallback_reason = "runtime_exception"
            return base

        status = getattr(runtime_result.status, "value", runtime_result.status)
        telemetry.hermes_status = str(status).lower()
        telemetry.fallback_reason = getattr(self.hermes_runtime, "fallback_reason", None)
        if status == RuntimeStatus.UNSAFE.value:
            raise OutputBlockedError()
        if status != RuntimeStatus.OK.value or not runtime_result.draft_markdown:
            telemetry.fallback_reason = telemetry.fallback_reason or str(
                runtime_result.finish_reason or "runtime_unavailable"
            )
            return base

        # The local Hermes-off/embedded spike currently delegates to the
        # deterministic renderer. Keep the richer base draft in that case while
        # still exercising and observing the capability boundary.
        if runtime_result.finish_reason == "template":
            return base

        blocks = list(runtime_result.blocks)
        if not blocks:
            blocks = [
                AnswerBlock(type=AnswerBlockType.ANALYSIS, content=runtime_result.draft_markdown)
            ]
        freshness = f"数据截至北京时间 {format_beijing(retrieved_at)}，已核验。"
        markdown = runtime_result.draft_markdown.strip()
        if "数据截至北京时间" not in markdown:
            markdown = f"{markdown}\n\n{freshness}"
            blocks.append(AnswerBlock(type=AnswerBlockType.TEXT, content=freshness))
        return DraftAnswer(
            markdown=markdown,
            blocks=blocks,
            evidence_state=facts.evidence_state,
            corrections=base.corrections,
            follow_up=base.follow_up,
        )

    @staticmethod
    def _runtime_question(parsed: ParseResult) -> str:
        """Build a provider-free, injection-free semantic question for Hermes."""

        entities = "、".join(item.display_name for item in parsed.intent.entities[:8])
        metrics = "、".join(item.name for item in parsed.intent.metrics[:8])
        parts = [f"意图：{parsed.intent.intent_name.value}"]
        if entities:
            parts.append(f"对象：{entities}")
        if metrics:
            parts.append(f"指标：{metrics}")
        if parsed.intent.period is not None:
            parts.append(f"节次：{parsed.intent.period}")
        return "；".join(parts)

    def _gateway_counters(self) -> dict[str, int]:
        if hasattr(self.gateway, "counters"):
            return self.gateway.counters()
        return {
            "provider_call_count": getattr(self.gateway, "call_count", 0),
            "cache_read_count": 0,
            "cache_write_count": 0,
            "cache_hit_count": 0,
        }

    def _facts_for(
        self,
        parsed: ParseResult,
        data: Any,
        evidence: list[Any],
        *,
        provider_partial: bool = False,
    ):
        game: Game | None = None
        bundle: GameBundle | None = None
        derived: DerivedResult | None = None
        if isinstance(data, GameBundle):
            bundle = data
            game = data.game
            verified = verify_bundle(data, [item.evidence_id for item in evidence])
            facts = verified.facts
            if parsed.intent.intent_name is IntentName.PLAY_BY_PLAY and data.plays:
                derived = derive_pbp(
                    data.plays,
                    parsed.intent.clock_window
                    or __import__(
                        "apps.api.src.domain.time_policy", fromlist=["game_end_window"]
                    ).game_end_window(5),
                    period=parsed.intent.period,
                )
            elif (
                parsed.intent.metrics
                and getattr(parsed.intent.metrics[0].scope, "value", parsed.intent.metrics[0].scope)
                == "SERIES"
            ):
                derived = derive_leaders(data)
            else:
                totals = derive_game_totals(data.game, [fact.fact_id for fact in facts.facts])
                leaders = derive_leaders(data)
                derived = DerivedResult(
                    facts=[*totals.facts, *leaders.facts],
                    missing=[*totals.missing, *leaders.missing],
                    partial=totals.partial or leaders.partial,
                )
            if provider_partial and facts.evidence_state is EvidenceState.VERIFIED:
                facts = FactBundle(
                    facts=facts.facts,
                    missing=facts.missing,
                    corrections=facts.corrections,
                    evidence_state=EvidenceState.PARTIAL,
                )
            return facts, game, bundle, derived
        if isinstance(data, list) and data and isinstance(data[0], Game):
            games = data
            # Series aggregate is selected by natural language, not by a model guess.
            if (
                parsed.intent.metrics
                and getattr(parsed.intent.metrics[0].scope, "value", parsed.intent.metrics[0].scope)
                == "SERIES"
            ):
                derived = derive_series(
                    games,
                    series_id=next((item.series_id for item in games if item.series_id), None),
                )
                facts = FactBundle(
                    facts=[],
                    evidence_state=(
                        EvidenceState.PARTIAL
                        if provider_partial or (derived and derived.partial)
                        else EvidenceState.VERIFIED
                    ),
                )
                return facts, None, None, derived
            game_facts = verify_game(games[0], [f"game:{games[0].game_id}"])
            if provider_partial and game_facts.evidence_state is EvidenceState.VERIFIED:
                game_facts = FactBundle(
                    facts=game_facts.facts,
                    missing=game_facts.missing,
                    corrections=game_facts.corrections,
                    evidence_state=EvidenceState.PARTIAL,
                )
            return game_facts, games[0], None, derived
        if isinstance(data, list) and (not data or isinstance(data[0], HistoryRecord)):
            facts_list = []
            for item in data:
                facts_list.append(
                    FactAssertion(
                        fact_id=item.record_id,
                        subject=item.subject
                        or EntityRef(
                            kind=EntityKind.SEASON, canonical_id="nba", display_name="NBA"
                        ),
                        predicate=item.record_type.value.lower(),
                        value=item.value,
                        season=item.season,
                        evidence_ids=[item.evidence_id],
                        verification=VerificationState.VERIFIED,
                    )
                )
            return (
                FactBundle(
                    facts=facts_list,
                    evidence_state=(
                        EvidenceState.PARTIAL
                        if provider_partial and facts_list
                        else EvidenceState.VERIFIED
                        if facts_list
                        else EvidenceState.NONE
                    ),
                ),
                None,
                None,
                None,
            )
        if isinstance(data, list) and data and isinstance(data[0], NewsItem):
            facts_list = []
            for item in data:
                value = {
                    "title": item.title,
                    "summary": item.summary,
                    "published_utc": (
                        item.published_utc.isoformat() if item.published_utc is not None else None
                    ),
                }
                facts_list.append(
                    FactAssertion(
                        fact_id=item.news_id,
                        subject=(
                            item.subject_refs[0]
                            if item.subject_refs
                            else EntityRef(
                                kind=EntityKind.UNKNOWN,
                                canonical_id="news",
                                display_name="NBA 新闻",
                            )
                        ),
                        predicate="news",
                        value=value,
                        evidence_ids=[item.evidence_id],
                        verification=VerificationState.VERIFIED,
                    )
                )
            return (
                FactBundle(
                    facts=facts_list,
                    evidence_state=(
                        EvidenceState.PARTIAL if provider_partial else EvidenceState.VERIFIED
                    ),
                ),
                None,
                None,
                None,
            )
        if isinstance(data, list) and data and isinstance(data[0], Standing):
            facts_list = []
            for item in data:
                values = {"wins": item.wins, "losses": item.losses, "rank": item.rank}
                for predicate, value in values.items():
                    if value is not None:
                        facts_list.append(
                            FactAssertion(
                                fact_id=f"standing:{item.season.label}:{item.team.canonical_id}:{predicate}",
                                subject=item.team,
                                predicate=predicate,
                                value=value,
                                evidence_ids=[f"standings:{item.season.label}"],
                                verification=VerificationState.VERIFIED,
                            )
                        )
            return (
                FactBundle(
                    facts=facts_list,
                    evidence_state=(
                        EvidenceState.PARTIAL
                        if provider_partial and facts_list
                        else EvidenceState.VERIFIED
                        if facts_list
                        else EvidenceState.NONE
                    ),
                ),
                None,
                None,
                None,
            )
        if isinstance(data, list):
            stats = verify_stat_lines(data, [item.evidence_id for item in evidence])
            if provider_partial and stats.evidence_state is EvidenceState.VERIFIED:
                stats = FactBundle(
                    facts=stats.facts,
                    missing=stats.missing,
                    corrections=stats.corrections,
                    evidence_state=EvidenceState.PARTIAL,
                )
            return stats, None, None, None
        if hasattr(data, "events"):
            from apps.api.src.domain.time_policy import game_end_window

            derived = derive_pbp(
                data, parsed.intent.clock_window or game_end_window(5), period=parsed.intent.period
            )
            derived_bundle = FactBundle(
                facts=derived.facts,
                missing=derived.missing,
                evidence_state=(
                    EvidenceState.PARTIAL
                    if provider_partial and derived.facts
                    else derived.evidence_state
                ),
            )
            return derived_bundle, None, None, derived
        return FactBundle(facts=[], evidence_state=EvidenceState.NONE), None, None, None

    @staticmethod
    def _last_message_hint(parsed: ParseResult) -> str:
        # QueryIntent intentionally does not retain raw text; metric scope is the
        # safe signal available to this layer.
        return (
            parsed.intent.intent_name.value
            + " "
            + " ".join(metric.name for metric in parsed.intent.metrics)
        )

    @staticmethod
    def _clarification(parsed: ParseResult) -> str:
        if parsed.ambiguity_reasons:
            return "我找到了多个可能的对象，请补充具体球队、球员或比赛。"
        # Slot names are internal parser vocabulary; never expose them in a
        # conversational response (e.g. ``subject``/``game``).  A compact
        # Chinese label also makes shorthand clarifications actionable.
        slot_labels = {
            "game": "具体比赛",
            "subject": "查询对象",
            "team": "球队",
            "player": "球员",
            "date": "日期",
            "season": "赛季",
            "period": "节次",
        }
        slots = "、".join(slot_labels.get(slot.name, "必要条件") for slot in parsed.missing_slots)
        return f"请补充{slots or '比赛或查询对象'}，我再帮您核对。"

    @staticmethod
    def _result_from_draft(
        request_id: UUID,
        session_id: UUID,
        status: str,
        draft: Any,
        started: float,
        *,
        as_of: str | None,
    ) -> ChatResult:
        return ChatResult(
            request_id=request_id,
            session_id=session_id,
            status=status,
            answer_markdown=draft.markdown,
            blocks=draft.blocks,
            as_of_beijing=as_of,
            evidence_state=draft.evidence_state.value.lower()
            if hasattr(draft.evidence_state, "value")
            else str(draft.evidence_state).lower(),
            corrections=draft.corrections,
            follow_up=draft.follow_up,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        )

    async def _technical_failure(
        self,
        request_id: UUID,
        session_id: UUID,
        error: Any,
        telemetry: QueryTelemetry,
        started: float,
        sink: Any,
        client_id: str | None,
        *,
        code: str | None = None,
    ) -> ChatResult:
        kind = getattr(error, "kind", ProviderErrorKind.HTTP)
        mapping = {
            ProviderErrorKind.TIMEOUT: ("UPSTREAM_TIMEOUT", True, "数据暂时不可用，请稍后重试。"),
            ProviderErrorKind.RATE_LIMITED: (
                "UPSTREAM_RATE_LIMITED",
                True,
                "数据服务暂时繁忙，请稍后重试。",
            ),
            ProviderErrorKind.AUTH: ("UPSTREAM_AUTH", False, "数据服务暂时不可用，请稍后再试。"),
            ProviderErrorKind.INVALID_JSON: (
                "INVALID_UPSTREAM_DATA",
                False,
                "公开数据格式异常，暂时无法核验。",
            ),
            ProviderErrorKind.SCHEMA_MISMATCH: (
                "INVALID_UPSTREAM_DATA",
                False,
                "公开数据格式异常，暂时无法核验。",
            ),
            ProviderErrorKind.NOT_FOUND: ("INVALID_UPSTREAM_DATA", False, "暂无匹配的公开数据。"),
        }
        error_code, retryable, message = mapping.get(
            kind, (code or "SERVICE_BUSY", True, "服务暂时不可用，请稍后重试。")
        )
        if code:
            error_code = code
        if code in {"INVALID_PAYLOAD", "OUTPUT_BLOCKED"}:
            retryable = False
            message = getattr(
                error,
                "safe_message",
                "请求格式不正确，请检查后重试。"
                if code == "INVALID_PAYLOAD"
                else "回答未通过安全校验，请换一种问法。",
            )
        result = ChatResult(
            request_id,
            session_id,
            "failed",
            message,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            error={"code": error_code, "retryable": retryable, "message": message},
        )
        telemetry.error_code = error_code
        telemetry.finish(outcome="failed", total_latency_ms=result.latency_ms)
        self.telemetry.record(telemetry)
        if client_id:
            await self.session_store.fail_idempotency(session_id, client_id)
        await _emit(sink, "run.error", result.to_dict())
        return result


def _chunks(text: str, size: int) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [" "]


__all__ = ["ChatResult", "ChatUseCase"]
