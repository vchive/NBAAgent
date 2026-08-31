"""Shared synchronous/SSE chat orchestration."""

from __future__ import annotations

import asyncio
import inspect
import re
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from apps.api.src.application.context_manager import ContextManager
from apps.api.src.application.parser import IntentParser, ParseResult, resolve_entities
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
    Category,
    ChatRequest,
    DraftAnswer,
    EntityKind,
    EntityRef,
    EvidenceState,
    FactAssertion,
    FactBundle,
    Game,
    GameBundle,
    GameFilters,
    HistoryRecord,
    IntelligenceMode,
    IntentName,
    MetricRef,
    NewsItem,
    Operation,
    QueryIntent,
    QueryMode,
    SafetyCategory,
    SafetyDecision,
    SafetyOutcome,
    Standing,
    StatScope,
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
from apps.api.src.infrastructure.agent_tools import resolve_date_expression
from apps.api.src.infrastructure.hermes_agent_runtime import (
    AgentTurnInput,
    AgentTurnResult,
    HermesAgentRuntime,
)
from apps.api.src.infrastructure.hermes_runtime import (
    HermesRuntimeAdapter,
    TemplateRuntime,
    is_unsafe_runtime_text,
)
from apps.api.src.infrastructure.session_store import InMemorySessionStore
from apps.api.src.infrastructure.telemetry import QueryTelemetry, TelemetrySink, hash_text

_MODEL_META_RE = re.compile(
    r"(?:你|您|当前|本次|系统|服务).{0,8}(?:用的|使用的|调用的|配置的)?\s*"
    r"(?:哪个|什么|何种)?\s*(?:模型|大模型|语言模型|LLM)"
    r"|(?:模型|大模型|语言模型|LLM).{0,8}(?:是什么|是哪一个|哪个|名称|配置)",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^(?:你?好|您好|嗨|哈喽|hello|hi|hey|ni\s*hao|nihao|在吗|早上好|下午好|晚上好)"
    r"(?:[!！,.，。?？\s]*)$",
    re.IGNORECASE,
)

# These are conversational capability questions, not NBA lookups.  They are
# deliberately kept as a small allow-list so that an ordinary question such as
# “你是谁说的球员” still goes through the normal intent/safety pipeline.  The
# pinyin aliases cover the common short forms seen in chat input (for example
# ``nishishei`` from the acceptance demo).
_CAPABILITY_RE = re.compile(
    r"^(?:"
    r"你是谁|你是誰|你叫什么|你是什么助手|你是什么ai|你是什么人工智能|"
    r"你能做什么|你会什么|你可以做什么|你能干什么|你可以干什么|"
    r"你能帮我做什么|你可以帮我做什么|你的功能是什么|有什么功能|"
    r"在吗|who\s+are\s+you|what\s+can\s+you\s+do|are\s+you\s+there|"
    r"ni\s*shi\s*(?:shei|shui)|nishishei|nishishui"
    r")(?:[!！,.，。?？\s]*)$",
    re.IGNORECASE,
)


def _is_model_meta_question(message: str) -> bool:
    return bool(_MODEL_META_RE.search(str(message or "").strip()))


def _is_capability_question(message: str) -> bool:
    """Return whether a turn can be answered without NBA observations.

    Hermes is allowed to answer these turns without a tool, but the same
    classification is also used by the output guard and the local safety
    response when Hermes is unavailable.  Keeping the predicate in one place
    prevents identity/capability questions from falling into the NBA parser.
    """

    return bool(_CAPABILITY_RE.fullmatch(str(message or "").strip()))


def _is_zero_tool_question(message: str) -> bool:
    return bool(_GREETING_RE.fullmatch(str(message or "").strip())) or _is_capability_question(
        message
    )


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
    # Minimal provider-neutral provenance.  This is deliberately separate
    # from internal telemetry: clients can tell whether the constrained model
    # pass was used or fell back without seeing model names, prompts, or keys.
    composition: dict[str, Any] = field(
        default_factory=lambda: {
            "mode": "deterministic",
            "status": "not_requested",
            "latency_ms": 0,
        }
    )

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
            "composition": dump(self.composition),
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
        agent_runtime: Any | None = None,
        siliconflow_client: Any | None = None,
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
                news_ttl_seconds=getattr(settings, "ddg_cache_ttl_seconds", 300),
            )
        self.gateway = gateway
        self.provider = provider
        self.parser = IntentParser(clock=self.clock)
        self.planner = QueryPlanner()
        self.template_composer = TemplateComposer()
        self.runtime = runtime or TemplateRuntime(composer=self.template_composer)
        configured_hermes_mode = str(getattr(settings, "hermes_lite_mode", "off")).lower()
        legacy_hermes_mode = (
            "off" if configured_hermes_mode == "embedded_agent" else configured_hermes_mode
        )
        self.hermes_runtime = hermes_runtime or HermesRuntimeAdapter(
            fallback=self.runtime,
            mode=legacy_hermes_mode,
            timeout_ms=getattr(settings, "hermes_lite_timeout_ms", 2500),
            endpoint=getattr(settings, "hermes_lite_endpoint", ""),
            llm_mode=getattr(settings, "llm_mode", "mock"),
            siliconflow_api_key=getattr(settings, "siliconflow_api_key", ""),
            siliconflow_api_key_file=getattr(settings, "siliconflow_api_key_file", ""),
            siliconflow_base_url=getattr(
                settings, "siliconflow_base_url", "https://api.siliconflow.cn/v1"
            ),
            siliconflow_model=getattr(
                settings, "siliconflow_model", "deepseek-ai/DeepSeek-V4-Flash"
            ),
            siliconflow_max_tokens=getattr(settings, "siliconflow_max_tokens", 800),
            siliconflow_timeout_seconds=getattr(settings, "llm_timeout_seconds", 8.0),
            siliconflow_max_response_bytes=getattr(
                settings, "siliconflow_max_response_bytes", 262_144
            ),
            siliconflow_max_request_bytes=getattr(settings, "max_request_bytes", 32_768),
            siliconflow_client=siliconflow_client,
        )
        self.agent_runtime = agent_runtime or HermesAgentRuntime(
            mode=configured_hermes_mode if configured_hermes_mode == "embedded_agent" else "off",
            llm_mode=getattr(settings, "llm_mode", "mock"),
            api_key=getattr(settings, "siliconflow_api_key", ""),
            api_key_file=getattr(settings, "siliconflow_api_key_file", ""),
            base_url=getattr(settings, "siliconflow_base_url", "https://api.siliconflow.cn/v1"),
            model=getattr(settings, "siliconflow_model", "deepseek-ai/DeepSeek-V4-Flash"),
            max_tokens=getattr(settings, "siliconflow_max_tokens", 640),
            timeout_ms=getattr(settings, "hermes_lite_timeout_ms", 40_000),
            max_iterations=getattr(settings, "agent_max_iterations", 4),
            max_tool_calls=getattr(settings, "agent_max_tool_calls", 4),
            tool_timeout_ms=getattr(settings, "agent_tool_timeout_ms", 8_000),
            max_tool_result_bytes=getattr(settings, "agent_max_tool_result_bytes", 16_384),
            max_output_bytes=getattr(settings, "agent_max_output_bytes", 20_000),
            package_version=getattr(settings, "agent_package_version", "0.19.0"),
        )
        self.runtime_selector = RuntimeSelector(
            template_runtime=self.runtime,
            hermes_runtime=self.hermes_runtime,
            profile=getattr(settings, "runtime_profile", "template"),
            full_intelligence_enabled=bool(getattr(settings, "full_intelligence_enabled", False)),
            default_intelligence_mode=getattr(settings, "default_intelligence_mode", "hybrid"),
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
        _internal_tool: bool = False,
        _parent_deadline: datetime | None = None,
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
            deadline_at_utc=_parent_deadline
            or self._now()
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
                        composition=replay.get(
                            "composition",
                            {
                                "mode": "deterministic",
                                "status": "not_requested",
                                "latency_ms": 0,
                            },
                        ),
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
            # Prompt-injection/control material is not a basketball fact.  Treat
            # it as an out-of-scope request before context, cache, admission or
            # provider access; the model boundary also performs an independent
            # check for callers that bypass this use case.
            if safety.outcome is SafetyOutcome.ALLOW and is_unsafe_runtime_text(req.message):
                telemetry.fallback_reason = "unsanitized_question"
                safety = SafetyDecision(
                    outcome=SafetyOutcome.OUT_OF_SCOPE,
                    category=SafetyCategory.OUT_OF_SCOPE,
                    confidence=0.99,
                    refusal_template_id="out_of_scope",
                )
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

            # Configuration questions are intentionally answered locally. They
            # should not be misclassified as an NBA lookup (which previously
            # produced a misleading "请补充查询对象" clarification), and a
            # model does not need to explain which model is configured.
            if _is_model_meta_question(req.message):
                runtime_model = getattr(self.agent_runtime, "model", "") or getattr(
                    getattr(self.hermes_runtime, "model_runtime", None), "model", ""
                )
                configured_model = runtime_model or getattr(
                    self.settings, "siliconflow_model", "deepseek-ai/DeepSeek-V4-Flash"
                )
                llm_mode = str(getattr(self.settings, "llm_mode", "mock")).lower()
                hermes_mode = str(getattr(self.settings, "hermes_lite_mode", "off")).lower()
                if llm_mode == "live" and hermes_mode in {
                    "embedded_spike",
                    "embedded_agent",
                    "sidecar",
                }:
                    availability = "当前已启用"
                else:
                    availability = "当前未启用（服务使用确定性模板）"
                answer = (
                    f"当前配置的模型是 **{configured_model}**，{availability}。"
                    "默认 hybrid 只在战术分析和赛后复盘中使用模型；开启全智能模式后，"
                    "智能分析会理解问题并选择受控 NBA 工具。比分、统计、赛程和"
                    "逐回合事实仍由工具后的核验与确定性逻辑生成。这个配置查询本身不会再次调用大模型。"
                )
                draft = DraftAnswer(
                    markdown=answer,
                    blocks=[AnswerBlock(type=AnswerBlockType.TEXT, content=answer)],
                    evidence_state=EvidenceState.NONE,
                    follow_up="您可以问我一场比赛的战术或赛后复盘。",
                )
                result = self._result_from_draft(
                    request_id, session_id, "completed", draft, started, as_of=None
                )
                telemetry.intent_name = "MODEL_META"
                telemetry.evidence_state = "none"
                telemetry.finish(outcome="completed", total_latency_ms=result.latency_ms)
                telemetry.provider_call_count = 0
                telemetry.cache_read_count = 0
                telemetry.cache_write_count = 0
                telemetry.cache_hit_count = 0
                self.telemetry.record(telemetry)
                if client_id:
                    await self.session_store.complete_idempotency(session_id, client_id, result)
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
            agent_attempted = False
            if not _internal_tool and self._full_agent_requested(req):
                agent_attempted = True
                await _emit(
                    sink,
                    "run.status",
                    {"stage": "agent_planning", "text": "正在理解问题"},
                )
                before_agent = self._gateway_counters()
                try:
                    agent_turn = await self._await_with_cancel(
                        self.agent_runtime.run(
                            AgentTurnInput(
                                request_id=str(request_id),
                                opaque_session_id=InMemorySessionStore.hash_session(session_id),
                                sanitized_question=self._agent_question(req.message),
                                timezone=context.timezone,
                                now_beijing=format_beijing(self._now()),
                                context_hint=self._agent_context_hint(context),
                                deadline_at_utc=telemetry.deadline_at_utc,
                                max_iterations=getattr(self.settings, "agent_max_iterations", 4),
                                max_tool_calls=getattr(self.settings, "agent_max_tool_calls", 4),
                            ),
                            tool_runner=self._agent_tool_runner(
                                session_id=session_id,
                                context=context,
                                deadline_at_utc=telemetry.deadline_at_utc,
                                token=token,
                                sink=sink,
                            ),
                            cancel=token,
                        ),
                        token,
                    )
                except Exception:
                    agent_turn = AgentTurnResult(
                        status=RuntimeStatus.UNAVAILABLE,
                        finish_reason="runtime_exception",
                    )
                after_agent = self._gateway_counters()
                telemetry.provider_call_count = max(
                    0,
                    after_agent.get("provider_call_count", 0)
                    - before_agent.get("provider_call_count", 0),
                )
                telemetry.cache_read_count = max(
                    0,
                    after_agent.get("cache_read_count", 0)
                    - before_agent.get("cache_read_count", 0),
                )
                telemetry.cache_write_count = max(
                    0,
                    after_agent.get("cache_write_count", 0)
                    - before_agent.get("cache_write_count", 0),
                )
                telemetry.cache_hit_count = max(
                    0,
                    after_agent.get("cache_hit_count", 0)
                    - before_agent.get("cache_hit_count", 0),
                )
                telemetry.hermes_mode = "embedded_agent"
                telemetry.hermes_status = str(
                    getattr(agent_turn.status, "value", agent_turn.status)
                ).lower()
                telemetry.agent_iteration_count = agent_turn.iteration_count
                telemetry.agent_tool_call_count = len(
                    [call for call in agent_turn.tool_calls if call.status != "duplicate"]
                )
                telemetry.agent_tool_names = list(
                    dict.fromkeys(call.tool_name for call in agent_turn.tool_calls)
                )
                telemetry.composition_latency_ms = agent_turn.latency_ms
                if (
                    agent_turn.status is RuntimeStatus.OK
                    and agent_turn.answer_markdown
                ):
                    agent_answer = self._ground_agent_answer(
                        agent_turn.answer_markdown,
                        agent_turn.observations,
                    )
                    evidence_value = str(agent_turn.evidence_state).upper()
                    evidence = (
                        EvidenceState(evidence_value)
                        if evidence_value in {"VERIFIED", "PARTIAL", "NONE"}
                        else EvidenceState.NONE
                    )
                    draft = DraftAnswer(
                        markdown=agent_answer,
                        blocks=[
                            AnswerBlock(
                                type=AnswerBlockType.TEXT,
                                content=agent_answer,
                            )
                        ],
                        evidence_state=evidence,
                    )
                    guarded: DraftAnswer | None = None
                    try:
                        guarded = self.output_guard.validate_agent(
                            draft,
                            agent_turn.observations,
                            require_observation=not _is_zero_tool_question(req.message),
                        )
                    except (OutputGuardError, ValueError, TypeError):
                        if _is_zero_tool_question(req.message) and not agent_turn.observations:
                            safe_greeting = self._capability_answer()
                            guarded = self.output_guard.validate_agent(
                                DraftAnswer(
                                    markdown=safe_greeting,
                                    blocks=[
                                        AnswerBlock(
                                            type=AnswerBlockType.TEXT,
                                            content=safe_greeting,
                                        )
                                    ],
                                    evidence_state=EvidenceState.NONE,
                                ),
                                [],
                                require_observation=False,
                            )
                        else:
                            telemetry.fallback_reason = "agent_output_guard"
                    if guarded is not None:
                        await _emit(
                            sink,
                            "run.status",
                            {"stage": "agent_completing", "text": "已完成回答"},
                        )
                        telemetry.composition_mode = "agent"
                        telemetry.composition_status = "used"
                        telemetry.evidence_state = evidence.value.lower()
                        telemetry.transition("COMPOSED")
                        telemetry.transition("OUTPUT_GUARDED")
                        try:
                            await self.context_manager.commit(
                                context,
                                intent=self._agent_summary_intent(),
                                facts=FactBundle(facts=[], evidence_state=EvidenceState.NONE),
                                answer=guarded.markdown,
                            )
                        except Exception:
                            pass
                        as_of = self._agent_as_of(agent_turn.observations)
                        result = self._result_from_draft(
                            request_id,
                            session_id,
                            "completed",
                            guarded,
                            started,
                            as_of=as_of,
                            composition=self._composition_from_telemetry(telemetry),
                        )
                        telemetry.finish(outcome="completed", total_latency_ms=result.latency_ms)
                        self.telemetry.record(telemetry)
                        if client_id:
                            await self.session_store.complete_idempotency(
                                session_id, client_id, result
                            )
                        for chunk in _chunks(guarded.markdown, 120):
                            await _emit(sink, "message.delta", {"text": chunk})
                        await _emit(sink, "message.completed", result.to_dict())
                        return result
                # Capability/identity turns are intentionally useful even when
                # the model is temporarily unavailable. They contain no NBA
                # facts, so answer locally instead of sending the user into
                # the NBA intent parser and a misleading clarification.
                if _is_zero_tool_question(req.message):
                    local_answer = self._capability_answer()
                    local_draft = DraftAnswer(
                        markdown=local_answer,
                        blocks=[
                            AnswerBlock(
                                type=AnswerBlockType.TEXT,
                                content=local_answer,
                            )
                        ],
                        evidence_state=EvidenceState.NONE,
                    )
                    telemetry.composition_mode = "deterministic"
                    telemetry.composition_status = "not_requested"
                    telemetry.fallback_reason = None
                    telemetry.evidence_state = "none"
                    telemetry.transition("COMPOSED")
                    telemetry.transition("OUTPUT_GUARDED")
                    result = self._result_from_draft(
                        request_id,
                        session_id,
                        "completed",
                        local_draft,
                        started,
                        as_of=None,
                        composition=self._composition_from_telemetry(telemetry),
                    )
                    telemetry.finish(outcome="completed", total_latency_ms=result.latency_ms)
                    self.telemetry.record(telemetry)
                    if client_id:
                        await self.session_store.complete_idempotency(
                            session_id, client_id, result
                        )
                    for chunk in _chunks(local_draft.markdown, 120):
                        await _emit(sink, "message.delta", {"text": chunk})
                    await _emit(sink, "message.completed", result.to_dict())
                    return result
                if telemetry.fallback_reason is None:
                    telemetry.fallback_reason = agent_turn.finish_reason or "agent_unavailable"
                telemetry.composition_mode = "fallback"
                telemetry.composition_status = "fallback"
                await _emit(
                    sink,
                    "run.status",
                    {"stage": "agent_fallback", "text": "正在回退到已核验事实链路"},
                )
            # Non-full requests do not enter the model loop, but greetings and
            # capability questions are still complete conversational turns.
            # Handle them before the NBA parser so they never become a
            # misleading “请补充查询对象” clarification.
            if _is_zero_tool_question(req.message):
                local_answer = self._capability_answer()
                local_draft = DraftAnswer(
                    markdown=local_answer,
                    blocks=[
                        AnswerBlock(
                            type=AnswerBlockType.TEXT,
                            content=local_answer,
                        )
                    ],
                    evidence_state=EvidenceState.NONE,
                )
                telemetry.composition_mode = "deterministic"
                telemetry.composition_status = "not_requested"
                telemetry.fallback_reason = None
                telemetry.evidence_state = "none"
                telemetry.transition("COMPOSED")
                telemetry.transition("OUTPUT_GUARDED")
                result = self._result_from_draft(
                    request_id,
                    session_id,
                    "completed",
                    local_draft,
                    started,
                    as_of=None,
                    composition=self._composition_from_telemetry(telemetry),
                )
                telemetry.finish(outcome="completed", total_latency_ms=result.latency_ms)
                self.telemetry.record(telemetry)
                if client_id:
                    await self.session_store.complete_idempotency(session_id, client_id, result)
                for chunk in _chunks(local_draft.markdown, 120):
                    await _emit(sink, "message.delta", {"text": chunk})
                await _emit(sink, "message.completed", result.to_dict())
                return result
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
                    composition=(
                        self._composition_from_telemetry(telemetry)
                        if agent_attempted
                        else None
                    ),
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
                draft = DraftAnswer(
                    markdown=question,
                    blocks=[AnswerBlock(type=AnswerBlockType.TEXT, content=question)],
                    evidence_state=EvidenceState.NONE,
                    # A clarification is already an actionable prompt.  Do
                    # not render the same sentence as a “继续追问” button:
                    # clicking that button used to submit the clarification
                    # back to the parser and create an endless loop.
                    follow_up=None,
                )
                result = self._result_from_draft(
                    request_id,
                    session_id,
                    "needs_clarification",
                    draft,
                    started,
                    as_of=None,
                    composition=(
                        self._composition_from_telemetry(telemetry)
                        if agent_attempted
                        else None
                    ),
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
                draft = DraftAnswer(
                    markdown=question,
                    blocks=[AnswerBlock(type=AnswerBlockType.TEXT, content=question)],
                    evidence_state=EvidenceState.NONE,
                    follow_up=None,
                )
                result = self._result_from_draft(
                    request_id,
                    session_id,
                    "needs_clarification",
                    draft,
                    started,
                    as_of=None,
                    composition=(
                        self._composition_from_telemetry(telemetry)
                        if agent_attempted
                        else None
                    ),
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
            deadline = telemetry.deadline_at_utc or self._now() + timedelta(
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
            telemetry.provider_call_count += max(
                0, after.get("provider_call_count", 0) - before.get("provider_call_count", 0)
            )
            telemetry.cache_read_count += max(
                0, after.get("cache_read_count", 0) - before.get("cache_read_count", 0)
            )
            telemetry.cache_write_count += max(
                0, after.get("cache_write_count", 0) - before.get("cache_write_count", 0)
            )
            telemetry.cache_hit_count += max(
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
                draft = self._no_data_draft(parsed)
                result = self._result_from_draft(
                    request_id,
                    session_id,
                    "no_data",
                    draft,
                    started,
                    as_of=None,
                    composition=(
                        self._composition_from_telemetry(telemetry)
                        if agent_attempted
                        else None
                    ),
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
            # Make the expensive/observable model phase explicit in SSE.  This
            # is a fixed progress label (no provider name or error detail),
            # and it is emitted only when the selector actually chose the
            # constrained analysis runtime.
            if (
                self.runtime_selector.for_intent(parsed.intent.intent_name, req.intelligence_mode)
                is not self.runtime
            ):
                await _emit(sink, "run.status", {"stage": "model", "text": "正在生成智能分析"})
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
                user_message=req.message,
                intelligence_mode=req.intelligence_mode,
                force_template=_internal_tool or agent_attempted,
                preserve_fallback=agent_attempted,
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
            if not _internal_tool:
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
                composition=self._composition_from_telemetry(telemetry),
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

    def _full_agent_requested(self, request: ChatRequest) -> bool:
        requested = getattr(request.intelligence_mode, "value", request.intelligence_mode)
        requested = str(
            requested or getattr(self.settings, "default_intelligence_mode", "hybrid")
        ).lower()
        return bool(
            requested == "full"
            and getattr(self.settings, "full_intelligence_enabled", False)
            and getattr(self.agent_runtime, "mode", "off") == "embedded_agent"
        )

    @staticmethod
    def _is_greeting(message: str) -> bool:
        return bool(_GREETING_RE.fullmatch(str(message or "").strip()))

    def _no_data_draft(self, parsed: ParseResult) -> DraftAnswer:
        """Turn an empty, valid lookup into a useful scoped answer.

        The old generic copy asked users to add a subject even when the query
        already had a complete date (for example, an off-season “today”
        schedule). Keep the deterministic mode honest while explaining what
        was actually checked and offering the next useful action.
        """

        intent = parsed.intent
        if intent.intent_name is IntentName.SCHEDULE_RESULT:
            date_range = intent.date_range
            if date_range is not None:
                start = format_beijing(date_range.start_inclusive).split(" ", 1)[0]
                end = format_beijing(date_range.end_exclusive - timedelta(microseconds=1)).split(
                    " ", 1
                )[0]
                scope = start if start == end else f"{start} 至 {end}"
                message = f"北京时间 **{scope}** 暂无可核验的 NBA 比赛。"
                return self.template_composer.no_data(
                    message=message,
                    follow_up="可以换一个日期，或切换左侧“精彩回顾”查看最近 5 场比赛。",
                )
            return self.template_composer.no_data(
                message="暂时没有返回可核验的 NBA 赛程。",
                follow_up="请指定日期（例如今天、明天或下周），我再帮您查询。",
            )

        subject = next(
            (item for item in intent.entities if item.kind in {EntityKind.PLAYER, EntityKind.TEAM}),
            None,
        )
        if intent.intent_name is IntentName.DATA and subject is not None:
            return self.template_composer.no_data(
                message=f"暂未找到 **{subject.display_name}** 的公开统计记录。",
                follow_up="可以补充赛季、比赛或统计范围，我再继续核对。",
            )
        if intent.intent_name is IntentName.PLAY_BY_PLAY:
            return self.template_composer.no_data(
                message="当前没有找到可用的逐回合记录。",
                follow_up="请指定具体比赛，或先从左侧“精彩回顾”选择一场比赛。",
            )
        return self.template_composer.no_data()

    @staticmethod
    def _capability_answer() -> str:
        return (
            "您好！我是 COURTSIDE，专注于 NBA 篮球问答。"
            "我可以帮您了解比赛、球队、球员、新闻和战术等内容。"
            "请直接告诉我想了解的对象或问题。"
        )

    @staticmethod
    def _agent_question(message: str) -> str:
        """Apply only an unambiguous chat typo correction before Hermes.

        Chinese users occasionally type ``下周有比赛买`` when they mean
        ``下周有比赛吗``.  Correct the terminal character only when the
        message is clearly a schedule question and does not contain purchase
        or betting vocabulary; all other wording is passed through unchanged.
        """

        question = str(message or "").strip()
        if not question.endswith("买"):
            return question
        if not any(token in question for token in ("比赛", "赛程", "赛果")):
            return question
        if any(token in question for token in ("买球", "买票", "下注", "投注", "赔率", "盘口")):
            return question
        return f"{question[:-1]}吗"

    @staticmethod
    def _agent_context_hint(context: Any) -> str | None:
        parts: list[str] = []
        active_names = [
            item.display_name
            for item in (
                getattr(context, "active_game", None),
                getattr(context, "active_team", None),
                getattr(context, "active_player", None),
            )
            if item is not None
        ]
        if active_names:
            parts.append("当前对象：" + "、".join(active_names[:3]))
        for summary in list(getattr(context, "recent_turn_summaries", []) or [])[-3:]:
            text = " ".join(str(getattr(summary, "text_summary", "")).split())[:600]
            if text and not is_unsafe_runtime_text(text):
                parts.append("上轮摘要：" + text)
        value = "\n".join(parts)
        return value[:3000] or None

    @staticmethod
    def _agent_summary_intent() -> QueryIntent:
        return QueryIntent(
            category=Category.A,
            intent_name=IntentName.DATA,
            mode=QueryMode.OBJECTIVE,
            confidence=1,
            operation=Operation.EXPLAIN,
        )

    @staticmethod
    def _agent_as_of(observations: list[dict[str, Any]]) -> str | None:
        values = [
            str(item.get("as_of_beijing"))
            for item in observations
            if item.get("as_of_beijing")
        ]
        return max(values) if values else None

    @staticmethod
    def _ground_agent_answer(
        answer: str,
        observations: list[dict[str, Any]],
    ) -> str:
        """Make bounded empty-schedule answers deterministic.

        A model may abbreviate a tool's ISO dates or speculate that an empty
        schedule means the offseason.  The server-owned schedule observation
        already contains the complete Beijing range and is the only truthful
        statement available, so project that canonical result instead.  Hermes
        still owns question understanding and tool selection; it does not own
        the factual wording of an empty result.
        """

        for item in reversed(observations):
            if not isinstance(item, Mapping):
                continue
            if str(item.get("intent", "")).lower() != "schedule_result":
                continue
            if str(item.get("status", "")).lower() != "no_data":
                continue
            scope = item.get("query_scope")
            if not isinstance(scope, Mapping):
                continue
            start = str(scope.get("start_date") or "").strip()
            end = str(scope.get("end_date") or "").strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", end
            ):
                return (
                    f"北京时间 **{start} 至 {end}** 的公开赛程查询"
                    "没有返回 NBA 比赛。"
                )
        return answer

    def _agent_tool_runner(
        self,
        *,
        session_id: UUID,
        context: Any,
        deadline_at_utc: datetime,
        token: CancelToken,
        sink: Any,
    ):
        async def run(tool_name: str, arguments: dict[str, str]) -> Mapping[str, Any]:
            token.raise_if_cancelled()
            await _emit(
                sink,
                "run.status",
                {"stage": "agent_tool", "text": "正在调用受控 NBA 数据工具"},
            )
            if tool_name == "nba_schedule":
                return await self._agent_schedule_observation(
                    arguments,
                    timezone_name=context.timezone,
                    deadline_at_utc=deadline_at_utc,
                    token=token,
                )
            if tool_name == "nba_query":
                message = arguments.get("question", "")
                intent = "nba_query"
            elif tool_name == "nba_news":
                message = arguments.get("subject", "")
                date_expression = arguments.get("date_expression")
                if date_expression:
                    message = f"{message}，时间范围：{date_expression}"
                message = f"{message} NBA 新闻"
                intent = "nba_news"
            else:
                return {
                    "status": "failed",
                    "intent": "unknown",
                    "query_scope": None,
                    "answer_markdown": "该工具不可用。",
                    "blocks": [],
                    "evidence_state": "none",
                    "as_of_beijing": None,
                }
            nested = await self.handle(
                ChatRequest(
                    session_id=session_id,
                    message=message,
                    client_timezone=context.timezone,
                    intelligence_mode=IntelligenceMode.HYBRID,
                ),
                cancel=token,
                _internal_tool=True,
                _parent_deadline=deadline_at_utc,
            )
            status = nested.status
            if status not in {"completed", "no_data", "needs_clarification"}:
                status = "failed"
            return {
                "status": status,
                "intent": intent,
                "query_scope": None,
                "answer_markdown": nested.answer_markdown,
                "blocks": [
                    block.model_dump(mode="json")
                    if hasattr(block, "model_dump")
                    else block
                    for block in nested.blocks
                ],
                "evidence_state": nested.evidence_state,
                "as_of_beijing": nested.as_of_beijing,
            }

        return run

    async def _agent_schedule_observation(
        self,
        arguments: Mapping[str, str],
        *,
        timezone_name: str,
        deadline_at_utc: datetime,
        token: CancelToken,
    ) -> Mapping[str, Any]:
        try:
            date_range, scope = resolve_date_expression(
                arguments.get("date_expression", ""),
                now_utc=self._now(),
                timezone_name=timezone_name,
            )
        except (TypeError, ValueError):
            return {
                "status": "needs_clarification",
                "intent": "schedule_result",
                "query_scope": None,
                "answer_markdown": "请给出明确日期，例如今天、明天、下周或具体日期。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
            }
        team_ids = [
            item.canonical_id
            for item in resolve_entities(arguments.get("team", ""))
            if item.kind is EntityKind.TEAM
        ][:1]
        budget = RequestBudget(
            deadline_at_utc,
            max_provider_operations=getattr(self.settings, "max_provider_operations", 4),
            max_retries_per_operation=getattr(self.settings, "provider_max_retries", 2),
            clock=self.clock,
        )
        admission_result, lease, _ = await self._await_with_cancel(
            self.admission.acquire(
                timeout_ms=getattr(self.settings, "queue_wait_deadline_ms", 1000)
            ),
            token,
        )
        if lease is None:
            return {
                "status": "failed",
                "intent": "schedule_result",
                "query_scope": scope,
                "answer_markdown": "当前查询较多，请稍后重试。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
            }
        try:
            provider_result = await self._await_with_cancel(
                self.gateway.search_games(
                    GameFilters(date_range=date_range, team_ids=team_ids), budget=budget
                ),
                token,
            )
        finally:
            await lease.release()
        if provider_result.error is not None:
            return {
                "status": "failed",
                "intent": "schedule_result",
                "query_scope": scope,
                "answer_markdown": "赛程数据暂时不可用，请稍后重试。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
            }
        games = [item for item in (provider_result.data or []) if isinstance(item, Game)]
        as_of = format_beijing(provider_result.retrieved_at_utc)
        if not games:
            message = (
                f"北京时间 **{scope['start_date']} 至 {scope['end_date']}** 的公开赛程查询"
                "没有返回 NBA 比赛。"
            )
            return {
                "status": "no_data",
                "intent": "schedule_result",
                "query_scope": scope,
                "answer_markdown": message,
                "blocks": [
                    AnswerBlock(type=AnswerBlockType.TEXT, content=message).model_dump(mode="json")
                ],
                "evidence_state": "none",
                "as_of_beijing": as_of,
            }
        intent = QueryIntent(
            category=Category.B,
            intent_name=IntentName.SCHEDULE_RESULT,
            mode=QueryMode.OBJECTIVE,
            confidence=1,
            metrics=[MetricRef(name="points", unit="分", scope=StatScope.GAME)],
            date_range=date_range,
            operation=Operation.LOOKUP,
        )
        parsed = ParseResult(intent=intent)
        facts, _, _, _ = self._facts_for(
            parsed,
            games,
            provider_result.evidence,
            provider_partial=bool(provider_result.partial),
        )
        status_labels = {
            "FINAL": "已结束",
            "LIVE": "进行中",
            "SCHEDULED": "未开赛",
            "POSTPONED": "延期",
            "UNKNOWN": "状态待确认",
        }
        rows: list[list[str]] = []
        lines = [f"北京时间 **{scope['start_date']} 至 {scope['end_date']}** 的 NBA 赛程："]
        for game in games[:30]:
            score = (
                f"{game.away_score}–{game.home_score}"
                if game.away_score is not None and game.home_score is not None
                else "—"
            )
            game_status = status_labels.get(
                str(getattr(game.status, "value", game.status)), "状态待确认"
            )
            start = format_beijing(game.start_utc)
            rows.append(
                [start, game.away.display_name, score, game.home.display_name, game_status]
            )
            lines.append(
                f"- {start}：{game.away.display_name} vs {game.home.display_name}"
                f"（{game_status}，{score}）"
            )
        draft = DraftAnswer(
            markdown="\n".join(lines),
            blocks=[
                AnswerBlock(
                    type=AnswerBlockType.TABLE,
                    columns=["北京时间", "客队", "比分", "主队", "状态"],
                    rows=rows,
                )
            ],
            evidence_state=facts.evidence_state,
        )
        try:
            guarded = self.output_guard.validate(draft, facts)
        except (OutputGuardError, ValueError, TypeError):
            return {
                "status": "failed",
                "intent": "schedule_result",
                "query_scope": scope,
                "answer_markdown": "赛程结果未通过事实校验。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": as_of,
            }
        return {
            "status": "completed",
            "intent": "schedule_result",
            "query_scope": scope,
            "answer_markdown": guarded.markdown,
            "blocks": [block.model_dump(mode="json") for block in guarded.blocks],
            "evidence_state": guarded.evidence_state.value.lower(),
            "as_of_beijing": as_of,
        }

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
        user_message: str | None = None,
        intelligence_mode: IntelligenceMode | str | None = None,
        force_template: bool = False,
        preserve_fallback: bool = False,
    ) -> DraftAnswer:
        """Select the constrained runtime according to the request mode.

        The runtime receives no raw provider data, URL, session identifier, cache,
        or tool handle. In hybrid mode objective answers keep using the richer
        deterministic renderer; full mode may append model wording while the
        deterministic section remains authoritative.
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
        if force_template:
            if not preserve_fallback:
                telemetry.composition_mode = "deterministic"
                telemetry.composition_status = "not_requested"
                telemetry.composition_latency_ms = 0
            return base
        # Every request starts with the deterministic answer.  Only an
        # explicitly selected analysis runtime can change this provenance.
        telemetry.composition_mode = "deterministic"
        telemetry.composition_status = "not_requested"
        telemetry.composition_latency_ms = 0
        selected = self.runtime_selector.for_intent(parsed.intent.intent_name, intelligence_mode)
        if selected is self.runtime:
            return base

        # Keep prompt-injection and transport/provenance material entirely on
        # the deterministic path.  Sanitising a few words is not enough: the
        # safest response to a control attempt is to skip model egress and use
        # the already-verified local answer.
        if user_message is not None and is_unsafe_runtime_text(user_message):
            telemetry.hermes_status = "unavailable"
            telemetry.fallback_reason = "unsanitized_question"
            return base

        telemetry.hermes_mode = getattr(
            self.hermes_runtime,
            "mode",
            getattr(self.settings, "hermes_lite_mode", "off"),
        )
        telemetry.composition_mode = "fallback"
        telemetry.composition_status = "fallback"
        composer_input = ComposerInput(
            request_id=request_id,
            opaque_session_id=InMemorySessionStore.hash_session(session_id),
            deadline_at_utc=budget.deadline_at_utc,
            remaining_ms=budget.remaining_ms(),
            sanitized_question=self._runtime_question(parsed, user_message),
            intent=parsed.intent,
            fact_bundle=facts,
            style_policy=StylePolicy(),
            tool_policy=getattr(self.hermes_runtime, "tool_policy", ToolPolicy()),
        )
        try:
            runtime_started = time.monotonic()
            runtime_value = await self._await_with_cancel(
                selected.compose(composer_input, token), token
            )
            runtime_result = (
                runtime_value
                if isinstance(runtime_value, RuntimeResult)
                else RuntimeResult.model_validate(runtime_value)
            )
            telemetry.composition_latency_ms = max(
                0,
                int(
                    getattr(runtime_result, "latency_ms", 0)
                    or (time.monotonic() - runtime_started) * 1000
                ),
            )
        except AgentError:
            raise
        except Exception:
            telemetry.hermes_status = "unavailable"
            telemetry.fallback_reason = "runtime_exception"
            return base

        status = getattr(runtime_result.status, "value", runtime_result.status)
        telemetry.hermes_status = str(status).lower()
        if status == RuntimeStatus.UNSAFE.value:
            raise OutputBlockedError()
        if status != RuntimeStatus.OK.value or not runtime_result.draft_markdown:
            # Prefer the immutable per-call result over the adapter's shared
            # diagnostic field; concurrent analysis requests must not inherit
            # one another's fallback reason.
            telemetry.fallback_reason = str(
                runtime_result.finish_reason
                or getattr(self.hermes_runtime, "fallback_reason", None)
                or "runtime_unavailable"
            )
            return base

        # The local mock delegates to the deterministic renderer. Keep the
        # richer base draft in that case while still exercising and observing
        # the capability boundary.  Mark it as disabled rather than claiming
        # that a model was used: ``LLM_MODE=mock`` never makes an external call.
        if runtime_result.finish_reason == "template":
            telemetry.composition_mode = "fallback"
            telemetry.composition_status = "disabled"
            telemetry.fallback_reason = telemetry.fallback_reason or "llm_mock"
            return base

        # Treat model text as an untrusted analysis supplement.  The
        # deterministic base remains responsible for every factual block and
        # freshness marker, so a terse/partial model answer cannot erase a
        # verified score or correction.  Validate the candidate here as well
        # as at the outer boundary; invalid model text then degrades cleanly to
        # the local answer instead of turning a transient model issue into a
        # user-visible 500.
        # Leave room for the deterministic answer and avoid a validation
        # exception if a provider returns its full output budget.
        model_text = runtime_result.draft_markdown.strip()[:6000]
        if not model_text:
            telemetry.fallback_reason = telemetry.fallback_reason or "empty_model_output"
            return base
        # ``DraftAnswer.markdown`` has a 20k bound.  Trim the model text
        # further when the deterministic section is already large.
        room = max(1, 19_500 - len(base.markdown))
        model_text = model_text[:room]

        def make_candidate(text: str) -> DraftAnswer:
            return DraftAnswer(
                markdown=f"{base.markdown}\n\n{text}",
                blocks=[
                    *base.blocks,
                    AnswerBlock(type=AnswerBlockType.ANALYSIS, content=text),
                ],
                evidence_state=base.evidence_state,
                corrections=base.corrections,
                follow_up=base.follow_up,
            )

        try:
            guarded_candidate = self.output_guard.validate(make_candidate(model_text), facts)
        except (OutputGuardError, ValueError, TypeError):
            # Never replace an untraceable value with a vague placeholder such
            # as “若干”.  That still looks like a factual claim to a viewer and
            # can hide a model hallucination.  The deterministic base already
            # contains every verified fact, so any output-guard violation gets
            # a clean, truthful fallback.
            telemetry.fallback_reason = "model_output_guard"
            telemetry.composition_mode = "fallback"
            telemetry.composition_status = "fallback"
            return base
        telemetry.composition_mode = "model"
        telemetry.composition_status = "used"
        return guarded_candidate

    @staticmethod
    def _runtime_question(parsed: ParseResult, raw_text: str | None = None) -> str:
        """Build a bounded semantic question without forwarding raw instructions/URLs."""

        # Preserve the user's actual analytical goal (for example, “如何限制挡拆？”)
        # while stripping transport/control and prompt-injection material.  The
        # structured intent/entities below remain the authoritative task context.
        question = ""
        if raw_text:
            question = unicodedata.normalize("NFKC", str(raw_text))
            question = "".join(
                " " if unicodedata.category(char) == "Cf" else char for char in question
            )
            question = re.sub(r"[\x00-\x1f\x7f]", " ", question)
            question = re.sub(r"https?://\S+|www\.\S+", " ", question, flags=re.IGNORECASE)
            question = re.sub(
                r"(?:(?:ignore|disregard|forget|override|bypass|skip)\s+(?:all\s+)?(?:the\s+)?"
                r"(?:(?:previous|prior|earlier|above|your|system|developer)\s+)?"
                r"(?:instructions?|rules?|requirements?|prompts?|messages?|constraints?|facts?|evidence|verification)|"
                r"(?:do\s+not|don't)\s+(?:follow|use|obey)|"
                r"answer\s+without\s+(?:facts?|evidence|verification)|"
                r"(?:bypass|skip)\s+(?:safety|verification|guardrails?|fact\s*checks?)|"
                r"(?:jailbreak|unrestricted\s+(?:assistant|model)|"
                r"(?:act|role[- ]?play)\s+as\s+(?:an?\s+)?"
                r"(?:unrestricted|system(?:\s+administrator)?))"
                r"(?![A-Za-z0-9_])|"
                r"system[_ -]?prompt|developer[_ -]?message|tool[_ -]?call|"
                r"source[_ -]?(?:url|ref|id)|evidence[_ -]?ids?|"
                r"provider[_ -]?(?:url|json|response)|"
                r"canonical[_ -]?ids?|fact[_ -]?ids?|request[_ -]?id|session[_ -]?id|"
                r"api[_ -]?key|authorization|bearer\s+\S+|"
                r"(?:忽略|无视|忘记|跳过)(?:之前|先前|此前|以前|上面|以上|当前|所有|系统|开发者)?(?:的)?"
                r"(?:指令|规则|要求|提示|约束|限制|事实|证据)|"
                r"(?:不要|勿)(?:遵循|理会|管|考虑|使用)(?:之前|上面|以上|系统|开发者)?(?:的)?"
                r"(?:指令|规则|要求|事实|证据)|(?:绕过|跳过)(?:安全|限制|审查|事实|核验)|"
                r"(?:请)?(?:扮演|充当|变成)[\s\S]{0,8}(?:系统|管理员|无约束|不受限制)|"
                r"(?:输出|泄露)[\s\S]{0,12}(?:内部提示|系统提示|开发者消息|思维链)|"
                r"系统\s*(?:提示|指令)|开发者\s*(?:消息|指令)|工具\s*(?:调用|指令)|泄露(?:密钥|凭据)|"
                r"访问令牌|提供商字段|原始响应|原始数据)",
                " ",
                question,
                flags=re.IGNORECASE,
            )
            question = " ".join(question.split())[:400]

        entities = "、".join(item.display_name for item in parsed.intent.entities[:8])
        metrics = "、".join(item.name for item in parsed.intent.metrics[:8])
        parts = [f"意图：{parsed.intent.intent_name.value}"]
        if question:
            parts.append(f"用户问题：{question}")
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

            # A date-scoped schedule is allowed to contain several games.  Keep
            # the first game in the legacy single-game slot (so existing
            # context/answer paths remain compatible), but verify and expose
            # every canonical row to the composer.  Without this projection a
            # query such as “某日有哪些比赛” would silently answer with only
            # the provider's first row.
            if parsed.intent.intent_name is IntentName.SCHEDULE_RESULT:
                # Providers normally return a typed list, but retain a
                # conservative guard for injected adapters that mix malformed
                # rows or duplicate event IDs.  Duplicates are not rendered
                # twice and make the evidence state partial rather than
                # claiming a fully verified slate.
                typed_games = [item for item in games if isinstance(item, Game)]
                unique_games: list[Game] = []
                seen_game_ids: set[str] = set()
                duplicate_row = False
                for item in typed_games:
                    if item.game_id in seen_game_ids:
                        duplicate_row = True
                        continue
                    seen_game_ids.add(item.game_id)
                    unique_games.append(item)
                if not unique_games:
                    return (
                        FactBundle(
                            facts=[],
                            missing=["比赛记录"],
                            evidence_state=EvidenceState.NONE,
                        ),
                        None,
                        None,
                        None,
                    )
                # Preserve the provider evidence fingerprint on every row. A
                # scoreboard response commonly has one evidence record for a
                # whole date range, so sharing that record across the rows is
                # more truthful than manufacturing a per-game source ID. The
                # synthetic fallback is used only by injected providers that
                # forgot to return evidence, keeping the verifier's invariant
                # (verified facts always carry a non-empty evidence list).
                provider_evidence_ids = [
                    str(item.evidence_id).strip()
                    for item in evidence
                    if getattr(item, "evidence_id", None) and not str(item.evidence_id).isspace()
                ]
                verified_rows = [
                    verify_game(item, provider_evidence_ids or [f"game:{item.game_id}"])
                    for item in unique_games
                ]
                merged_facts = [fact for row in verified_rows for fact in row.facts]
                merged_missing = [missing for row in verified_rows for missing in row.missing]
                malformed_rows = len(typed_games) != len(games)
                partial = bool(
                    provider_partial
                    or malformed_rows
                    or duplicate_row
                    or any(row.evidence_state is EvidenceState.PARTIAL for row in verified_rows)
                )
                facts = FactBundle(
                    facts=merged_facts,
                    missing=merged_missing,
                    evidence_state=EvidenceState.PARTIAL if partial else EvidenceState.VERIFIED,
                )
                derived = DerivedResult(games=unique_games, partial=partial)
                return facts, unique_games[0], None, derived
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
        if any(
            slot.name == "game" and "精彩回顾" in slot.reason
            for slot in parsed.missing_slots
        ):
            return "请先从左侧“精彩回顾”选择最近一场比赛，或补充对阵双方，我再帮您核对关键回合。"
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
        composition: Mapping[str, Any] | None = None,
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
            composition=dict(
                composition
                or {
                    "mode": "deterministic",
                    "status": "not_requested",
                    "latency_ms": 0,
                }
            ),
        )

    @staticmethod
    def _composition_from_telemetry(telemetry: QueryTelemetry) -> dict[str, Any]:
        """Project internal runtime telemetry into a tiny safe public object."""

        return {
            "mode": telemetry.composition_mode,
            "status": telemetry.composition_status,
            "latency_ms": max(0, int(telemetry.composition_latency_ms or 0)),
        }

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
