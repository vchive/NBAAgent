"""Shared synchronous/SSE chat orchestration."""

from __future__ import annotations

import asyncio
import inspect
import re
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from apps.api.src.application.context_manager import ContextManager
from apps.api.src.application.parser import (
    GAMES,
    IntentParser,
    ParseResult,
    is_contextual_series_selection_question,
    is_game_recap_question,
    is_inverse_series_selection_question,
    is_positive_series_selection_question,
    is_subjective_comparison_question,
    resolve_entities,
)
from apps.api.src.application.ports import (
    CancelToken,
    ComposerInput,
    ProviderResult,
    RequestBudget,
    RuntimeResult,
    RuntimeStatus,
    StylePolicy,
    ToolPolicy,
)
from apps.api.src.application.query_planner import QueryPlan, QueryPlanner
from apps.api.src.application.runtime_selector import RuntimeSelector
from apps.api.src.application.session_meta import (
    classify_session_meta_question,
    render_session_meta_answer,
)
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
    ErrorCode,
    EvidenceState,
    FactAssertion,
    FactBundle,
    Game,
    GameBundle,
    GameFilters,
    GameStatus,
    HistoryRecord,
    IntelligenceMode,
    IntentName,
    MetricRef,
    NewsItem,
    NewsQuery,
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
from apps.api.src.domain.safety import (
    OutputGuard,
    OutputGuardError,
    SafetyGuard,
    neutralize_external_internal_names,
)
from apps.api.src.domain.time_policy import (
    SystemClock,
    format_beijing,
    game_end_window,
    local_date_range,
    now_utc,
    validate_timezone,
)
from apps.api.src.domain.verifier import (
    verify_bundle,
    verify_game,
    verify_premise,
    verify_stat_lines,
)
from apps.api.src.infrastructure.admission import AdmissionController
from apps.api.src.infrastructure.agent_tools import AgentToolCall, resolve_date_expression
from apps.api.src.infrastructure.hermes_agent_runtime import (
    AgentHistoryMessage,
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
    r"(?:你|您|当前|本次|系统|服务|后台|应用).{0,16}"
    r"(?:用的|使用的|调用的|配置的|接入的|基于|怎么|如何)?\s*"
    r"(?:哪个|什么|何种|哪些)?\s*"
    r"(?:模型|大模型|语言模型|LLM|框架|工具|提示词|系统提示|数据源|"
    r"接口|API|搜索方式|检索方式|缓存|数据库|技术栈|调用链|内部架构|实现)"
    r"|(?:模型|大模型|语言模型|LLM|框架|工具|提示词|系统提示|数据源|"
    r"接口|API|搜索方式|检索方式|缓存|数据库|技术栈|调用链|内部架构)"
    r".{0,16}(?:是什么|是哪一个|哪个|哪些|名称|配置|怎么|如何|用了什么)",
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

_PUBLIC_REVERIFICATION_RE = re.compile(
    r"(?:联网|在线|公开(?:资料|数据|来源)?).{0,10}(?:实时|重新|再|最新)?(?:查验|核验|查询|查一下|确认)"
    r"|(?:实时|重新|再).{0,8}(?:联网|在线|公开(?:资料|数据|来源)?).{0,8}(?:查验|核验|查询|确认)",
    re.IGNORECASE,
)

_PLAYOFF_GAME_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])G\s*(?P<number>\d{1,3})(?!\d)",
    re.IGNORECASE,
)

_SEARCH_SUBJECT_ALIASES = (
    "库里",
    "斯蒂芬·库里",
    "Stephen Curry",
    "Curry",
    "詹姆斯",
    "勒布朗",
    "LeBron James",
    "约基奇",
    "Nikola Jokic",
    "字母哥",
    "Antetokounmpo",
    "恩比德",
    "Embiid",
)

_SEARCH_TOPIC_GROUPS = (
    (
        "阵容",
        "调整",
        "交易",
        "签约",
        "续约",
        "补强",
        "引援",
        "加盟",
        "离队",
        "裁员",
        "轮换",
        "自由市场",
    ),
    ("伤病", "受伤", "缺阵", "复出"),
    ("教练", "主帅", "执教"),
    ("战术", "复盘", "防守", "进攻", "挡拆", "联防"),
    ("得分",),
    ("篮板",),
    ("助攻",),
    ("场馆", "球馆", "地点", "举办"),
    ("时长", "耗时", "多久"),
)


def _is_model_meta_question(message: str) -> bool:
    return bool(_MODEL_META_RE.search(str(message or "").strip()))


def _invalid_playoff_game_number(message: str) -> int | None:
    """Return an impossible best-of-seven game number when explicitly scoped.

    ``G9`` is meaningful only as a playoff/series label in this product.  Keep
    the check narrow so an unrelated regular-season ordinal is not rejected.
    """

    text = str(message or "").strip()
    if not re.search(r"(?:季后赛|总决赛|系列赛|首轮|半决赛|分区决赛)", text):
        return None
    numbers = [int(match.group("number")) for match in _PLAYOFF_GAME_NUMBER_RE.finditer(text)]
    return next((number for number in numbers if number > 7), None)


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


def _requests_public_reverification(message: str) -> bool:
    """Recognize an explicit request to upgrade a prior fact to public data."""

    return bool(_PUBLIC_REVERIFICATION_RE.search(str(message or "").strip()))


@dataclass(slots=True)
class ChatResult:
    request_id: UUID
    session_id: UUID
    status: str
    answer_markdown: str
    blocks: list[Any] = field(default_factory=list)
    as_of_beijing: str | None = None
    evidence_state: str = "none"
    # Generic, provider-neutral origin classification used to distinguish a
    # fixed demo snapshot from a freshly retrieved public record.
    data_origin: str = "none"
    corrections: list[Any] = field(default_factory=list)
    follow_up: str | None = None
    latency_ms: int = 0
    error: dict[str, Any] | None = None
    notices: list[dict[str, Any]] = field(default_factory=list)
    # Flower-domain turns expose a deliberately small, privacy-safe snapshot
    # of the active garden context.  Legacy NBA results leave this as ``None``
    # so the compatibility wire contract is unchanged.  The projection is
    # produced by the owning use case; never attach the full domain context or
    # a transcript here.
    garden_context: dict[str, Any] | None = None
    # Request-internal canonical identity.  A nested full-intelligence tool
    # call uses this to persist the uniquely resolved game for a later
    # deictic follow-up; it is deliberately omitted from ``to_dict`` so no
    # provider/index identifier becomes part of the public API.
    resolved_game: Game | None = None
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
            "data_origin": self.data_origin,
            "corrections": dump(self.corrections),
            "follow_up": self.follow_up,
            "latency_ms": self.latency_ms,
            "composition": dump(self.composition),
            "notices": dump(self.notices),
        }
        if self.garden_context is not None:
            # ``garden_context`` is already a server-owned projection, but run
            # it through the same conservative dumper as blocks/notices so an
            # embedding caller cannot smuggle a Pydantic/provider object into
            # the public response.
            payload["garden_context"] = dump(self.garden_context)
        if self.error is not None:
            payload = {
                "request_id": str(self.request_id),
                "session_id": str(self.session_id),
                "status": "failed",
                "error": dump(self.error),
                "notices": dump(self.notices),
            }
        return payload


@dataclass(slots=True)
class AgentObservationRecovery:
    """A truthful public outcome reconstructed from trusted tool observations."""

    markdown: str
    observations: list[dict[str, Any]]
    status: str = "completed"


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
        game_registry: Mapping[str, Game] | None = None,
        game_origin_registry: Mapping[str, str] | None = None,
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
        # Highlights requests populate this server-owned registry.  It lets a
        # later chat turn resolve a clicked live/ESPN game without trusting
        # client-supplied team names or scores.  Fixture IDs are also resolved
        # from the parser catalog for direct use-case tests.
        self.game_registry = game_registry if game_registry is not None else {}
        self.game_origin_registry = (
            game_origin_registry if game_origin_registry is not None else {}
        )
        self._public_data_mode = str(
            getattr(settings, "public_data_mode", "fixture")
        ).lower()
        self._fixture_game_aliases_enabled = self._public_data_mode == "fixture"
        self.parser = IntentParser(
            clock=self.clock,
            include_fixture_games=self._fixture_game_aliases_enabled,
        )
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
            reasoning_effort=getattr(settings, "agent_reasoning_effort", "none"),
            model_timeout_seconds=getattr(settings, "llm_timeout_seconds", 20.0),
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

    def _selected_game(self, game_id: str | None) -> Game | None:
        """Resolve a scoreboard selection from server-owned game records."""

        if not game_id:
            return None
        value = str(game_id).strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
            return None
        found = self.game_registry.get(value)
        if isinstance(found, Game):
            # In live/hybrid profiles a browser-supplied id is authorized only
            # after the highlights projection has registered both the typed
            # game and its public/demo origin.  This keeps a guessed built-in
            # fixture id from opening the private deterministic snapshot.  A
            # registered demo card remains usable because the UI has already
            # exposed it with an explicit ``demo_snapshot`` label.
            origin = str(self.game_origin_registry.get(value, "none")).lower()
            if self._fixture_game_aliases_enabled or origin in {
                "public",
                "demo_snapshot",
            }:
                return found
            return None
        if not self._fixture_game_aliases_enabled:
            # Provider internals, especially a hybrid gateway's fixture
            # fallback, are not a selected-game registry.  Never traverse them
            # in a public profile: ``selected_game_id`` is client controlled.
            return None
        # Direct use-case tests and offline callers may not go through the
        # highlights route first.  The fixture provider keeps typed games in
        # a private list; reading that server-owned list is safe and avoids
        # making a duplicate provider request just to establish context.
        owners = [
            self.provider,
            getattr(self.gateway, "provider", None),
            getattr(self.gateway, "fallback", None),
        ]
        visited: set[int] = set()
        while owners:
            owner = owners.pop(0)
            if owner is None or id(owner) in visited:
                continue
            visited.add(id(owner))
            # Provider composition is intentionally transparent for selected
            # game lookup.  A local fixture/index can sit behind the search or
            # indexed wrapper even when highlights have not populated the
            # registry yet.
            owners.extend(
                candidate
                for candidate in (
                    getattr(owner, "primary", None),
                    getattr(owner, "provider", None),
                    getattr(owner, "fallback", None),
                    getattr(owner, "detail_provider", None),
                )
                if candidate is not None
            )
            # FixtureProvider loads its snapshot lazily on the first provider
            # operation.  A card id can arrive before any chat retrieval, so
            # initialise that server-owned snapshot here; otherwise the
            # selection is reduced to an id-only reference and team questions
            # accidentally fall through to season aggregates.
            loader = getattr(owner, "_load", None)
            if callable(loader):
                try:
                    loader()
                except Exception:
                    # Selection must remain best-effort.  The normal provider
                    # path will report an unavailable/not-found result later.
                    pass
            for item in list(getattr(owner, "_games", []) or []):
                if isinstance(item, Game) and item.game_id == value:
                    return item
        return None

    def _selected_game_origin(self, game_id: str | None) -> str:
        if not game_id:
            return "none"
        value = str(game_id)
        origin = str(self.game_origin_registry.get(value, "none")).lower()
        registered = isinstance(self.game_registry.get(value), Game)
        if origin in {"public", "demo_snapshot"} and (
            registered or self._fixture_game_aliases_enabled
        ):
            return origin
        if not self._fixture_game_aliases_enabled:
            return "none"
        # A fixture-owned ID discovered without first loading highlights is a
        # snapshot by construction.  Do not let it masquerade as public just
        # because the origin registry has not yet been populated.
        if self._selected_game(value) is not None:
            return "demo_snapshot"
        return "none"

    @staticmethod
    def _selected_game_ref(game_id: str, game: Game | None) -> EntityRef:
        if game is not None:
            return EntityRef(
                kind=EntityKind.GAME,
                canonical_id=game.game_id,
                display_name=(
                    f"{game.season.label} 总决赛 G{game.series_game_number}"
                    if game.series_game_number and game.series_id
                    else f"{game.away.display_name} 对 {game.home.display_name}"
                ),
                aliases=[
                    alias
                    for alias in (
                        f"G{game.series_game_number}" if game.series_game_number else None,
                        game.home.display_name,
                        game.away.display_name,
                    )
                    if alias
                ],
                confidence=1,
            )
        fixture_ref = GAMES.get(game_id)
        if fixture_ref is not None:
            return fixture_ref
        return EntityRef(
            kind=EntityKind.GAME,
            canonical_id=game_id,
            display_name="当前选中比赛",
            confidence=1,
        )

    @staticmethod
    def _attach_selected_game(
        parsed: ParseResult,
        selected_game: Game | None,
        selected_ref: EntityRef | None,
    ) -> ParseResult:
        """Bind a clicked game for matching-team questions.

        Pronouns/PBP are already resolved by ``IntentParser`` through the
        active context.  This extra pass handles explicit matchup wording
        such as “雷霆 对 凯尔特人 谁得分最高？”, but only when both sides
        belong to the server-resolved selected game.  Unrelated teams never
        inherit the card and continue through the normal broad lookup.
        """

        if selected_game is None or selected_ref is None:
            return parsed
        if any(item.kind is EntityKind.GAME for item in parsed.intent.entities):
            return parsed
        # A concrete calendar scope is an explicit user condition. Do not
        # turn “今天/下周某队有哪些比赛” into the previously selected
        # single-game summary just because one team name overlaps.
        if parsed.intent.date_range is not None:
            return parsed
        team_ids = {
            item.canonical_id
            for item in parsed.intent.entities
            if item.kind is EntityKind.TEAM
        }
        selected_ids = {selected_game.home.canonical_id, selected_game.away.canonical_id}
        # A selected card is the user's explicit event-drill scope.  Bind
        # game-scoped questions even when the wording omits “这场”, such as
        # “比赛双方教练是谁” or “比赛持续多久”.  This is deliberately
        # limited to game-level intents: a broad history lookup and a
        # “最近一场” request must remain global, while an explicit date range
        # is handled above and remains authoritative.
        game_scoped_intent = parsed.intent.intent_name in {
            IntentName.DATA,
            IntentName.PLAY_BY_PLAY,
            IntentName.FACT_CHECK,
            IntentName.TACTICAL,
            IntentName.RECAP,
            IntentName.FOLLOW_UP,
        }
        implicit_selected_scope = (
            game_scoped_intent
            and not team_ids
            and not getattr(parsed.intent, "recent_game", False)
        )
        if not implicit_selected_scope and (
            not team_ids or not team_ids.issubset(selected_ids)
        ):
            return parsed
        # Avoid mutating the parser's list in place: ParseResult is reused by
        # telemetry/evaluation code and a copy keeps that boundary explicit.
        intent = parsed.intent.model_copy(
            update={"entities": [*parsed.intent.entities, selected_ref]}
        )
        # A generic DATA question may carry a parser-added ``subject`` slot;
        # the selected card supplies that subject just as it supplies the
        # missing game slot.  Remove only those two slots and preserve any
        # genuinely unresolved period/other constraints.
        missing = [
            slot
            for slot in parsed.missing_slots
            if slot.name not in {"game", "subject"}
        ]
        intent = intent.model_copy(update={"missing_slots": missing})
        return ParseResult(
            intent=intent,
            entity_candidates=parsed.entity_candidates,
            normalized_filters=parsed.normalized_filters,
            missing_slots=missing,
            ambiguity_reasons=parsed.ambiguity_reasons,
            confidence=parsed.confidence,
        )

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
        _parent_budget: RequestBudget | None = None,
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
                        data_origin=replay.get("data_origin", "none"),
                        corrections=replay.get("corrections", []),
                        follow_up=replay.get("follow_up"),
                        latency_ms=replay.get("latency_ms", 0),
                        error=replay.get("error"),
                        notices=replay.get("notices", []),
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
            if (
                safety.outcome is SafetyOutcome.ALLOW
                and is_unsafe_runtime_text(req.message)
                and not _is_model_meta_question(req.message)
            ):
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
                context = await self.context_manager.ensure(
                    session_id, req.client_timezone or "Asia/Shanghai"
                )
                answer = (
                    "我是 COURTSIDE，面向 NBA 球迷的问答助手。"
                    "我会结合当前会话与可用的公开赛事资料，回答比赛、球员、"
                    "关键回合和战术复盘问题。"
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
                if not _internal_tool:
                    await self._commit_context_turn(
                        context,
                        intent=self._session_meta_summary_intent(context),
                        answer=draft.markdown,
                        user_message=req.message,
                    )
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
            selected_game = self._selected_game(req.selected_game_id)
            selected_game_origin = (
                self._selected_game_origin(req.selected_game_id)
                if selected_game is not None
                else "none"
            )
            selected_ref = (
                self._selected_game_ref(req.selected_game_id, selected_game)
                if req.selected_game_id
                and (
                    selected_game is not None
                    or (
                        self._fixture_game_aliases_enabled
                        and req.selected_game_id in GAMES
                    )
                )
                else None
            )
            if selected_ref is not None:
                # Treat a card selection as the current session's active game
                # for this request.  Explicit G4/G3 entities parsed from the
                # message still take precedence, and a later card replaces
                # the previous active game on the next committed turn.
                context = context.model_copy(update={"active_game": selected_ref})
            telemetry.transition("CONTEXT_RESOLVED")
            session_meta = (
                None
                if _internal_tool
                else classify_session_meta_question(req.message)
            )
            if session_meta is not None:
                requested_mode = getattr(
                    req.intelligence_mode, "value", req.intelligence_mode
                ) or getattr(self.settings, "default_intelligence_mode", "hybrid")
                requested_full = str(requested_mode).lower() == "full"
                answer = render_session_meta_answer(
                    session_meta,
                    context,
                    requested_full=requested_full,
                    effective_full=self._full_agent_requested(req),
                )
                draft = self.output_guard.validate(
                    DraftAnswer(
                        markdown=answer,
                        blocks=[
                            AnswerBlock(type=AnswerBlockType.TEXT, content=answer)
                        ],
                        evidence_state=EvidenceState.NONE,
                    ),
                    facts=None,
                    allow_unverified_numbers=True,
                )
                telemetry.intent_category = "SESSION_META"
                telemetry.intent_name = f"SESSION_META_{session_meta.kind.value}"
                telemetry.composition_mode = "deterministic"
                telemetry.composition_status = "not_requested"
                telemetry.evidence_state = "none"
                telemetry.transition("COMPOSED")
                telemetry.transition("OUTPUT_GUARDED")
                await self._commit_context_turn(
                    context,
                    intent=self._session_meta_summary_intent(context),
                    answer=draft.markdown,
                    user_message=req.message,
                )
                result = self._result_from_draft(
                    request_id,
                    session_id,
                    "completed",
                    draft,
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
                for chunk in _chunks(draft.markdown, 120):
                    await _emit(sink, "message.delta", {"text": chunk})
                await _emit(sink, "message.completed", result.to_dict())
                return result
            invalid_game_number = _invalid_playoff_game_number(req.message)
            if invalid_game_number is not None:
                answer = (
                    "NBA 季后赛系列赛采用七场四胜制，最多进行到 **G7**；"
                    f"因此 **G{invalid_game_number} 不存在**，无法查询这场比赛的得分王。"
                )
                draft = self.output_guard.validate(
                    DraftAnswer(
                        markdown=answer,
                        blocks=[
                            AnswerBlock(type=AnswerBlockType.TEXT, content=answer)
                        ],
                        evidence_state=EvidenceState.NONE,
                        follow_up="您可以改问 G1 到 G7 中的具体场次。",
                    ),
                    facts=None,
                    allow_unverified_numbers=True,
                )
                telemetry.intent_category = "A"
                telemetry.intent_name = IntentName.DATA.value
                telemetry.composition_mode = "deterministic"
                telemetry.composition_status = "not_requested"
                telemetry.evidence_state = "none"
                telemetry.transition("COMPOSED")
                telemetry.transition("OUTPUT_GUARDED")
                if not _internal_tool:
                    await self._commit_context_turn(
                        context,
                        intent=self._agent_summary_intent(),
                        answer=draft.markdown,
                        user_message=req.message,
                    )
                result = self._result_from_draft(
                    request_id,
                    session_id,
                    "no_data",
                    draft,
                    started,
                    as_of=None,
                    composition=self._composition_from_telemetry(telemetry),
                )
                telemetry.finish(outcome="no_data", total_latency_ms=result.latency_ms)
                self.telemetry.record(telemetry)
                if client_id:
                    await self.session_store.complete_idempotency(
                        session_id, client_id, result
                    )
                for chunk in _chunks(draft.markdown, 120):
                    await _emit(sink, "message.delta", {"text": chunk})
                await _emit(sink, "message.completed", result.to_dict())
                return result
            agent_attempted = False
            agent_notices: list[dict[str, Any]] = []
            # Request-local only: a model may choose a game number, but it may
            # never manufacture the game identity that becomes session state.
            # The series lookup fills this map exclusively from typed provider
            # rows observed during the current turn.
            agent_series_candidates: dict[int, Game] = {}
            request_budget = RequestBudget(
                telemetry.deadline_at_utc,
                max_provider_operations=getattr(self.settings, "max_provider_operations", 4),
                max_retries_per_operation=getattr(self.settings, "provider_max_retries", 2),
                clock=self.clock,
            )
            # A clicked replay card is a server-resolved game scope, not a
            # reason to bypass the full Agent.  The Agent may understand and
            # explain the question, while its nba_query tool is rebound to the
            # original user wording and the validated selected_game_id below.
            # Hybrid keeps the low-latency deterministic selected-game path.
            selected_game_verified = selected_ref is not None
            authorized_demo_game_id = (
                str(req.selected_game_id)
                if selected_game is not None
                and selected_game_origin == "demo_snapshot"
                else None
            )
            full_agent_requested = self._full_agent_requested(req)
            if (
                not _internal_tool
                and full_agent_requested
            ):
                agent_attempted = True
                await _emit(
                    sink,
                    "run.status",
                    {"stage": "agent_planning", "text": "正在理解问题"},
                )
                before_agent = self._gateway_counters()
                agent_tool_runner = self._agent_tool_runner(
                    session_id=session_id,
                    context=context,
                    selected_game_id=(
                        req.selected_game_id if selected_game_verified else None
                    ),
                    original_question=req.message,
                    deadline_at_utc=telemetry.deadline_at_utc,
                    budget=request_budget,
                    token=token,
                    sink=sink,
                    series_candidates=agent_series_candidates,
                )
                resolved_objective_game_plan = (
                    self._agent_resolved_objective_game_plan(
                        req.message,
                        context=context,
                    )
                )
                expected_agent_game_id: str | None = None
                if (
                    resolved_objective_game_plan is not None
                    and resolved_objective_game_plan.operation
                    in {"get_game_summary", "get_play_by_play"}
                    and resolved_objective_game_plan.args
                    and isinstance(resolved_objective_game_plan.args[0], str)
                ):
                    expected_agent_game_id = str(
                        resolved_objective_game_plan.args[0]
                    )
                bounded_single_lookup = (
                    is_subjective_comparison_question(req.message)
                    or is_contextual_series_selection_question(req.message)
                    or resolved_objective_game_plan is not None
                )
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
                                conversation_history=self._agent_conversation_history(
                                    context
                                ),
                                deadline_at_utc=telemetry.deadline_at_utc,
                                # Subjective comparison and a series-scoped
                                # recommendation each need one useful evidence
                                # operation followed by synthesis.  In the
                                # Agent loop that means planning, observing the
                                # tool result, then one final composition pass.
                                # Keep three iterations but only one tool call:
                                # two iterations stop at the observation
                                # boundary, while four tool calls waste the
                                # request deadline on near-equivalent lookups.
                                max_iterations=(
                                    min(
                                        getattr(self.settings, "agent_max_iterations", 4),
                                        3,
                                    )
                                    if bounded_single_lookup
                                    else getattr(self.settings, "agent_max_iterations", 4)
                                ),
                                max_tool_calls=(
                                    1
                                    if bounded_single_lookup
                                    else getattr(self.settings, "agent_max_tool_calls", 4)
                                ),
                            ),
                            tool_runner=agent_tool_runner,
                            cancel=token,
                        ),
                        token,
                    )
                except Exception:
                    agent_turn = AgentTurnResult(
                        status=RuntimeStatus.UNAVAILABLE,
                        finish_reason="runtime_exception",
                    )
                # A model may answer a factual pronoun from conversation
                # history without re-querying the underlying game.  History
                # resolves language, but it is not current fact evidence.  If
                # the deterministic parser/planner can bind this turn to one
                # objective game or PBP operation, add exactly one same-scope
                # typed observation before relevance/output validation.  A
                # valid Agent answer keeps the normal agent/used composition;
                # stale or missing PBP prose is replaced by the observation.
                if (
                    agent_turn.status is RuntimeStatus.OK
                    and agent_turn.answer_markdown
                    and not agent_turn.observations
                    and self._agent_resolved_objective_game_plan(
                        req.message,
                        context=context,
                    )
                    is not None
                ):
                    try:
                        observation = dict(
                            await agent_tool_runner(
                                "nba_query",
                                {"question": req.message},
                            )
                        )
                        observation_status = str(
                            observation.get("status") or "failed"
                        ).lower()
                        if observation_status in {
                            "completed",
                            "no_data",
                            "needs_clarification",
                        }:
                            observation["coverage"] = (
                                "server_typed_pbp_grounding"
                                if self._agent_typed_pbp_game_id(
                                    req.message,
                                    context=context,
                                )
                                is not None
                                else "server_typed_game_grounding"
                            )
                            observations = [observation]
                            agent_turn = agent_turn.model_copy(
                                update={
                                    "observations": observations,
                                    "tool_calls": [
                                        AgentToolCall(
                                            "nba_query",
                                            "controlled_typed_grounding",
                                            observation_status,
                                            0,
                                            str(
                                                observation.get("evidence_state")
                                                or "none"
                                            ),
                                        )
                                    ],
                                    "evidence_state": self._agent_evidence_state(
                                        observations,
                                        agent_turn.evidence_state,
                                    ).value.lower(),
                                }
                            )
                    except Exception:
                        # This is bounded evidence enrichment.  Existing
                        # deterministic recovery still handles a provider
                        # failure without changing the public error contract.
                        pass
                # A capable Agent may decide that a contextual recommendation
                # can be answered from the conversation alone and return no
                # tool call (or merely echo the candidate list it saw in
                # history).  That is not enough grounding for a game choice.
                # Perform one server-owned, typed series lookup before judging
                # the prose.  A qualified answer is then validated against the
                # retrieved candidates; an unqualified/candidate-only answer
                # can be repaired by the scoped recommendation recovery below.
                if (
                    not _internal_tool
                    and is_contextual_series_selection_question(req.message)
                    and self._open_ended_scope_is_resolved(req.message, context)
                    and not self._agent_has_series_candidate_observation(agent_turn)
                ):
                    try:
                        agent_turn = await self._augment_series_recommendation_turn(
                            req.message,
                            agent_turn,
                            context=context,
                            selected_game_id=(
                                req.selected_game_id if selected_game_verified else None
                            ),
                            budget=request_budget,
                            token=token,
                            sink=sink,
                            series_candidates=agent_series_candidates,
                        )
                    except Exception:
                        # Retrieval is bounded and best-effort here.  The normal
                        # Agent/deterministic safety paths still decide whether
                        # an answer can be shown when the lookup itself fails.
                        pass
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
                agent_notices = self._agent_public_notices(agent_turn)
                if (
                    agent_turn.status is RuntimeStatus.OK
                    and agent_turn.answer_markdown
                ):
                    # Hermes is responsible for planning, but a valid-looking
                    # answer from the wrong NBA tool is still incorrect.  The
                    # most dangerous case is a recent play-by-play question
                    # answered with an empty schedule observation.  Detect
                    # this narrow semantic mismatch before OutputGuard and
                    # let the deterministic parser recover the verified PBP.
                    agent_result_relevant = self._agent_result_relevant(
                        req.message,
                        agent_turn,
                        expected_game_id=expected_agent_game_id,
                    )
                    repairable_series_selection = bool(
                        is_positive_series_selection_question(req.message)
                        and agent_series_candidates
                        and self._agent_has_series_candidate_observation(agent_turn)
                    )
                    comparison_recovery = self._player_comparison_recovery(
                        req.message,
                        agent_turn.observations,
                    )
                    repairable_player_comparison = bool(
                        not agent_result_relevant
                        and comparison_recovery is not None
                    )
                    if (
                        not agent_result_relevant
                        and not repairable_series_selection
                        and not repairable_player_comparison
                    ):
                        telemetry.fallback_reason = "agent_tool_mismatch"
                    else:
                        agent_answer = self._ground_agent_answer(
                            req.message,
                            agent_turn.answer_markdown,
                            agent_turn.observations,
                        )
                        if repairable_player_comparison and comparison_recovery is not None:
                            # The search completed, but its evidence may cover
                            # only one side and the model may answer with an
                            # unnecessary clarification. Keep the successful
                            # full-intelligence route while replacing that
                            # incomplete prose with a neutral, non-numeric
                            # comparison framework.
                            agent_answer = comparison_recovery.markdown
                            telemetry.fallback_reason = (
                                "agent_comparison_quality_repaired"
                            )
                        if (
                            is_positive_series_selection_question(req.message)
                            and agent_series_candidates
                            and (
                                not self._series_recommendation_facts_valid(
                                    agent_answer,
                                    agent_series_candidates,
                                )
                                or not self._series_recommendation_context_valid(
                                    req.message,
                                    agent_answer,
                                    agent_turn.observations,
                                )
                            )
                        ):
                            # Preference remains Agent-owned, but its stated
                            # score/margin/home-away relations are objective.
                            # If one of those conflicts with the typed series
                            # rows, replace the unsafe prose with the existing
                            # scoped recommendation recovery before it reaches
                            # OutputGuard.  This is still the successful full-
                            # intelligence route when the Agent performed its
                            # own lookup; only a separately injected grounding
                            # observation retains the explicit fallback label.
                            recovered = self._recover_observation_answer(
                                req.message,
                                agent_turn.observations,
                                agent_series_candidates,
                                self._recommended_game_number_from_answer(
                                    agent_answer
                                ),
                            )
                            if recovered is not None:
                                agent_answer = recovered.markdown
                                telemetry.fallback_reason = "agent_answer_repaired"
                        if not self._tactical_answer_is_high_quality(
                            req.message,
                            agent_answer,
                            agent_turn.observations,
                        ):
                            # A successful search/tool turn can still be a poor
                            # answer when the model merely joins article
                            # snippets with words such as “另外”.  Keep the
                            # successful Agent route and its evidence metadata,
                            # but repair the public prose into bounded,
                            # actionable basketball advice.
                            agent_answer = self._tactical_advice_recovery(
                                req.message
                            )
                            telemetry.fallback_reason = (
                                "agent_tactical_quality_repaired"
                            )
                        elif self._is_tactical_advice_question(req.message):
                            # The user-supplied short player reference is
                            # sufficient for this advisory turn and avoids a
                            # conservative proper-name guard mistaking a long
                            # Chinese clause around an interpunct name for a
                            # newly invented person.
                            agent_answer = self._compact_question_player_names(
                                req.message,
                                agent_answer,
                            )
                        # Search observations are internal grounding material.
                        # Project them once more at the public boundary so a
                        # model/recovery path cannot expose “补充线索” lists or
                        # provider-shaped snippets in the API response.
                        agent_answer = self._strip_public_search_sections(agent_answer)
                        evidence = self._agent_evidence_state(
                            agent_turn.observations,
                            agent_turn.evidence_state,
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
                            elif self._is_tactical_advice_question(req.message):
                                recovered_answer = self._tactical_advice_recovery(
                                    req.message
                                )
                                try:
                                    guarded = self.output_guard.validate_agent(
                                        recovered_answer,
                                        agent_turn.observations,
                                        require_observation=True,
                                    )
                                    telemetry.fallback_reason = (
                                        "agent_tactical_quality_repaired"
                                    )
                                except (OutputGuardError, ValueError, TypeError):
                                    telemetry.fallback_reason = "agent_output_guard"
                            else:
                                # Do not throw away a useful tool result just
                                # because the model added an untraceable
                                # number, name, or a process phrase.  First
                                # try a local numeric redaction of the model
                                # supplement; if that is still unsafe, keep
                                # the server-owned observation as the answer.
                                # Core fact mismatches are already prevented
                                # by _ground_agent_answer and remain blocked.
                                # ``agent_answer`` has already been cleaned by
                                # _ground_agent_answer.  The observation path
                                # below is deliberately preferred over trying
                                # to infer which proper name the model meant.
                                recovered = self._agent_observation_answer(
                                    req.message,
                                    agent_turn,
                                    series_candidates=agent_series_candidates,
                                    expected_game_id=expected_agent_game_id,
                                )
                                if recovered is not None and recovered.status == "completed":
                                    recovered_answer = self._strip_public_search_sections(
                                        recovered.markdown
                                    )
                                    try:
                                        guarded = self.output_guard.validate_agent(
                                            recovered_answer,
                                            recovered.observations,
                                            require_observation=True,
                                        )
                                        telemetry.fallback_reason = "agent_answer_repaired"
                                    except (OutputGuardError, ValueError, TypeError):
                                        telemetry.fallback_reason = "agent_output_guard"
                                else:
                                    telemetry.fallback_reason = "agent_output_guard"
                        if guarded is not None:
                            # The runtime envelope is advisory.  The
                            # server-owned observations are authoritative for
                            # whether this answer is verified, partial, or has
                            # no external evidence; recovery validation must
                            # not accidentally reset partial search evidence.
                            evidence = self._agent_evidence_state(
                                agent_turn.observations,
                                agent_turn.evidence_state,
                            )
                            guarded = guarded.model_copy(
                                update={"evidence_state": evidence}
                            )
                            await _emit(
                                sink,
                                "run.status",
                                {"stage": "agent_completing", "text": "已完成回答"},
                            )
                            # The runtime completed this turn.  Typed
                            # grounding and relation repair are part of the
                            # Agent answer guard, not a route downgrade; only
                            # an unavailable/timeout runtime enters the later
                            # deterministic fallback branch.
                            telemetry.composition_mode = "agent"
                            telemetry.composition_status = "used"
                            telemetry.evidence_state = evidence.value.lower()
                            telemetry.transition("COMPOSED")
                            telemetry.transition("OUTPUT_GUARDED")
                            # The Agent path returns before the deterministic
                            # parser normally runs.  Still project explicit
                            # entities (for example “2025-26 总决赛 G4”) into
                            # the application session so a later “这场比赛”
                            # turn has a server-owned active game.  The model
                            # answer itself is never treated as fact; this
                            # lightweight parse only updates conversational
                            # scope and falls back to the generic summary on
                            # malformed/out-of-scope text.
                            await self._commit_context_turn(
                                context,
                                intent=self._agent_context_intent(
                                    req.message,
                                    context,
                                    answer=guarded.markdown,
                                    series_candidates=agent_series_candidates,
                                    observations=agent_turn.observations,
                                ),
                                facts=FactBundle(
                                    facts=[], evidence_state=EvidenceState.NONE
                                ),
                                answer=guarded.markdown,
                                user_message=req.message,
                            )
                            as_of = self._agent_as_of(agent_turn.observations)
                            data_origin = self._agent_data_origin(
                                agent_turn.observations
                            )
                            result = self._result_from_draft(
                                request_id,
                                session_id,
                                "completed",
                                guarded,
                                started,
                                as_of=as_of,
                                data_origin=data_origin,
                                composition=self._composition_from_telemetry(telemetry),
                                notices=agent_notices,
                            )
                            telemetry.finish(
                                outcome="completed", total_latency_ms=result.latency_ms
                            )
                            self.telemetry.record(telemetry)
                            if client_id:
                                await self.session_store.complete_idempotency(
                                    session_id, client_id, result
                                )
                            for chunk in _chunks(guarded.markdown, 120):
                                await _emit(sink, "message.delta", {"text": chunk})
                            await _emit(sink, "message.completed", result.to_dict())
                            return result
                # Hermes may stop with a timeout/iteration-boundary status
                # after a tool has already completed.  Reuse that observation
                # before entering the deterministic parser.  This keeps the
                # Agent-first contract useful under normal provider latency,
                # while the relevance and output guards above still protect
                # against wrong-tool or unsafe fact answers.
                if agent_attempted and agent_turn.observations:
                    recovered = self._agent_observation_answer(
                        req.message,
                        agent_turn,
                        series_candidates=agent_series_candidates,
                        expected_game_id=expected_agent_game_id,
                    )
                    if recovered is not None:
                        recovered_answer = self._strip_public_search_sections(
                            recovered.markdown
                        )
                        try:
                            recovered_draft = self.output_guard.validate_agent(
                                recovered_answer,
                                recovered.observations,
                                require_observation=True,
                            )
                        except (OutputGuardError, ValueError, TypeError):
                            recovered_draft = None
                        if recovered_draft is not None:
                            telemetry.composition_mode = "fallback"
                            telemetry.composition_status = "fallback"
                            telemetry.fallback_reason = (
                                telemetry.fallback_reason or "agent_answer_recovered"
                            )
                            recovered_evidence = self._agent_evidence_state(
                                recovered.observations,
                                getattr(agent_turn, "evidence_state", "none"),
                            )
                            recovered_draft = recovered_draft.model_copy(
                                update={"evidence_state": recovered_evidence}
                            )
                            telemetry.evidence_state = recovered_evidence.value.lower()
                            telemetry.transition("COMPOSED")
                            telemetry.transition("OUTPUT_GUARDED")
                            await self._commit_context_turn(
                                context,
                                intent=self._agent_context_intent(
                                    req.message,
                                    context,
                                    answer=recovered_draft.markdown,
                                    series_candidates=agent_series_candidates,
                                    observations=recovered.observations,
                                ),
                                facts=FactBundle(
                                    facts=[], evidence_state=EvidenceState.NONE
                                ),
                                answer=recovered_draft.markdown,
                                user_message=req.message,
                            )
                            result = self._result_from_draft(
                                request_id,
                                session_id,
                                recovered.status,
                                recovered_draft,
                                started,
                                as_of=self._agent_as_of(agent_turn.observations),
                                data_origin=self._agent_data_origin(
                                    agent_turn.observations
                                ),
                                composition=self._composition_from_telemetry(telemetry),
                                notices=agent_notices,
                            )
                            telemetry.finish(
                                outcome=recovered.status,
                                total_latency_ms=result.latency_ms,
                            )
                            self.telemetry.record(telemetry)
                            if client_id:
                                await self.session_store.complete_idempotency(
                                    session_id, client_id, result
                                )
                            for chunk in _chunks(recovered_draft.markdown, 120):
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
                    if not _internal_tool:
                        await self._commit_context_turn(
                            context,
                            intent=self._agent_summary_intent(),
                            answer=local_draft.markdown,
                            user_message=req.message,
                        )
                    result = self._result_from_draft(
                        request_id,
                        session_id,
                        "completed",
                        local_draft,
                        started,
                        as_of=None,
                        composition=self._composition_from_telemetry(telemetry),
                        notices=agent_notices,
                    )
                    telemetry.finish(
                        outcome="completed", total_latency_ms=result.latency_ms
                    )
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
                    {"stage": "agent_fallback", "text": "正在整理回答"},
                )
                if (
                    self._is_open_ended_agent_synthesis(req.message)
                    and self._open_ended_scope_is_resolved(req.message, context)
                    and not agent_notices
                ):
                    if agent_turn.error_code is not None:
                        runtime_kind = {
                            ErrorCode.UPSTREAM_TIMEOUT: ProviderErrorKind.TIMEOUT,
                            ErrorCode.UPSTREAM_RATE_LIMITED: ProviderErrorKind.RATE_LIMITED,
                            ErrorCode.UPSTREAM_AUTH: ProviderErrorKind.AUTH,
                            ErrorCode.INVALID_UPSTREAM_DATA: (
                                ProviderErrorKind.SCHEMA_MISMATCH
                            ),
                        }.get(agent_turn.error_code, ProviderErrorKind.HTTP)
                        runtime_error = type(
                            "RuntimeFailure",
                            (),
                            {
                                "kind": runtime_kind,
                                "retryable": agent_turn.retryable,
                                "safe_message": "智能回答服务暂时不可用，请稍后重试。",
                            },
                        )()
                        return await self._technical_failure(
                            request_id,
                            session_id,
                            runtime_error,
                            telemetry,
                            started,
                            sink,
                            client_id,
                            code=agent_turn.error_code.value,
                        )
                    # A generic objective parser cannot manufacture a useful
                    # recommendation, comparison, tactical explanation, or
                    # recap.  Once the user's scope is clear, returning an
                    # unrelated score/list is worse than an honest, scoped
                    # evidence limitation.
                    fallback_answer = self._open_ended_unavailable_answer(req.message)
                    fallback_draft = DraftAnswer(
                        markdown=fallback_answer,
                        blocks=[
                            AnswerBlock(
                                type=AnswerBlockType.TEXT,
                                content=fallback_answer,
                            )
                        ],
                        evidence_state=EvidenceState.NONE,
                    )
                    telemetry.evidence_state = "none"
                    telemetry.transition("COMPOSED")
                    telemetry.transition("OUTPUT_GUARDED")
                    await self._commit_context_turn(
                        context,
                        intent=self._agent_context_intent(req.message, context),
                        answer=fallback_draft.markdown,
                        user_message=req.message,
                    )
                    result = self._result_from_draft(
                        request_id,
                        session_id,
                        "no_data",
                        fallback_draft,
                        started,
                        as_of=None,
                        composition=self._composition_from_telemetry(telemetry),
                    )
                    telemetry.finish(outcome="no_data", total_latency_ms=result.latency_ms)
                    self.telemetry.record(telemetry)
                    if client_id:
                        await self.session_store.complete_idempotency(
                            session_id, client_id, result
                        )
                    for chunk in _chunks(fallback_draft.markdown, 120):
                        await _emit(sink, "message.delta", {"text": chunk})
                    await _emit(sink, "message.completed", result.to_dict())
                    return result
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
                if not _internal_tool:
                    await self._commit_context_turn(
                        context,
                        intent=self._agent_summary_intent(),
                        answer=local_draft.markdown,
                        user_message=req.message,
                    )
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
            parser = IntentParser(
                clock=self.clock,
                input_timezone=context.timezone,
                include_fixture_games=self._fixture_game_aliases_enabled,
            )
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
            if selected_ref is not None:
                parsed = self._attach_selected_game(
                    parsed, selected_game, selected_ref
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
                if not _internal_tool:
                    await self._commit_context_turn(
                        context,
                        intent=parsed.intent,
                        answer=draft.markdown,
                        user_message=req.message,
                    )
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
                if not _internal_tool:
                    await self._commit_context_turn(
                        context,
                        intent=parsed.intent,
                        answer=draft.markdown,
                        user_message=req.message,
                    )
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
                if not _internal_tool:
                    await self._commit_context_turn(
                        context,
                        intent=parsed.intent,
                        answer=draft.markdown,
                        user_message=req.message,
                    )
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
            # Agent tool calls pass a shared request budget so a four-step
            # plan cannot multiply the provider-operation cap once per nested
            # tool.  Ordinary top-level requests still create their own
            # budget here.
            budget = _parent_budget or request_budget or RequestBudget(
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
                provider_result = await self._call_plan(
                    plan,
                    budget,
                    token,
                    authorized_demo_game_id=authorized_demo_game_id,
                )
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
            agent_notices = self._merge_public_notices(
                agent_notices,
                self._provider_public_notices(provider_result),
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
                    notices=agent_notices,
                )
            telemetry.transition("NORMALIZED")
            data = provider_result.data
            if data is None or data == []:
                if agent_notices:
                    technical_code, technical_message, technical_retryable = (
                        self._agent_failure_error(agent_turn, agent_notices)
                    )
                    return await self._technical_failure(
                        request_id,
                        session_id,
                        type(
                            "AgentCapabilityFailure",
                            (),
                            {
                                "kind": ProviderErrorKind.HTTP,
                                "retryable": technical_retryable,
                                "safe_message": technical_message,
                            },
                        )(),
                        telemetry,
                        started,
                        sink,
                        client_id,
                        code=technical_code,
                        retryable=technical_retryable,
                        message=technical_message,
                        notices=agent_notices,
                    )
                draft = self._no_data_draft(parsed)
                # An unrelated clicked card is still a successfully handled
                # conversational turn: the answer explicitly says that the
                # requested head-to-head record was not found, rather than
                # surfacing an error state or silently substituting the card.
                # Keep ordinary empty lookups as ``no_data`` for API clients.
                empty_status = (
                    "completed"
                    if selected_game_verified and getattr(parsed.intent, "matchup", False)
                    else "no_data"
                )
                result = self._result_from_draft(
                    request_id,
                    session_id,
                    empty_status,
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
                telemetry.finish(outcome=empty_status, total_latency_ms=result.latency_ms)
                if not _internal_tool:
                    await self._commit_context_turn(
                        context,
                        intent=parsed.intent,
                        answer=draft.markdown,
                        user_message=req.message,
                    )
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
                force_template=(
                    _internal_tool
                    or agent_attempted
                    or (selected_game_verified and not full_agent_requested)
                ),
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
                await self._commit_context_turn(
                    context,
                    intent=parsed.intent,
                    facts=facts,
                    answer=guarded.markdown,
                    user_message=req.message,
                )
            result = self._result_from_draft(
                request_id,
                session_id,
                "completed",
                guarded,
                started,
                as_of=(
                    None
                    if self._data_origin(provider_result.evidence)
                    == "demo_snapshot"
                    else format_beijing(provider_result.retrieved_at_utc)
                ),
                data_origin=self._data_origin(provider_result.evidence),
                composition=self._composition_from_telemetry(telemetry),
                notices=agent_notices,
                resolved_game=self._uniquely_resolved_game(data),
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
            if getattr(intent, "matchup", False):
                teams = [
                    item.display_name
                    for item in intent.entities
                    if item.kind is EntityKind.TEAM
                ]
                matchup = " 对 ".join(dict.fromkeys(teams[:2])) or "该对阵"
                season_text = (
                    f" {intent.season.label} 赛季" if intent.season is not None else ""
                )
                message = f"暂未找到 **{matchup}**{season_text}的公开交手记录。"
                return self.template_composer.no_data(
                    message=message,
                    follow_up="可以补充具体日期或赛季，我再继续查找。",
                )
            if date_range is not None:
                start = format_beijing(date_range.start_inclusive).split(" ", 1)[0]
                end = format_beijing(date_range.end_exclusive - timedelta(microseconds=1)).split(
                    " ", 1
                )[0]
                scope = start if start == end else f"{start} 至 {end}"
                message = f"北京时间 **{scope}** 暂无可核验的 NBA 比赛。"
                return self.template_composer.no_data(
                    message=message,
                    follow_up="可以换一个日期，或切换左侧“赛事下钻”（精彩回顾）查看最近 5 场比赛。",
                )
            return self.template_composer.no_data(
                message="暂时没有返回可核验的 NBA 赛程。",
                follow_up="请指定日期（例如今天、明天或下周），我再帮您查询。",
            )

        if getattr(intent, "matchup", False):
            teams = [
                item.display_name
                for item in intent.entities
                if item.kind is EntityKind.TEAM
            ]
            matchup = " 对 ".join(dict.fromkeys(teams[:2])) or "该对阵"
            season_text = (
                f" {intent.season.label} 赛季" if intent.season is not None else ""
            )
            return self.template_composer.no_data(
                message=f"暂未找到 **{matchup}**{season_text}的公开比赛记录。",
                follow_up="可以补充具体日期或赛季，我再继续查找。",
            )

        subject = next(
            (item for item in intent.entities if item.kind in {EntityKind.PLAYER, EntityKind.TEAM}),
            None,
        )
        metric_names = {
            str(getattr(metric, "name", "")).casefold()
            for metric in getattr(intent, "metrics", [])
        }
        if "news" in metric_names or "background" in metric_names:
            subject_name = subject.display_name if subject is not None else "该范围"
            return self.template_composer.no_data(
                message=f"暂未找到 **{subject_name}** 的相关新闻或背景资料。",
                follow_up="可以换一个日期、球队或球员，我再继续查找。",
            )
        if intent.intent_name is IntentName.DATA and subject is not None:
            return self.template_composer.no_data(
                message=f"暂未找到 **{subject.display_name}** 的公开统计记录。",
                follow_up="可以补充赛季、比赛或统计范围，我再继续核对。",
            )
        if intent.intent_name is IntentName.PLAY_BY_PLAY:
            return self.template_composer.no_data(
                message="当前没有找到可用的逐回合记录。",
                follow_up="请指定具体比赛，或先从左侧“赛事下钻”（精彩回顾）选择一场比赛。",
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
        """Pass the safety-accepted user wording to the Agent unchanged.

        Intent repair belongs to the Agent.  Rewriting an apparently obvious
        typo here made the model reason about a server-authored paraphrase
        rather than the user's actual turn and obscured routing regressions.
        ``ChatRequest`` has already applied the public input constraints.
        """

        return str(message or "")

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
        for summary in reversed(
            list(getattr(context, "recent_turn_summaries", []) or [])
        ):
            teams: list[str] = []
            seen_team_ids: set[str] = set()
            for item in list(getattr(summary, "active_refs", []) or []):
                if (
                    isinstance(item, EntityRef)
                    and item.kind is EntityKind.TEAM
                    and item.canonical_id not in seen_team_ids
                ):
                    seen_team_ids.add(item.canonical_id)
                    teams.append(item.display_name)
            if len(teams) >= 2:
                parts.append("当前对阵：" + "、".join(teams[:2]))
                break
        active_season = getattr(context, "active_season", None)
        if active_season is not None:
            label = str(getattr(active_season, "label", active_season)).strip()
            if label:
                parts.append("当前赛季：" + label[:32])
        value = "\n".join(parts)
        return value[:3000] or None

    @staticmethod
    def _agent_conversation_history(context: Any) -> list[AgentHistoryMessage]:
        """Project application summaries into a bounded Hermes transcript.

        The application session remains authoritative for isolation and TTL.
        Hermes receives no raw session id and keeps native memory disabled;
        this explicit projection gives it conversational continuity without
        allowing an old answer to become current factual evidence.
        """

        history: list[AgentHistoryMessage] = []
        summaries = list(getattr(context, "recent_turn_summaries", []) or [])[-4:]
        for summary in summaries:
            user = " ".join(str(getattr(summary, "user_message", "") or "").split())[:600]
            assistant = " ".join(
                str(getattr(summary, "text_summary", "") or "").split()
            )[:1800]
            if not user or not assistant:
                continue
            if is_unsafe_runtime_text(user) or is_unsafe_runtime_text(assistant):
                continue
            history.extend(
                [
                    AgentHistoryMessage(role="user", content=user),
                    AgentHistoryMessage(role="assistant", content=assistant),
                ]
            )
        return history

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
    def _recommended_game_number_from_answer(answer: str) -> int | None:
        """Read an explicit recommendation without mistaking candidate mentions.

        The Agent is free to phrase a recommendation naturally.  Persisting the
        selected game used to depend on the narrow form ``推荐 G4``; equally
        clear answers such as ``首选第四场`` or ``G4 更值得看`` therefore lost
        their follow-up scope.  Keep the recogniser deliberately preference-
        bound so a plain ``G1、G2、G3`` candidate list is never treated as a
        choice, then resolve the number only against request-observed games in
        ``_trusted_recommended_game``.
        """

        text = re.sub(r"[*_`]", "", str(answer or ""))
        token = (
            r"(?:G\s*(?P<g>[1-7])|第\s*"
            r"(?:(?P<d>[1-7])|(?P<c>[一二三四五六七]))\s*(?:场|战))"
        )
        before = re.compile(
            rf"(?:推荐|选择(?:是|为)?|会选|选|挑|首选|优先(?:推荐|考虑)?|"
            rf"答案(?:是|为)|最(?:精华|精彩|好看|经典|值得(?:看|回看))"
            rf"(?:的)?(?:是|为)?|(?:若|如果|假如)?只能(?:看|回看)"
            rf"(?:一场|一战)(?:的话)?(?:我)?(?:会)?(?:看|选|推荐))\s*{token}",
            re.IGNORECASE,
        )
        after = re.compile(
            rf"{token}\s*(?:更|最|比较)?\s*"
            rf"(?:值得(?:看|回看)|精彩|精华|好看|经典|有观赏价值|"
            rf"是(?:我的)?(?:首选|选择|推荐)|更合适|最合适)",
            re.IGNORECASE,
        )
        chinese = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
        }
        for pattern in (before, after):
            for match in pattern.finditer(text):
                # A rejected candidate is often mentioned immediately before
                # the actual choice (``不推荐 G2，我推荐 G4``).  Regex search
                # starts at ``推荐`` and would otherwise persist G2 as session
                # state even though the public answer clearly chose G4.
                prefix = text[max(0, match.start() - 12) : match.start()]
                if re.search(
                    r"(?:(?:并)?不(?:太|再|会|想|是)?(?:我(?:的)?)?|"
                    r"没(?:有)?|别|不要)\s*$",
                    prefix,
                ):
                    continue
                if match.group("g") or match.group("d"):
                    return int(match.group("g") or match.group("d"))
                return chinese.get(str(match.group("c")))
        return None

    @staticmethod
    def _trusted_recommended_game(
        answer: str,
        series_candidates: Mapping[int, Game] | None,
    ) -> Game | None:
        """Resolve a model choice only against request-observed game numbers."""

        if not series_candidates:
            return None
        number = ChatUseCase._recommended_game_number_from_answer(answer)
        return series_candidates.get(number) if number is not None else None

    @staticmethod
    def _series_recommendation_facts_valid(
        answer: str,
        series_candidates: Mapping[int, Game] | None,
    ) -> bool:
        """Validate relation claims in a recommendation against typed games.

        A normal output guard can prove that names and numbers occurred in an
        observation, but it cannot tell that ``G3`` was an away win, that its
        four-point margin was not the series minimum, or which game a repeated
        ``105–104`` belongs to.  Keep this guard series-scoped and relation-
        based: subjective preference stays model-owned, while any score,
        winner, venue-side or margin comparison it chooses to cite must agree
        with the request-owned candidate map.
        """

        candidates = {
            int(number): game
            for number, game in (series_candidates or {}).items()
            if game.home_score is not None and game.away_score is not None
        }
        if not candidates:
            return True
        selected_number = ChatUseCase._recommended_game_number_from_answer(answer)
        if selected_number is None or selected_number not in candidates:
            return False

        text = unicodedata.normalize("NFKC", re.sub(r"[*_`]", "", str(answer or "")))
        if not text.strip():
            return False
        for chinese, digit in {
            "一分": "1分",
            "两分": "2分",
            "二分": "2分",
            "三分": "3分",
            "四分": "4分",
            "五分": "5分",
            "六分": "6分",
            "七分": "7分",
            "八分": "8分",
            "九分": "9分",
            "十分": "10分",
        }.items():
            text = text.replace(chinese, digit)

        game_token = re.compile(r"(?<![A-Za-z0-9])G\s*([1-7])(?!\d)", re.IGNORECASE)
        ordinal_game_token = re.compile(
            r"第\s*([1-7一二三四五六七])\s*(?:场|战)"
        )
        ordinal_values = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
        }

        def mentioned_game_numbers(value: str) -> list[int]:
            numbers = [int(item) for item in game_token.findall(value)]
            numbers.extend(
                int(item) if item.isdigit() else ordinal_values[item]
                for item in ordinal_game_token.findall(value)
            )
            return list(dict.fromkeys(numbers))

        mentioned_numbers = mentioned_game_numbers(text)
        if any(number not in candidates for number in mentioned_numbers):
            return False

        def margin(game: Game) -> int:
            return abs(int(game.home_score or 0) - int(game.away_score or 0))

        def winner_id(game: Game) -> str:
            return (
                game.home.canonical_id
                if int(game.home_score or 0) > int(game.away_score or 0)
                else game.away.canonical_id
            )

        def aliases(team: EntityRef) -> str:
            values = sorted(
                {
                    str(value).strip()
                    for value in (team.display_name, *team.aliases)
                    if str(value).strip()
                },
                key=len,
                reverse=True,
            )
            return "(?:" + "|".join(re.escape(value) for value in values) + ")"

        minimum_margin = min(margin(game) for game in candidates.values())
        maximum_margin = max(margin(game) for game in candidates.values())
        clauses = [
            value.strip()
            for value in re.split(r"[。！？!?；;：:\n]+", text)
            if value.strip()
        ]
        for clause in clauses:
            clause_numbers = mentioned_game_numbers(clause)
            has_scoped_fact = bool(
                re.search(
                    r"(?:\d{2,3}\s*[-–—:：比]\s*\d{2,3}|分差|险胜|惜败|"
                    r"主场|客场|战胜|击败|取胜|获胜|赢(?:了|下)?|不敌|负于|输给)",
                    clause,
                )
            )
            scoped_numbers = clause_numbers or (
                [selected_number] if has_scoped_fact else []
            )

            # A terminal score in a G-scoped clause must be that game's score.
            # Series progress such as 3–1 is deliberately excluded here.
            score_pairs = [
                (int(left), int(right))
                for left, right in re.findall(
                    r"(?<!\d)(\d{2,3})\s*[-–—:：比]\s*(\d{2,3})(?!\d)",
                    clause,
                )
            ]
            if score_pairs and len(scoped_numbers) != 1:
                return False
            if score_pairs:
                game = candidates[scoped_numbers[0]]
                expected = {int(game.home_score or 0), int(game.away_score or 0)}
                if any({left, right} != expected for left, right in score_pairs):
                    return False

            # Explicit numeric margins apply to every game joined by “同为”.
            margin_values = {
                int(value)
                for value in re.findall(
                    r"(?:分差(?:为|是|只有|仅有|达到|达)?\s*|"
                    r"(?:只有|仅有)\s*)(\d{1,2})\s*分",
                    clause,
                )
            }
            margin_values.update(
                int(value)
                for value in re.findall(
                    r"(\d{1,2})\s*分\s*(?:分差|险胜|惜败)",
                    clause,
                )
            )
            if margin_values:
                targets = clause_numbers or [selected_number]
                if any(
                    margin(candidates[number]) not in margin_values
                    for number in targets
                ):
                    return False

            # Home/away and winner/loser roles are checked per scoped game.
            if len(scoped_numbers) == 1:
                game = candidates[scoped_numbers[0]]
                teams = (game.home, game.away)
                for team in teams:
                    team_pattern = aliases(team)
                    expected_side = (
                        "主场"
                        if team.canonical_id == game.home.canonical_id
                        else "客场"
                    )
                    side_matches = re.findall(
                        rf"(?:{team_pattern})\s*(?:在|坐镇|作为)?\s*(主场|客场)|"
                        rf"(主场|客场)(?:作战|出战)?(?:的)?\s*(?:{team_pattern})",
                        clause,
                        re.IGNORECASE,
                    )
                    sides = {left or right for left, right in side_matches}
                    if sides and sides != {expected_side}:
                        return False
                    says_win = bool(
                        re.search(
                            rf"(?:{team_pattern})[^。！？!?；;]{{0,80}}"
                            r"(?:战胜|击败|取胜|获胜|赢(?:了|下)?)",
                            clause,
                            re.IGNORECASE,
                        )
                    )
                    says_loss = bool(
                        re.search(
                            rf"(?:{team_pattern})[^。！？!?；;]{{0,80}}"
                            r"(?:不敌|负于|输给|失利)",
                            clause,
                            re.IGNORECASE,
                        )
                    )
                    is_winner = team.canonical_id == winner_id(game)
                    if (says_win and not is_winner) or (says_loss and is_winner):
                        return False

                # Natural prose often omits the team name after the selected
                # game has already been introduced (for example “作为主场连续
                # 第 2 场险胜”). In that shape, the side claim applies to the
                # game's winner. Validate it just like an explicit
                # “尼克斯主场取胜” relation; otherwise a fluent but wrong
                # home/away claim can cross the normal proper-name guard.
                implicit_home_win = bool(
                    re.search(
                        r"(?<!对手)(?:(?:作为|坐镇|回到)\s*)?主场"
                        r"[^。！？!?；;]{0,36}(?:险胜|取胜|获胜|赢(?:了|下)?)",
                        clause,
                    )
                )
                implicit_away_win = bool(
                    re.search(
                        r"(?:(?:作为|奔赴|来到)\s*)?客场"
                        r"[^。！？!?；;]{0,36}(?:险胜|取胜|获胜|赢(?:了|下)?)",
                        clause,
                    )
                )
                winner = winner_id(game)
                if (
                    implicit_home_win
                    and winner != game.home.canonical_id
                    or implicit_away_win
                    and winner != game.away.canonical_id
                ):
                    return False

            target_numbers = clause_numbers or [selected_number]
            if re.search(r"(?:分差最小|最小分差|比分最接近)", clause):
                if any(margin(candidates[number]) != minimum_margin for number in target_numbers):
                    return False
            minimum_ties = sum(
                margin(game) == minimum_margin for game in candidates.values()
            )
            if minimum_ties > 1 and re.search(
                r"(?:唯一[^。！？!?；;]{0,30}(?:分差|接近|险胜)|"
                r"(?:分差|接近|险胜)[^。！？!?；;]{0,30}唯一)",
                clause,
            ):
                return False
            if re.search(r"(?:分差最大|最大分差|比分最悬殊)", clause):
                if any(margin(candidates[number]) != maximum_margin for number in target_numbers):
                    return False

            # “相比之下，G2 和 G4 的分差都更大” omits the comparison
            # object; in a recommendation its natural baseline is the chosen
            # game.  Validate that shorthand as well as explicit Gx 比 Gy.
            if "分差" in clause and clause_numbers and selected_number not in clause_numbers:
                if re.search(r"(?:都|也|明显|相对)?\s*更大", clause) and any(
                    margin(candidates[number]) <= margin(candidates[selected_number])
                    for number in clause_numbers
                ):
                    return False
                if re.search(r"(?:都|也|明显|相对)?\s*更小", clause) and any(
                    margin(candidates[number]) >= margin(candidates[selected_number])
                    for number in clause_numbers
                ):
                    return False
                if re.search(r"(?:相同|一样|持平|同为)", clause) and any(
                    margin(candidates[number]) != margin(candidates[selected_number])
                    for number in clause_numbers
                ):
                    return False
            for left, right, relation in re.findall(
                r"G\s*([1-7])\s*(?:的)?分差\s*比\s*G\s*([1-7])"
                r"(?:的)?分差\s*(更大|更小|相同|一样)",
                clause,
                re.IGNORECASE,
            ):
                left_margin = margin(candidates[int(left)])
                right_margin = margin(candidates[int(right)])
                if (
                    (relation == "更大" and left_margin <= right_margin)
                    or (relation == "更小" and left_margin >= right_margin)
                    or (relation in {"相同", "一样"} and left_margin != right_margin)
                ):
                    return False
        return True

    @staticmethod
    def _series_recommendation_context_valid(
        question: str,
        answer: str,
        observations: list[dict[str, Any]],
    ) -> bool:
        """Prevent a premise challenge from silently changing the last choice.

        If the user asks “为什么不是 G2” immediately after the application
        published G2, the premise is false: the answer should correct it, not
        use a new but internally consistent G4 recommendation. The active
        recommendation is server-owned query scope, never inferred from model
        prose in the current turn.
        """

        challenged = re.search(
            r"为什么\s*(?:并)?不是\s*G\s*([1-7])",
            str(question or ""),
            re.IGNORECASE,
        )
        if challenged is None:
            return True
        active_number: int | None = None
        for observation in reversed(observations):
            if not isinstance(observation, Mapping):
                continue
            if str(observation.get("coverage", "")).lower() != "series_candidates_ready":
                continue
            scope = observation.get("query_scope")
            if not isinstance(scope, Mapping):
                continue
            try:
                raw_active = scope.get("active_game_number")
                active_number = int(raw_active) if raw_active is not None else None
            except (TypeError, ValueError):
                active_number = None
            break
        if active_number is None or active_number != int(challenged.group(1)):
            return True

        selected = ChatUseCase._recommended_game_number_from_answer(answer)
        acknowledges_existing_choice = bool(
            re.search(
                r"(?:刚才|之前|上一轮|上轮|已经|本来|原本|其实)"
                r"[^。！？!?；;]{0,24}(?:推荐|选|选择)|"
                r"(?:推荐|选|选择)(?:的)?[^。！？!?；;]{0,12}"
                r"(?:就是|正是|过)\s*\**\s*G\s*[1-7]",
                str(answer or ""),
                re.IGNORECASE,
            )
        )
        return selected == active_number and acknowledges_existing_choice

    def _agent_context_intent(
        self,
        message: str,
        context: Any,
        *,
        answer: str = "",
        series_candidates: Mapping[int, Game] | None = None,
        observations: list[dict[str, Any]] | None = None,
    ) -> QueryIntent:
        """Extract only conversational scope from an accepted Agent turn.

        Full-intelligence turns intentionally return before the normal parser
        and provider pipeline.  Without a small scope projection here, an
        explicit first-turn reference such as ``总决赛 G4`` is absent from the
        session context, so a subsequent pronoun (“这场比赛…”) is forced to
        clarify despite the Agent having just answered that game.  Parsing is
        used solely to persist typed entities; no provider call or fact is
        inferred from the result, and any parser failure keeps the safe generic
        summary intent.
        """

        if _is_zero_tool_question(message):
            return self._agent_summary_intent()
        try:
            timezone_name = str(getattr(context, "timezone", "Asia/Shanghai"))
            parsed = IntentParser(
                clock=self.clock,
                input_timezone=timezone_name,
                include_fixture_games=self._fixture_game_aliases_enabled,
            ).parse(message, context)
        except (TypeError, ValueError):
            return self._agent_summary_intent()
        observed_game = self._agent_resolved_observation_game(observations or [])
        if observed_game is not None:
            requested_team_ids = {
                item.canonical_id
                for item in parsed.intent.entities
                if item.kind is EntityKind.TEAM
            }
            observed_team_ids = {
                observed_game.away.canonical_id,
                observed_game.home.canonical_id,
            }
            requested_game_ids = {
                item.canonical_id
                for item in parsed.intent.entities
                if item.kind is EntityKind.GAME
            }
            if (
                requested_team_ids
                and not requested_team_ids.issubset(observed_team_ids)
            ) or (
                requested_game_ids
                and observed_game.game_id not in requested_game_ids
            ):
                # A wrong-game observation cannot mutate session identity,
                # even when its prose passed a looser analytical guard.
                observed_game = None
        if observed_game is not None:
            entities = [
                self._selected_game_ref(observed_game.game_id, observed_game),
                observed_game.away,
                observed_game.home,
                *parsed.intent.entities,
            ]
            deduplicated: list[EntityRef] = []
            seen: set[tuple[EntityKind, str]] = set()
            for entity in entities:
                key = (entity.kind, entity.canonical_id)
                if key in seen:
                    continue
                seen.add(key)
                deduplicated.append(entity)
            return parsed.intent.model_copy(
                update={
                    "entities": deduplicated,
                    "season": observed_game.season,
                    "game_number": observed_game.series_game_number,
                    "matchup": True,
                }
            )
        # Keep the parser's typed entities/season only.  A missing slot is
        # expected for an unresolved question and must not block recording a
        # concrete team/player/game that was explicitly mentioned.
        if is_positive_series_selection_question(message):
            recommended = self._trusted_recommended_game(answer, series_candidates)
            if recommended is not None:
                # The choice is safe only because it resolved against this
                # request's typed public series candidates.  Retain the full
                # canonical game server-side (not just its EntityRef) so a
                # later PBP-only payload can restore team names for score
                # direction without trusting model prose or a client id.
                try:
                    self.game_registry[recommended.game_id] = recommended
                    self.game_origin_registry[recommended.game_id] = "public"
                except (AttributeError, TypeError):
                    # Read-only registries are supported for embedders; the
                    # conversation still keeps its id-only active reference.
                    pass
                game_ref = self._selected_game_ref(recommended.game_id, recommended)
                entities: list[EntityRef] = [
                    game_ref,
                    recommended.away,
                    recommended.home,
                    *parsed.intent.entities,
                ]
                deduplicated: list[EntityRef] = []
                seen: set[tuple[EntityKind, str]] = set()
                for entity in entities:
                    key = (entity.kind, entity.canonical_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    deduplicated.append(entity)
                return parsed.intent.model_copy(
                    update={
                        "entities": deduplicated,
                        "season": recommended.season,
                        "game_number": recommended.series_game_number,
                        "matchup": True,
                    }
                )
        if parsed.intent.entities or parsed.intent.season is not None:
            return parsed.intent
        return self._agent_summary_intent()

    def _agent_resolved_observation_game(
        self,
        observations: list[dict[str, Any]],
    ) -> Game | None:
        """Resolve exactly one private canonical game from typed observations."""

        resolved_ids: set[str] = set()
        for item in observations:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("status") or "").lower() != "completed":
                continue
            if str(item.get("intent") or "").lower() not in {
                "nba_query",
                "public_reverification",
            }:
                continue
            candidate = item.get("_resolved_game_id")
            if not isinstance(candidate, str):
                scope = item.get("query_scope")
                candidate = scope.get("game_id") if isinstance(scope, Mapping) else None
            if isinstance(candidate, str) and re.fullmatch(
                r"[A-Za-z0-9._:-]{1,128}", candidate
            ):
                resolved_ids.add(candidate)
        if len(resolved_ids) != 1:
            return None
        return self._selected_game(next(iter(resolved_ids)))

    @staticmethod
    def _agent_has_series_candidate_observation(result: AgentTurnResult) -> bool:
        """Return whether this turn already performed the trusted series lookup."""

        return any(
            isinstance(item, Mapping)
            and str(item.get("coverage", "")).lower() == "series_candidates_ready"
            for item in list(result.observations or [])
        )

    @staticmethod
    def _agent_public_notices(result: AgentTurnResult) -> list[dict[str, Any]]:
        """Project typed runtime/search failures into provider-neutral notices."""

        notices: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(code: str, message: str, retryable: bool) -> None:
            if code not in seen:
                seen.add(code)
                notices.append(
                    {"code": code, "message": message, "retryable": retryable}
                )

        finish = str(result.finish_reason or "").casefold()
        runtime_code = str(
            getattr(result.error_code, "value", result.error_code) or ""
        ).upper()
        if result.status is not RuntimeStatus.OK:
            if "quota_exhausted" in finish or "billing" in finish:
                add(
                    "INTELLIGENCE_QUOTA_EXHAUSTED",
                    "智能回答额度已用完，当前无法完成智能分析。",
                    False,
                )
            elif runtime_code in {"UPSTREAM_AUTH", "INTELLIGENCE_AUTH_UNAVAILABLE"}:
                add(
                    "INTELLIGENCE_AUTH_UNAVAILABLE",
                    "智能回答服务当前不可用，暂时无法完成智能分析。",
                    False,
                )
            elif runtime_code or finish:
                add(
                    "INTELLIGENCE_TEMPORARILY_UNAVAILABLE",
                    "智能回答服务暂时不可用，已尝试使用现有比赛数据继续回答。",
                    bool(result.retryable),
                )

        for call in list(result.tool_calls or []):
            # ``nba_query`` can perform a nested public lookup.  Its usable
            # typed answer remains completed while the real tool bridge keeps
            # a request-local search capability issue on this call.
            if call.tool_name not in {"nba_query", "nba_search", "nba_news"}:
                continue
            # A preferred search can exhaust its quota while a bounded
            # fallback still returns useful data.  In that case the tool call
            # is correctly ``completed`` but keeps an internal error code so
            # the browser can disclose the reduced capability for this
            # request.  Do not limit notices to failed calls.
            if not call.error_code:
                continue
            code = str(call.error_code).upper()
            if "QUOTA" in code or (
                code == "UPSTREAM_RATE_LIMITED" and not call.retryable
            ):
                add(
                    "SEARCH_QUOTA_EXHAUSTED",
                    "在线检索额度已用完，当前无法补充公开资料。",
                    False,
                )
            elif code == "UPSTREAM_AUTH":
                add(
                    "SEARCH_AUTH_UNAVAILABLE",
                    "在线检索服务当前不可用，暂时无法补充公开资料。",
                    False,
                )
            elif code in {
                "UPSTREAM_TIMEOUT",
                "UPSTREAM_RATE_LIMITED",
                "COMPOSER_UNAVAILABLE",
            }:
                add(
                    "SEARCH_TEMPORARILY_UNAVAILABLE",
                    "在线检索服务暂时不可用，已尝试使用现有比赛数据继续回答。",
                    bool(call.retryable),
                )
        return notices

    @staticmethod
    def _provider_public_notices(
        result: ProviderResult[Any],
    ) -> list[dict[str, Any]]:
        """Turn request-local provider capability failures into safe notices."""

        issues = list(result.capability_issues or [])
        if result.error is not None:
            issues.append(result.error)
        notices: list[dict[str, Any]] = []
        seen: set[str] = set()
        for issue in issues[:8]:
            kind = getattr(issue, "kind", None)
            retryable = bool(getattr(issue, "retryable", False))
            if kind is ProviderErrorKind.QUOTA_EXHAUSTED:
                code = "SEARCH_QUOTA_EXHAUSTED"
                message = "在线检索额度已用完，当前无法补充公开资料。"
                retryable = False
            elif kind is ProviderErrorKind.AUTH:
                code = "SEARCH_AUTH_UNAVAILABLE"
                message = "在线检索服务当前不可用，暂时无法补充公开资料。"
                retryable = False
            elif kind in {
                ProviderErrorKind.TIMEOUT,
                ProviderErrorKind.RATE_LIMITED,
                ProviderErrorKind.HTTP,
            }:
                code = "SEARCH_TEMPORARILY_UNAVAILABLE"
                message = "在线检索服务暂时不可用，已尝试使用现有比赛数据继续回答。"
            else:
                continue
            if code in seen:
                continue
            seen.add(code)
            notices.append(
                {"code": code, "message": message, "retryable": retryable}
            )
        return notices

    @staticmethod
    def _merge_public_notices(
        *groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in groups:
            for item in group:
                code = str(item.get("code") or "")
                if not code or code in seen:
                    continue
                seen.add(code)
                merged.append(dict(item))
                if len(merged) >= 8:
                    return merged
        return merged

    @staticmethod
    def _agent_has_usable_observation(result: AgentTurnResult) -> bool:
        for observation in list(result.observations or []):
            if not isinstance(observation, Mapping):
                continue
            if str(observation.get("status") or "").lower() not in {
                "completed",
                "no_data",
            }:
                continue
            if str(observation.get("answer_markdown") or "").strip() or observation.get(
                "blocks"
            ):
                return True
        return False

    @staticmethod
    def _agent_failure_error(
        result: AgentTurnResult,
        notices: list[dict[str, Any]],
    ) -> tuple[str, str, bool]:
        codes = {str(item.get("code") or "") for item in notices}
        if codes & {
            "INTELLIGENCE_QUOTA_EXHAUSTED",
            "INTELLIGENCE_AUTH_UNAVAILABLE",
        }:
            return (
                "COMPOSER_UNAVAILABLE",
                "智能回答服务当前不可用，暂时无法完成本次回答。",
                False,
            )
        if "SEARCH_QUOTA_EXHAUSTED" in codes:
            return (
                "UPSTREAM_RATE_LIMITED",
                "在线检索服务当前不可用，暂时无法完成本次回答。",
                False,
            )
        if "SEARCH_AUTH_UNAVAILABLE" in codes:
            return (
                "UPSTREAM_AUTH",
                "在线检索服务当前不可用，暂时无法完成本次回答。",
                False,
            )
        retryable = any(bool(item.get("retryable")) for item in notices)
        runtime_code = str(
            getattr(result.error_code, "value", result.error_code) or ""
        ).upper()
        code = (
            "UPSTREAM_TIMEOUT"
            if runtime_code == "UPSTREAM_TIMEOUT"
            else "COMPOSER_UNAVAILABLE"
        )
        return code, "服务暂时不可用，请稍后重试。", retryable

    async def _augment_series_recommendation_turn(
        self,
        question: str,
        result: AgentTurnResult,
        *,
        context: Any,
        selected_game_id: str | None,
        budget: RequestBudget,
        token: CancelToken,
        sink: Any,
        series_candidates: dict[int, Game],
    ) -> AgentTurnResult:
        """Ground a no-tool recommendation in one controlled series lookup.

        This does not run a second model turn or replace a valid synthesis.  It
        appends one server-owned observation so the existing relevance/output
        guard can validate the model prose, while the same candidate map gives
        the scoped recovery an exact, deterministic fallback when the prose is
        generic, candidate-only, or factually inconsistent.
        """

        observation = dict(
            await self._agent_series_selection_observation(
                question,
                context=context,
                selected_game_id=selected_game_id,
                budget=budget,
                token=token,
                candidate_sink=series_candidates,
            )
        )
        observations = [*list(result.observations or []), observation]
        calls = [*list(result.tool_calls or [])]
        calls.append(
            AgentToolCall(
                "nba_query",
                "controlled_series_recommendation",
                str(observation.get("status") or "failed"),
                0,
                str(observation.get("evidence_state") or "none"),
            )
        )
        return result.model_copy(
            update={
                "observations": observations[-8:],
                "tool_calls": calls[-8:],
                "evidence_state": self._agent_evidence_state(
                    observations,
                    result.evidence_state,
                ).value.lower(),
            }
        )

    @staticmethod
    def _session_meta_summary_intent(context: Any) -> QueryIntent:
        """Represent a session-state turn without inventing an NBA lookup."""

        entities = [
            item
            for item in (
                getattr(context, "active_game", None),
                getattr(context, "active_team", None),
                getattr(context, "active_player", None),
            )
            if item is not None
        ]
        return QueryIntent(
            category=Category.H,
            intent_name=IntentName.FOLLOW_UP,
            mode=QueryMode.OBJECTIVE,
            confidence=1,
            entities=entities,
            operation=Operation.EXPLAIN,
        )

    async def _commit_context_turn(
        self,
        context: Any,
        *,
        intent: QueryIntent,
        answer: str,
        user_message: str,
        facts: FactBundle | None = None,
    ) -> None:
        """Commit one safety-allowed conversational outcome.

        Context persistence must not invalidate an otherwise safe answer.  The
        optimistic store still retries conflicts inside ``ContextManager``;
        after that bounded retry the next request simply starts from the last
        committed state.
        """

        try:
            await self.context_manager.commit(
                context,
                intent=intent,
                facts=facts,
                answer=answer,
                user_message=user_message,
            )
        except Exception:
            pass

    @staticmethod
    def _agent_as_of(observations: list[dict[str, Any]]) -> str | None:
        values = [
            str(item.get("as_of_beijing"))
            for item in observations
            if item.get("as_of_beijing")
        ]
        return max(values) if values else None

    @staticmethod
    def _agent_data_origin(observations: list[dict[str, Any]]) -> str:
        values = {
            str(item.get("data_origin") or "none")
            for item in observations
            if isinstance(item, Mapping)
        }
        values.discard("none")
        if not values:
            return "none"
        if values == {"demo_snapshot"}:
            return "demo_snapshot"
        if values == {"public"}:
            return "public"
        return "mixed"

    @staticmethod
    def _agent_evidence_state(
        observations: list[dict[str, Any]],
        fallback: Any = "none",
    ) -> EvidenceState:
        """Derive the public evidence label from server-owned observations."""

        states = {
            str(item.get("evidence_state") or "none").strip().lower()
            for item in observations
            if isinstance(item, Mapping)
        }
        meaningful = states - {"none"}
        if "partial" in meaningful or (meaningful and "none" in states):
            return EvidenceState.PARTIAL
        if meaningful == {"verified"}:
            return EvidenceState.VERIFIED
        fallback_value = str(getattr(fallback, "value", fallback)).strip().upper()
        if fallback_value in {"VERIFIED", "PARTIAL", "NONE"}:
            return EvidenceState(fallback_value)
        return EvidenceState.NONE

    @staticmethod
    def _data_origin(evidence: Any) -> str:
        classes = {
            str(
                getattr(
                    getattr(item, "source_class", None),
                    "value",
                    getattr(item, "source_class", ""),
                )
            ).upper()
            for item in list(evidence or [])
        }
        classes.discard("")
        if not classes:
            return "none"
        if classes == {"FIXTURE"}:
            return "demo_snapshot"
        if "FIXTURE" in classes:
            return "mixed"
        return "public"

    @staticmethod
    def _agent_result_relevant(
        question: str,
        result: AgentTurnResult,
        *,
        expected_game_id: str | None = None,
    ) -> bool:
        """Reject a successful Agent turn whose observations answer another task.

        Hermes owns intent understanding, but a successful-looking answer is
        still incorrect when every observation came from an unrelated tool
        (for example, a schedule ``no_data`` result for a player-stat or
        tactical question).  This guard is intentionally narrow: it only
        rejects tool-only mismatches and leaves wording/quality to the model
        and the output guard.
        """

        text = str(question or "")
        observations = [
            item
            for item in result.observations
            if isinstance(item, Mapping)
            and str(item.get("status", "")).lower()
            in {"completed", "no_data", "needs_clarification"}
        ]
        if not observations:
            # OutputGuard will reject fact turns without observations.  Keep
            # this helper permissive for zero-tool greetings/capability turns.
            return _is_zero_tool_question(text)

        tool_names = {
            str(getattr(call, "tool_name", "")).lower()
            for call in result.tool_calls
            if str(getattr(call, "status", "")).lower()
            not in {"duplicate", "failed", "cancelled"}
        }
        observed_intents = {
            str(item.get("intent", "")).lower() for item in observations
        }
        answers = "\n".join(str(item.get("answer_markdown") or "") for item in observations)
        if (
            ChatUseCase._is_open_ended_agent_synthesis(text)
            and re.search(
                r"(?:请|需要).{0,20}(?:补充|指定)"
                r"(?:查询对象|对象|球队|球员|比赛|场次)",
                str(result.answer_markdown or ""),
            )
        ):
            # Recommendation/comparison prompts are complete questions.  A
            # generic clarification here is a routing failure, not a valid
            # Agent synthesis, and must never be packaged as ``completed``.
            return False
        answer_text = str(result.answer_markdown or "").strip()
        answer_is_candidate = (
            result.status is RuntimeStatus.OK and bool(answer_text)
        )
        if answer_is_candidate and is_positive_series_selection_question(text):
            # A candidate list is grounding, not a recommendation.  Require a
            # concrete choice plus an explicit evaluation criterion before a
            # successful envelope can be shown as an Agent answer.
            chose_game = (
                ChatUseCase._recommended_game_number_from_answer(answer_text)
                is not None
            )
            criterion = bool(
                re.search(
                    r"(?:按|标准|胶着|分差|险胜|悬念|转折|关键|观赏|系列赛进程|重要性)",
                    answer_text,
                )
            )
            if not (chose_game and criterion):
                return False
        if answer_is_candidate and is_inverse_series_selection_question(text):
            # An inverse selection is just as complete as “哪场最精彩”, but a
            # bare candidate list is still not an answer.  Validate the model's
            # negative choice without invoking the positive best-game repair.
            chose_game = bool(
                re.search(
                    r"(?:最不(?:推荐|精彩|好看|值得(?:看|回看))|"
                    r"不推荐|最没(?:看点|悬念))[^。！？\n]{0,36}"
                    r"(?:G\s*[1-7]|第\s*[一二三四五六七1-7]\s*场)|"
                    r"(?:G\s*[1-7]|第\s*[一二三四五六七1-7]\s*场)"
                    r"[^。！？\n]{0,24}(?:最不(?:推荐|精彩|好看|值得)|不推荐)",
                    answer_text,
                    re.IGNORECASE,
                )
            )
            criterion = bool(
                re.search(
                    r"(?:按|标准|分差|悬念|胶着|看点|转折|关键|观赏|"
                    r"系列赛进程|重要性)",
                    answer_text,
                )
            )
            if not (chose_game and criterion):
                return False
        if answer_is_candidate and is_subjective_comparison_question(text):
            players = [
                item
                for item in resolve_entities(text)
                if item.kind is EntityKind.PLAYER
            ]
            both_players = len(players) >= 2 and all(
                any(
                    str(alias).casefold() in answer_text.casefold()
                    for alias in (player.display_name, *player.aliases)
                    if str(alias).strip()
                )
                for player in players[:2]
            )
            dimensions = sum(
                bool(re.search(pattern, answer_text))
                for pattern in (
                    r"(?:荣誉|冠军|团队成绩)",
                    r"(?:峰值|巅峰|得分|攻防)",
                    r"(?:生涯|长度|稳定|耐久)",
                    r"(?:时代|角色|组织|适应)",
                )
            )
            qualified = bool(
                re.search(r"(?:没有唯一|并无唯一|取决于|口径|标准不同)", answer_text)
            )
            explicit_standard = bool(
                re.search(
                    r"(?:只|仅)(?:按|看|比较|考虑)|"
                    r"(?:如果|若)(?:只|仅)?(?:按|看|考虑)|"
                    r"(?:评价|比较|判断)(?:的)?标准(?:是|为|按)",
                    text,
                )
            )
            # When the user explicitly supplies a single evaluation standard,
            # forcing a generic three-dimension essay plus “no unique answer”
            # contradicts that request.  Still require both players and at
            # least one objective comparison dimension; the observation/output
            # guards remain responsible for factual provenance.
            comparison_complete = (
                both_players and dimensions >= 1
                if explicit_standard
                else both_players and dimensions >= 3 and qualified
            )
            if not comparison_complete:
                return False
        tactical_or_recap = is_game_recap_question(text) or bool(
            re.search(
                r"(?:战术|挡拆|防守策略|怎么限制|如何限制|如何防|怎么防|为什么能|为何能)",
                text,
            )
        )
        if answer_is_candidate and tactical_or_recap:
            if re.search(
                r"(?:资料不足|只有终场结果|缺少.{0,18}(?:回合|过程|战术|资料)|"
                r"不能据此|无法判断|请补充)",
                answer_text,
            ):
                return False
            if not re.search(
                r"(?:因为|靠|通过|关键(?:在于|是)|核心(?:在于|是)|"
                r"优势|防守|进攻|挡拆|轮转|执行|篮板|失误|命中率|节奏|对位)",
                answer_text,
            ):
                return False
        # Schedule observations are authoritative only for schedule wording.
        # A tool-call list may be unavailable in a test double, so inspect the
        # sanitized observation intent as a second signal.
        schedule_question = bool(
            re.search(
                r"(?:赛程|赛果|今天|明天|后天|昨天|本周|下周|未来\s*\d+\s*天|"
                r"接下来\s*\d+\s*天|哪天有比赛|有哪些比赛|有比赛吗)",
                text,
                re.IGNORECASE,
            )
        )
        schedule_only = (
            bool(observations)
            and observed_intents <= {"schedule_result"}
        )
        if schedule_only and not schedule_question:
            return False

        # News observations should not satisfy a numeric/PBP/tactical request
        # unless the answer itself clearly carries the requested analysis.
        news_question = bool(
            re.search(r"(?:新闻|消息|资讯|报道|动态|近况|背景|\bnews\b|\bheadline\b)", text, re.I)
        )
        news_only = (
            bool(observations)
            and observed_intents <= {"nba_news"}
        )
        if news_only and not news_question:
            # A news result can still be useful for an explicitly analytical
            # question when the observation itself contains tactical context.
            # Reject only a bare news dump; this avoids turning a reasonable
            # background answer into a full deterministic fallback.
            tactical_request = bool(
                re.search(r"(?:战术|挡拆|防守|轮转|复盘|关键转折|为什么能|表现如何|评价)", text)
            )
            if not (
                tactical_request
                and re.search(r"(?:战术|挡拆|防守|轮转|复盘|转折|执行|对位|篮板)", answers)
            ):
                return False

        # The broad web-search tool is intentionally available for long-tail
        # and tactical context. It is a valid observation for those questions,
        # but it must not replace the typed NBA query for hard numeric facts.
        web_only = (
            bool(observations)
            and "nba_query" not in observed_intents
            and (
                tool_names
                and tool_names <= {"nba_search"}
                or not tool_names
                and observed_intents <= {"web_search"}
            )
        )
        hard_fact_question = bool(
            re.search(
                r"(?:比分|得分|篮板|助攻|排名|赛程|赛果|谁赢|胜负|最后\s*[0-9一二三四五六七八九十]+\s*秒|"
                r"逐回合|出手者|球馆|场馆|教练|冠军|夺冠|"
                r"赢了吗|怎么赢|如何赢|取胜|获胜)",
                text,
            )
        )
        pbp_question = bool(
            re.search(
                r"(?:关键回合|逐回合|最后\s*[0-9一二三四五六七八九十]+\s*秒|"
                r"最后一攻|最后那个球|刚才那个球|最后一球|最后一投|"
                r"最后谁投|回放|出手者|谁助攻)",
                text,
            )
        )
        metadata_question = bool(
            re.search(
                r"(?:场馆|球馆|球场|举办地|比赛地点|"
                r"在哪(?:儿|里)?(?:举办|进行|打)|"
                r"(?:双方|主客队|球队)?(?:主教练|教练|主帅)|"
                r"比赛时长|实际耗时|打了多久|持续多久|"
                r"观众人数|上座人数|谁打谁|对阵双方)",
                text,
            )
        )
        game_metric_question = bool(
            re.search(r"(?:这场|本场|那场|该场|比赛|G\s*[1-7]|对|vs)", text, re.I)
            and re.search(
                r"(?:比分|谁赢|胜负|得分(?:最高|最多|第\s*[一二三四五六七八九十\d]+)|"
                r"篮板(?:最高|最多)|助攻(?:最高|最多)|三分(?:最高|最多))",
                text,
            )
        )
        game_hard_fact_question = bool(
            not schedule_question
            and (pbp_question or metadata_question or game_metric_question)
        )
        completed_typed = [
            item
            for item in observations
            if str(item.get("status") or "").lower() == "completed"
            and str(item.get("intent") or "").lower()
            in {"nba_query", "public_reverification"}
        ]
        if game_hard_fact_question:
            # A typed miss plus a search hit is still a miss for objective
            # game facts.  Search summaries may add background, but cannot
            # establish a score, player line, venue, coach, duration or play.
            if not completed_typed:
                return False
            resolved_ids: set[str] = set()
            for item in completed_typed:
                candidate = item.get("_resolved_game_id")
                if not isinstance(candidate, str):
                    scope = item.get("query_scope")
                    candidate = (
                        scope.get("game_id") if isinstance(scope, Mapping) else None
                    )
                if isinstance(candidate, str) and re.fullmatch(
                    r"[A-Za-z0-9._:-]{1,128}", candidate
                ):
                    resolved_ids.add(candidate)
            if expected_game_id is not None:
                if resolved_ids != {expected_game_id}:
                    return False
            elif len(resolved_ids) != 1:
                return False
            if pbp_question:
                typed_answers = "\n".join(
                    str(item.get("answer_markdown") or "")
                    for item in completed_typed
                )
                event_or_honest_gap = bool(
                    re.search(
                        r"(?:第\s*[1-9]\s*节|Q[1-9]|还剩|\d+\s*秒|"
                        r"投篮|出手|罚球|助攻|回合|逐回合|回放|"
                        r"暂无.{0,12}(?:回合|出手|投篮)|"
                        r"未(?:标注|提供|包含).{0,12}(?:回合|出手|投篮)|"
                        r"无法(?:确认|核验|还原).{0,12}(?:回合|出手|投篮))",
                        typed_answers,
                        re.I,
                    )
                )
                if not event_or_honest_gap:
                    return False
        # Search is valid evidence for an open recap/background synthesis, but
        # it cannot be the sole authority for score, winner, box-score, venue,
        # coach or play-by-play facts.  The Agent may still use search after a
        # typed miss; in that case the typed observation remains visible and
        # the answer is necessarily partial.
        if (
            web_only
            and hard_fact_question
            and not is_subjective_comparison_question(text)
        ):
            return False

        # Event-level questions require an observation that actually mentions
        # event/PBP facts.  This prevents an empty schedule from being painted
        # as the answer to “最近一场关键回合”。
        # ``nba_query`` is the typed source for event facts, including an
        # honest "暂无逐回合记录" response.  Requiring a keyword in the
        # rendered text made a valid no-data observation look like a tool
        # mismatch and sent the whole turn through the clarification template.
        # Only reject a PBP turn when the Agent never used the event-capable
        # query (for example, it used schedule/news exclusively).
        if (
            pbp_question
            and "nba_query" not in tool_names
            and "nba_query" not in observed_intents
            and not re.search(r"(?:回合|逐回合|最后一攻|出手|投篮|罚球|回放)", answers)
        ):
            return False

        # Tactical/recap and player/history questions must not accept a
        # schedule-only observation (handled above), and an apparently empty
        # non-schedule answer without a domain marker is also suspicious when
        # the Agent never used the general NBA query tool.
        analytical_question = ChatUseCase._is_open_ended_agent_synthesis(text) or bool(
            re.search(r"(?:战术|挡拆|防守策略|为什么能|怎么限制|复盘|关键转折|表现如何|评价)", text)
        )
        # A generic ``nba_query`` bridge can still yield a schedule-shaped
        # answer when the model supplied a date expression for an open recap
        # question.  Tool-name checks alone cannot catch that mismatch because
        # the generic bridge deliberately records the tool as ``nba_query``.
        # Reject the unmistakable schedule projection so the deterministic
        # recap path (or a subsequent web-search iteration) can recover.
        schedule_shaped_answer = bool(
            re.search(r"(?:NBA\s*赛程|北京时间\s*[^\n]{0,40}的\s*NBA\s*赛程)", answers)
            and not re.search(r"(?:过程|回合|战术|复盘|比分|得分|走势|关键)", answers)
        )
        if analytical_question and schedule_shaped_answer:
            return False
        if (
            analytical_question
            and "nba_query" not in observed_intents
            and tool_names
            and tool_names <= {"nba_schedule", "nba_news"}
        ):
            if not re.search(r"(?:战术|挡拆|防守|轮转|复盘|转折|执行|对位|篮板)", answers):
                return False

        return True

    @staticmethod
    def _short_public_text(value: Any, *, limit: int) -> str:
        """Bound an untrusted search snippet at a sentence boundary.

        Search providers frequently return the first paragraph of an article
        (sometimes several thousand characters).  Passing that paragraph
        through unchanged makes a recovery answer look like a copied article
        and can cut off in the middle of a fact.  Keep at most two complete
        sentences and add an ellipsis only when the provider text continues.
        """

        text = neutralize_external_internal_names(" ".join(str(value or "").split()))
        if not text:
            return ""
        if len(text) <= limit:
            return text
        sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])", text) if part.strip()]
        kept = ""
        for sentence in sentences[:2]:
            candidate = f"{kept}{sentence}" if kept else sentence
            if len(candidate) > limit:
                break
            kept = candidate
        if kept:
            return kept.rstrip("，,；;：: ") + "…"
        # A provider is allowed to return text without sentence punctuation.
        # Do not split a surrogate pair or leave a dangling markdown marker.
        return text[: max(1, limit - 1)].rstrip("，,；;：: ") + "…"

    @staticmethod
    def _compact_news_blocks(
        blocks: Any,
        *,
        max_items: int = 3,
        title_limit: int = 120,
        summary_limit: int = 240,
    ) -> str:
        """Render news blocks as a short, deduplicated evidence list.

        ``NewsItem.summary`` is background evidence, not an article body.  A
        compact projection keeps the model context and the user-facing
        recovery answer bounded while retaining enough context to explain why
        a result is only partially verified.
        """

        items: list[tuple[str, str]] = []
        title: str | None = None
        for block in list(blocks or []):
            label = str(
                block.get("label", "")
                if isinstance(block, Mapping)
                else getattr(block, "label", "")
            ).strip()
            if label == "新闻标题":
                raw = (
                    block.get("value", "")
                    if isinstance(block, Mapping)
                    else getattr(block, "value", "")
                )
                title = ChatUseCase._short_public_text(raw, limit=title_limit)
            elif label == "新闻摘要":
                raw = (
                    block.get("content", "")
                    if isinstance(block, Mapping)
                    else getattr(block, "content", "")
                )
                summary = ChatUseCase._short_public_text(raw, limit=summary_limit)
                if title and summary:
                    key = re.sub(r"\s+", "", title).casefold()
                    if not any(re.sub(r"\s+", "", old).casefold() == key for old, _ in items):
                        items.append((title, summary))
                    title = None
            if len(items) >= max_items:
                break
        if not items:
            return ""
        lines = ["公开资料线索（待交叉核验）："]
        lines.extend(f"- **{item_title}**：{summary}" for item_title, summary in items)
        return "\n".join(lines)

    @staticmethod
    def _compact_search_markdown(
        value: Any,
        *,
        max_items: int = 3,
        item_limit: int = 360,
    ) -> str:
        """Compact an already-rendered search answer without copying articles."""

        raw = str(value or "").strip()
        if not raw:
            return ""
        # Prefer explicit markdown bullets produced by the search adapters.
        bullets: list[str] = []
        lead: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if re.match(r"^(?:[-*]|\d+[.)])\s+", line):
                bullets.append(re.sub(r"^(?:[-*]|\d+[.)])\s+", "", line))
            elif not bullets and not re.search(r"公开资料(?:检索到|新闻摘要|线索)", line):
                lead.append(line)
        output: list[str] = []
        if lead:
            output.append(ChatUseCase._short_public_text(" ".join(lead), limit=320))
        seen: set[str] = set()
        for bullet in bullets:
            cleaned = ChatUseCase._short_public_text(bullet, limit=item_limit)
            key = re.sub(r"\s+", "", cleaned).casefold()
            if cleaned and key not in seen:
                seen.add(key)
                output.append(f"- {cleaned}")
            if len(seen) >= max_items:
                break
        if not output:
            output.append(ChatUseCase._short_public_text(raw, limit=720))
        return "公开资料线索（待交叉核验）：\n" + "\n".join(output)

    @staticmethod
    def _strip_public_search_sections(value: Any) -> str:
        """Remove provider-shaped search appendices at the API boundary.

        Search observations are deliberately verbose because they are useful to
        Hermes while it is reasoning.  They are not, however, a good public
        answer: a ``补充线索`` heading followed by copied snippets makes the
        assistant look like a search-results proxy and can expose stale or
        unrelated context.  Keep the model's lead/conclusion and replace the
        implementation-shaped appendix with one short evidence boundary.

        This projection is intentionally applied only to the public answer.  The
        server still retains the complete observation for grounding, auditing,
        and future turns; internal helper tests may therefore continue to use
        the bounded observation format.  It must not add a second, workflow-
        shaped disclaimer: evidence state is carried in response metadata and
        rendered by the UI, while the answer body remains a direct response.
        """

        text = str(value or "").strip()
        if not text:
            return ""

        search_label = (
            r"(?:"
            r"公开资料(?:新闻)?(?:线索|摘要|结果)"
            r"|公开资料检索到以下(?:相关)?(?:线索|摘要|结果)"
            r"|公开网页(?:线索|摘要|结果)"
            r"|网页(?:搜索|检索)(?:结果|摘要|线索)"
            r"|搜索(?:结果|摘要|线索)"
            r"|检索(?:结果|摘要|线索)"
            r"|补充(?:线索|资料)"
            r"|相关报道(?:还)?提到"
            r"|公开报道(?:线索|摘要)"
            r")"
        )
        section_marker = re.compile(
            rf"^{search_label}(?:\s*[（(][^）)]{{0,80}}[）)])?"
            r"(?:\s*[:：].*)?$",
            re.IGNORECASE,
        )
        inline_section_marker = re.compile(
            rf"(?<=[。！？!?；;，,])\s*(?:\*\*|__|`)*{search_label}"
            r"(?:\s*[（(][^）)]{0,80}[）)])?(?:\*\*|__|`)*"
            r"\s*[:：].*$",
            re.IGNORECASE,
        )
        caveat_marker = re.compile(
            r"(?:这些内容目前按公开网页线索处理，不标记为已核验事实。?|"
            r"以上为公开网页线索，尚未与(?:官方|结构化)比赛记录交叉核验；?"
            r"(?:网页之间如有日期或比分不一致，以官方记录为准。?)?|"
            r"尚未与(?:官方|结构化)比赛记录交叉核验。?)",
            re.IGNORECASE,
        )
        inline_caveat_marker = re.compile(
            r"\s*[（(][^）)]{0,120}(?:交叉核验|待核验|不标记为已核验事实)[^）)]*[）)]\s*",
            re.IGNORECASE,
        )
        composed_boundary = re.compile(
            r"^(?:综合结论|最终(?:结论|回答)|结论|总结|"
            r"综合来看|总体来看|总的来说|综合判断|简要回答)"
            r"(?:\s*[:：].*|$)",
            re.IGNORECASE,
        )
        provenance_prefix = re.compile(
            r"^(?:(?:根据|据)(?:现有)?(?:公开)?"
            r"(?:搜索结果|检索结果|网页(?:搜索|检索)(?:结果)?|"
            r"公开(?:报道|资料|网页)(?:结果|信息)?)|"
            r"相关报道(?:还)?提到|补充资料(?:显示|表明))"
            r"\s*[，,:：]\s*",
            re.IGNORECASE,
        )
        internal_process_line = re.compile(
            r"^(?=[^\n]{0,360}(?:查询|检索|调用|命中|未命中|复用|读取|写入|"
            r"返回|未找到|没有找到|完成搜索|失败))"
            r"(?=[^\n]{0,360}(?:结构化(?:比赛)?(?:记录|数据)|缓存|数据库|索引|工具|搜索))"
            r"[^\n]{1,360}$",
            re.IGNORECASE,
        )
        forbidden_internal_line = re.compile(
            r"(?<![A-Za-z0-9_])(?:hermes|provider)(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        search_process_lead = re.compile(
            r"^(?:(?:我|我们|助手|系统|服务)\s*)?"
            r"(?:进行(?:了)?\s*)?(?:在线|网页|公开资料)?\s*"
            r"(?:搜索|检索|查询)(?:了)?[^，,:：。！？!?\n]{0,24}?"
            r"(?:后|之后)?(?:发现|显示|表明|得知|可见)\s*[，,:：]\s*",
            re.IGNORECASE,
        )
        structured_missing_line = re.compile(
            r"^(?:(?:我|我们|助手|系统|服务)\s*)?"
            r"(?:查询|检索|检查|读取)(?:了)?\s*(?:当前)?"
            r"结构化(?:比赛)?(?:记录|数据)\s*[，,]?\s*"
            r"(?:但|不过|然而)?\s*(?:没有|未能|未|缺少)\s*"
            r"(?:找到|提供|包含|返回)?\s*"
            r"(?P<detail>[^。！？!?；;\n]{1,120})[。！？!?]?$",
            re.IGNORECASE,
        )
        model_workflow_meta_line = re.compile(
            r"^(?:"
            r"(?=[^\n]{0,520}(?:搜索|检索|来源|核验))"
            r"(?=[^\n]{0,520}(?:我只使用|谨慎综合|存在不一致|说法不同|未经核验))"
            r"[^\n]{1,520}|"
            r"先给结论[^\n]{0,240}(?:明确标注|确认事实|过程细节)[^\n]{0,240}"
            r")$",
            re.IGNORECASE,
        )

        output: list[str] = []
        in_search_section = False
        saw_search_section = False

        def normalized_marker_line(line: str) -> str:
            """Normalize only outer Markdown decoration for marker matching."""

            normalized = re.sub(r"^(?:>\s*)+", "", line.strip())
            return re.sub(
                r"^(?:#{1,6}\s*|(?:\*\*|__|`)+)|"
                r"(?:(?:\*\*|__|`)+(?=\s*(?:[：:（(]|$)))",
                "",
                normalized,
            ).strip()

        for raw_line in text.splitlines():
            # Remove inline evidence qualifiers before matching section
            # headings.  Otherwise a sentence such as
            # ``分析（基于公开报道线索，待交叉核验）：`` is split at the
            # embedded marker and leaves the dangling ``分析（基于`` prefix.
            line = inline_caveat_marker.sub("", raw_line).strip()
            marker_line = normalized_marker_line(line)
            missing = structured_missing_line.fullmatch(marker_line)
            if missing:
                detail = missing.group("detail").strip(" ，,：:")
                detail = re.sub(r"^(?:可用的?|对应的?)\s*", "", detail)
                if re.fullmatch(
                    r"(?:直接|对应)?匹配(?:项|内容|记录|结果)?",
                    detail,
                    flags=re.IGNORECASE,
                ):
                    line = ""
                else:
                    line = f"目前缺少{detail}。"
            else:
                line = search_process_lead.sub("", line).strip()
            line = provenance_prefix.sub("", line).strip()
            marker_line = normalized_marker_line(line)
            if not line:
                # Blank lines inside a search appendix are discarded.  Outside
                # it, retain one separator only when there is content on both
                # sides so composed prose keeps its readability.
                if not in_search_section and output and output[-1] != "":
                    output.append("")
                continue

            if caveat_marker.search(marker_line):
                saw_search_section = True
                # A trailing evidence caveat is an explicit end marker.  Drop
                # it, then allow any following model-authored paragraph to be
                # considered independently.
                in_search_section = False
                continue

            marker = section_marker.fullmatch(marker_line)
            if marker:
                saw_search_section = True
                in_search_section = True
                continue

            inline_marker = inline_section_marker.search(marker_line)
            if inline_marker:
                saw_search_section = True
                in_search_section = True
                prefix = marker_line[: inline_marker.start()].rstrip(
                    " \t*_`~#>，,；;：:"
                )
                if prefix:
                    output.append(prefix)
                continue

            if forbidden_internal_line.search(marker_line):
                continue

            if model_workflow_meta_line.fullmatch(marker_line):
                continue

            if internal_process_line.fullmatch(marker_line):
                # Retrieval/storage/tool narration is diagnostic metadata,
                # never part of the fan-facing answer.  Keep this predicate
                # sentence-scoped and require both an internal noun and a
                # process verb so normal basketball analysis is unaffected.
                continue

            if in_search_section:
                # Provider results are not guaranteed to be Markdown bullets:
                # some adapters emit a plain title followed by one or more
                # plain excerpt lines.  Stay hidden until the model marks a
                # new composed-answer boundary explicitly.  Treating any
                # ordinary paragraph as that boundary leaked titles and
                # snippets into the public chat.
                if composed_boundary.search(marker_line):
                    in_search_section = False
                else:
                    continue

            output.append(line)

        # Remove duplicate separators introduced around a removed appendix.
        cleaned_lines: list[str] = []
        for line in output:
            if line == "" and (not cleaned_lines or cleaned_lines[-1] == ""):
                continue
            cleaned_lines.append(line)
        while cleaned_lines and cleaned_lines[-1] == "":
            cleaned_lines.pop()

        result = "\n".join(cleaned_lines).strip()
        result = re.sub(
            r"(?m)^\s*(?:根据|经)\s*(?:NBA\s*)?工具"
            r"(?:核验|查询|检索|确认)(?:结果)?\s*[，,:：]\s*",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?:当前|现有)(?:比赛|赛事)?记录(?:里|中)?"
            r"(?:没有|缺少|未提供|未包含)\s*"
            r"([^，,。！？!?；;\n]{1,120})\s*[，,]\s*因此",
            r"目前缺少\1，因此",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^[ \t]*现有(?:技术)?统计(?:中|显示)[ \t]*"
            r"[，,:：]?[ \t]*",
            "",
            result,
        )
        result = re.sub(
            r"目前缺少([^，,。！？!?；;\n]{1,120})\s*[，,]\s*"
            r"(?:因此)?只能给出(?:上述)?有限回顾\s*[，,]\s*"
            r"(?:因此)?不能还原([^。！？!?；;\n]{1,120})",
            r"目前缺少\1，无法可靠还原\2",
            result,
            flags=re.IGNORECASE,
        )
        # Live model prose sometimes appends a mini audit paragraph after a
        # perfectly good answer ("查询记录里…可确认；过程来自复盘报道…谨慎
        # 对待").  Evidence state already has a dedicated response field, so
        # remove that workflow narration while preserving the actual recap.
        result = re.sub(
            r"(?:需要说明(?:的是|一点)?\s*[，,:：]?\s*)?"
            r"(?:查询|检索)(?:到的)?(?:比赛|赛事)?记录(?:里|中)?"
            r"[^。！？!?；;\n]{0,220}(?:可确认|可以确认)\s*[；;]",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"[^。！？!?；;\n]{0,180}(?:来自|依据|基于)"
            r"(?:比赛|赛事)?复盘(?:报道|资料|内容)?"
            r"[^。！？!?；;\n]{0,180}(?:作为参考|谨慎对待)"
            r"[^。！？!?；;\n]{0,60}[。！？!?]?",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?:总冠军归属|上述(?:比分|统计|结果))"
            r"[^。！？!?\n]{0,140}(?:可确认|可以确认)[。！？!?]?",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"全场砍下全场最高(?:[（(][^）)]{0,40}[）)])?(?:的)?\s*",
            "砍下全场最高的 ",
            result,
        )
        result = re.sub(
            r"(?:相关)?报道(?:描述|称|指出)\s*",
            "",
            result,
            flags=re.IGNORECASE,
        )
        # Repair two common streaming joins from model prose: an adjacent
        # Markdown bullet after a Chinese full stop, and an analytical lead
        # accidentally emitted as a bullet before its numbered reasons.
        result = re.sub(r"([。！？!?）)])\s*-\s+", r"\1\n- ", result)
        result = re.sub(
            r"(?m)^\s*-\s*结合公开(?:报道|资料)(?:进行)?分析\s*[，,]\s*",
            "综合来看，",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)(^-\s+[^\n]+)\n(?=(?:综合来看|总体来看|总的来说))",
            r"\1\n\n",
            result,
        )
        result = re.sub(r"([：:])\n(?=\s*\d+[.)、]\s*)", r"\1\n\n", result)
        result = re.sub(r"\b他[，,]\s*(?=(?:这|本|该)场)", "他在", result)
        result = re.sub(
            r"[；;]\s*上述第[^。！？!?\n]{0,240}"
            r"(?:谨慎分析|分析推断)[^。！？!?\n]{0,140}"
            r"(?:逐回合事实|可确认事实|核验事实)[。！？!?]?",
            "。",
            result,
            flags=re.IGNORECASE,
        )
        # Provider/model projections occasionally place the evidence caveat
        # inline with an otherwise useful conclusion (for example,
        # ``根据相关公开报道（尚待更多公开来源交叉核验），……``).  Remove
        # only the workflow qualifier and keep the surrounding sentence; the
        # response metadata already carries the partial/verified state.
        result = re.sub(
            r"\s*[（(][^）)]{0,120}(?:交叉核验|待核验|不标记为已核验事实)[^）)]*[）)]\s*",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?:公开报道|公开网页|公开资料)(?:尚未|目前)?与(?:官方|结构化)?比赛记录交叉核验[^。！？!?；;\n]*[。！？!?；;]?",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"这些内容目前按公开网页线索处理，?不标记为已核验事实[。！？!?；;]?",
            "",
            result,
            flags=re.IGNORECASE,
        )
        # Remove model-authored evidence-process framing while preserving the
        # actual conclusion and analysis.  Evidence tier and freshness already
        # have dedicated response fields/UI chips; repeating them as prose made
        # an otherwise fluent answer read like an internal audit log.
        result = re.sub(
            r"^(?:基于|结合)[^。！？!?\n]{0,160}"
            r"(?:交叉检索|检索材料|已核验事实|比赛记录)"
            r"[^。！？!?\n]{0,100}(?:回答)?\s*[：:]\s*",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"[（(](?:已核验(?:的)?硬事实|依据公开报道(?:，?谨慎表述)?|"
            r"基于公开报道(?:，?谨慎表述)?|交叉检索材料|结构化数据)[）)]",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^\s*(?:根据|据)(?:相关|现有)?公开(?:报道|资料|信息)\s*[，,:：]\s*",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^\s*>?\s*(?:(?:需要)?说明(?:的)?(?:一下|一点)?"
            r"(?:数据)?(?:范围)?|"
            r"注|备注)\s*[：:]"
            r"[^\n]{0,400}(?:结构化|检索|公开复盘|报道|资料|材料|来源|"
            r"官方[^\n]{0,40}录像|官方逐回合记录|交叉核验|口径)"
            r"[^\n]*$",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^\s*数据截至北京时间\s*[^\n]{0,120}"
            r"(?:已核验|部分核验)[。.!！]?\s*$",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^\s*(?:已经|已|目前)?(?:拿到|获得|取得|汇总(?:了)?|整理(?:了)?)"
            r"[^。！？!?\n]{0,160}(?:硬事实|过程线索|检索材料|资料)"
            r"[^。！？!?\n]{0,100}(?:综合回答|回答如下)?[。！？!?]?\s*$",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?:需要说明(?:的是|一点)?\s*[，,:：]?\s*)?"
            r"(?:当前)?结构化(?:比赛)?记录(?:里|中)?"
            r"(?:没有|未提供|缺少|不含)\s*"
            r"([^，,。！？!?；;\n]{1,120})[，,]",
            r"目前缺少\1，",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?:打法|过程|战术)?细节(?:[（(][^）)]{0,140}[）)])?"
            r"[^。！？!?；;\n]{0,80}(?:来自|依据|基于)"
            r"[^。！？!?；;\n]{0,100}(?:公开)?(?:报道|资料|搜索|检索)"
            r"(?:线索|材料)?[^。！？!?；;\n]{0,120}[；;]",
            "因此无法可靠还原具体回合和反超节点；",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?:这些|以上)(?:信息|内容|数据)?(?:是|属于)?"
            r"已核验(?:的)?硬事实",
            "这些可以确认",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^\s*(?:\*\*)?(?:当前)?可以确认的硬事实"
            r"(?:\*\*)?\s*[：:]?\s*$",
            "**关键数据**",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^\s*(?:\*\*)?可以推断的有限可能因素"
            r"(?:\*\*)?\s*[：:]\s*",
            "**谨慎分析**：",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"[，,]\s*公开(?:报道|资料|网页)"
            r"[^。！？!?；;\n]{0,220}(?:不一致|冲突)"
            r"[^。！？!?；;\n]{0,160}[，,]",
            "，",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"我不能凭空(?:替你|为你)?(?:还原|补全|编出)",
            "因此暂时无法可靠还原",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"\A[^\n]{1,500}(?:回答|分析|结论)如下[。.!！]?"
            r"[ \t]*\n{2,}(?=(?:\*\*)?结论)",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^[ \t]*赢下的方式[ \t]*[：:][ \t]*",
            "**怎么赢的：** ",
            result,
        )
        result = re.sub(
            r"在客场领先场面下完成逆转",
            "在客场完成逆转",
            result,
        )
        result = re.sub(
            r"(?:回答如下|可以确认的是|结论如下|分析如下)\s*[。.!！]",
            "",
            result,
        )
        result = re.sub(
            r"(?m)^[ \t]*(?:综合|结合|基于)"
            r"(?=[^\n]{0,180}(?:结构化|检索|证据|过程材料))"
            r"(?=[^\n]{0,180}(?:结论|回答))"
            r"[^\n]{1,220}[ \t]*$",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^\s*(?:\*\*)?怎么赢的"
            r"(?=[^：:\n]{0,80}(?:可确认|谨慎|限定|硬事实|证据))"
            r"[^：:\n]{0,80}[：:](?:\*\*)?\s*$",
            "**怎么赢的：**",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^(\s*[-*]\s+)(?:\*\*)?(?:可确认的?)?硬事实"
            r"(?:\*\*)?\s*[：:]\s*",
            r"\1**关键数据**：",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^([ \t]*(?:[-*][ \t]+)?)(?:\*\*)?关键数据(?:\*\*)?"
            r"[ \t]*[（(][^）)\n]{0,80}(?:可确认|已核验|事实|证据)"
            r"[^）)\n]*[）)][ \t]*[：:][ \t]*",
            r"\1**关键数据**：",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^(\s*[-*]\s+)(?:\*\*)?过程(?:\*\*)?\s*"
            r"[（(][^）)\n]{0,120}(?:报道|资料|来源|措辞|核验)"
            r"[^）)\n]*[）)]\s*[：:]\s*",
            r"\1**比赛过程**：",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?m)^([ \t]*(?:[-*][ \t]+)?)(?:\*\*)?过程(?:\*\*)?[ \t]*"
            r"[（(][^）)\n]{0,120}(?:报道|资料|来源|措辞|核验)"
            r"[^）)\n]*[）)][ \t]*[：:][ \t]*",
            r"\1**比赛过程**：",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?:逐回合(?:与|和|、)?分节|分节(?:与|和|、)?逐回合)"
            r"[^。！？!?\n]{0,100}结构化(?:比赛)?(?:记录|数据)"
            r"[^。！？!?\n]{0,180}[。！？!?]?",
            "目前无法完整还原逐回合和分节走势。",
            result,
            flags=re.IGNORECASE,
        )
        # Storage/retrieval narration is response metadata, not part of the
        # answer.  A successful web-grounded synthesis must not begin by
        # explaining that another internal representation missed the query.
        result = re.sub(
            r"(?m)^\s*(?:已找到与问题相关的公开报道[；;，,。]?\s*)?"
            r"(?:当前接入的)?结构化比赛(?:数据|记录)"
            r"[^。！？!?\n]{0,180}(?:未|没有|暂未|尚未)"
            r"[^。！？!?\n]{0,180}[。！？!?]?\s*",
            "",
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(r"[ \t]{2,}", " ", result)
        # A Finals/series MVP is awarded for the whole series.  Search prose
        # can correctly mention the award while a model accidentally attaches
        # it to one game ("本场当选").  Repair only that impossible scope
        # relation and preserve the surrounding Agent-authored sentence.
        result = ChatUseCase._normalize_series_award_scope(result)
        result = re.sub(r"\n{3,}", "\n\n", result).strip()
        if saw_search_section and not result:
            # A provider-only observation is not itself a user-facing answer.
            # Keep the fallback short and natural; evidence state and source
            # provenance remain available in metadata, not in the prose.
            return "目前可用资料不足以可靠回答这个问题。"
        return result

    @staticmethod
    def _search_evidence_items(value: Any) -> list[tuple[str, str]]:
        """Parse the bounded search projection into title/summary pairs."""

        items: list[tuple[str, str]] = []
        for raw_line in str(value or "").splitlines():
            line = raw_line.strip()
            if not re.match(r"^(?:[-*]|\d+[.)])\s+", line):
                continue
            content = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", line).strip()
            match = re.match(r"^\*\*(.+?)\*\*\s*[：:]\s*(.+)$", content)
            if match:
                title = ChatUseCase._short_public_text(match.group(1), limit=140)
                summary = ChatUseCase._short_public_text(match.group(2), limit=420)
            else:
                if content.startswith(("**", "__", "`")):
                    # A decorated row whose delimiter is inside the decoration
                    # is a title, not ``title: summary``.  Do not let the colon
                    # within a headline (for example ``**夺冠：完整回顾**``)
                    # masquerade as an evidence separator.
                    continue
                title, separator, summary = content.partition("：")
                if not separator:
                    title, separator, summary = content.partition(":")
                if not separator:
                    # A delimiter-less row is indistinguishable from a search
                    # headline.  Headlines help retrieval ranking but are not
                    # evidence, so never promote one into a factual answer.
                    continue
                title = ChatUseCase._short_public_text(title, limit=140)
                summary = ChatUseCase._short_public_text(summary, limit=420)
            # A headline is only a retrieval label.  It is not sufficient
            # evidence for a factual answer: Baidu/AI-search providers often
            # return a title-only knowledge-card row or an empty snippet.  Do
            # not allow those rows to become conclusions in the recovery path.
            if summary:
                items.append((title, summary))
        return items

    @staticmethod
    def _synthesize_search_answer(
        question: str,
        value: Any,
        *,
        public: bool = False,
    ) -> str:
        """Produce a stable answer from ranked partial web evidence.

        This path is used only when the model did not compose the completed
        search observation before its turn ended.  It extracts claims rather
        than rendering a search-results page.  Search provenance and evidence
        state are carried in the response metadata; the answer body should
        read like a normal, concise response and must not expose provider
        headings, copied result lists, or workflow disclaimers.
        """

        items = ChatUseCase._search_evidence_items(value)
        if not items:
            # Search headings and title-only rows are internal grounding
            # material, never a public answer.  In particular, do not fall
            # back to ``_compact_search_markdown`` here: doing so turns a
            # provider title into an apparently verified claim and was the
            # source of the leaked “补充线索” blocks in the UI.
            answer = "目前可用资料不足以可靠回答这个问题。"
            return ChatUseCase._strip_public_search_sections(answer) if public else answer

        entities = [
            item
            for item in resolve_entities(str(question or ""))
            if item.kind in {EntityKind.TEAM, EntityKind.PLAYER}
        ]
        entity_aliases = [
            str(alias).casefold()
            for entity in entities
            for alias in [entity.display_name, *entity.aliases]
            if str(alias).strip()
        ]
        question_text = str(question or "")
        semantic_terms = list(
            dict.fromkeys(
                term
                for group in _SEARCH_TOPIC_GROUPS
                if any(trigger in question_text for trigger in group)
                for term in group
            )
        )
        # Topic groups are used for retrieval ranking, not as answer text.  A
        # search snippet can contain a headline fragment or a provider name;
        # never let those labels become the selected conclusion.
        semantic_terms = [
            term for term in semantic_terms if term not in {"复盘", "分析", "解读"}
        ]
        if semantic_terms:
            topical_items = [
                item
                for item in items
                if any(term in f"{item[0]} {item[1]}" for term in semantic_terms)
            ]
            if topical_items:
                items = topical_items
            if entity_aliases:
                def topic_is_centered(value: str) -> bool:
                    clauses = [
                        part.casefold()
                        for part in re.split(r"[，,。！？!?；;：:\n]+", value)
                        if part.strip()
                    ]
                    return any(
                        any(alias in clause for alias in entity_aliases)
                        and any(term in clause for term in semantic_terms)
                        for clause in clauses
                    )

                body_centered = [
                    item for item in items if topic_is_centered(item[1])
                ]
                if body_centered:
                    items = body_centered
                else:
                    title_centered = [
                        item for item in items if topic_is_centered(item[0])
                    ]
                    if title_centered:
                        items = title_centered
        outcome_question = bool(
            (
                len([item for item in entities if item.kind is EntityKind.TEAM]) >= 2
                and not semantic_terms
            )
            or re.search(r"(?:总决赛|系列赛|比分|赛果|结果|谁赢|冠军|夺冠(?!后))", question)
        )
        candidates: list[tuple[int, int, str]] = []
        position = 0
        for title, summary in items:
            # ``_search_evidence_items`` already filters empty summaries, but
            # keep this guard local so future callers cannot accidentally
            # reintroduce title-only evidence.
            if not str(summary or "").strip():
                # A headline without a snippet is a retrieval label only.  It
                # may be useful to a ranking layer, but it is not evidence and
                # must never become the public answer by itself.
                position += 1
                continue
            source = summary
            source = neutralize_external_internal_names(source)
            source = re.sub(r"(?:公开资料\s*){2,}", "公开资料 ", source).strip()
            sentences = [
                part.strip(" ，,；;：:。.!！?？…")
                for part in re.split(r"(?<=[。！？!?；;])\s*", source)
                if part.strip(" ，,；;：:。.!！?？…")
            ] or [source]
            for sentence in sentences[:4]:
                folded = sentence.casefold()
                score = 0
                score += sum(3 for alias in entity_aliases if alias in folded)
                score += sum(15 for token in semantic_terms if token in sentence)
                score += sum(
                    weight if outcome_question else max(1, weight // 4)
                    for token, weight in (
                        ("总冠军", 8),
                        ("夺冠", 7),
                        ("战胜", 6),
                        ("击败", 6),
                        ("总比分", 6),
                        ("系列赛", 4),
                        ("比分", 3),
                        ("得分", 2),
                    )
                    if token in sentence
                )
                if re.search(r"\d+\s*[-:：比–—]\s*\d+", sentence):
                    score += 5
                candidates.append((score, -position, sentence))
                position += 1
        if not candidates:
            # All rows were title-only (or empty).  Do not turn those labels
            # into a pseudo-answer; make the lack of usable evidence explicit.
            return "目前可用资料不足以可靠回答这个问题。"
        candidates.sort(reverse=True)
        conclusion = ChatUseCase._short_public_text(candidates[0][2], limit=280)
        details: list[str] = []
        conclusion_key = re.sub(r"\s+", "", conclusion).casefold()
        for _score, _position, candidate in candidates[1:]:
            detail = ChatUseCase._short_public_text(candidate, limit=220)
            key = re.sub(r"\s+", "", detail).casefold()
            if not detail or key == conclusion_key:
                continue
            if key in conclusion_key or conclusion_key in key:
                continue
            if any(key == re.sub(r"\s+", "", old).casefold() for old in details):
                continue
            details.append(detail)
            if len(details) >= 2:
                break

        # Keep the fallback conversational.  A response-level evidence state
        # already tells the UI that these are public-report facts; repeating
        # an implementation caveat or a search-results heading makes the
        # assistant look like a provider proxy and was explicitly disallowed by
        # the product contract.
        answer = "根据相关公开报道，" + conclusion.rstrip("。.!！") + "。"
        if details:
            # Use ordinary prose rather than an article-title/bullet dump.
            # Keep at most two short details so a provider response cannot
            # consume the whole answer budget or drown out the conclusion.
            answer += " " + " ".join(
                f"另外，{item.rstrip('。.!！')}。" for item in details
            )
        return (
            ChatUseCase._strip_public_search_sections(answer)
            if public
            else answer
        )

    @staticmethod
    def _looks_like_failed_search_answer(value: Any) -> bool:
        raw = str(value or "")
        failure = re.compile(
            r"(?:暂未找到|没有找到|未找到|无法回答|无法确认|"
            r"结构化比赛数据尚未返回|请补充(?:具体)?(?:日期|场次|比赛|查询对象))"
        )
        if not failure.search(raw):
            return False
        # Preserve honest partial answers that first state what can be
        # established and then delimit one missing detail.  The old substring
        # check discarded the entire Agent synthesis whenever it contained
        # “无法确认”, even after a useful conclusion.
        if re.search(
            r"(?:能|可|已经|已)(?:够)?确认[^。！？!?\n]{1,220}"
            r"(?:但|不过|然而|只是)",
            raw,
        ):
            return False
        clauses = [
            part.strip()
            for part in re.split(r"(?:[。！？!?；;\n]+|但|不过|然而)", raw)
            if part.strip()
        ]
        substantive = re.compile(
            r"(?:战胜|击败|取胜|获胜|赢下|赢了|赢球|领先|守住|追平|反超|"
            r"末节|防守|进攻|篮板|关键|优势|逆转|加时|罚球|"
            r"终场比分|比分(?:是|为|来到)|得到\s*\d+|命中|发生在|举办于)"
        )
        if any(not failure.search(part) and substantive.search(part) for part in clauses):
            return False
        return True

    @staticmethod
    def _structured_miss_lead(value: Any) -> str:
        """Describe a structured miss without contradicting a successful search.

        This lead is used only after the public-search observation completed.
        Reusing the original no-data template here incorrectly told users that
        no public record existed immediately before showing public reports.
        Keep the two evidence scopes explicit instead.
        """

        del value
        return (
            "已找到与问题相关的公开报道；"
            "当前接入的结构化比赛数据尚未返回直接匹配。"
        )

    @staticmethod
    def _compact_mixed_search_answer(value: Any) -> str:
        """Bound a mixed structured-miss/search observation for recovery."""

        raw = str(value or "").strip()
        marker_at = raw.find("公开资料线索")
        if marker_at < 0:
            return ChatUseCase._short_public_text(raw, limit=600)
        lead = ChatUseCase._short_public_text(raw[:marker_at], limit=220)
        evidence = ChatUseCase._compact_search_markdown(
            raw[marker_at:],
            max_items=2,
            item_limit=200,
        )
        caveat = (
            "以上为公开网页线索，尚未与官方比赛记录交叉核验；"
            "网页之间如有日期或比分不一致，以官方记录为准。"
        )
        return "\n\n".join(item for item in (lead, evidence, caveat) if item)

    @staticmethod
    def _compact_news_answer(value: Any, *, max_items: int = 3) -> str:
        """Keep a model-written news answer concise without losing its caveat."""

        raw = str(value or "").strip()
        if not raw:
            return ""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        intro: list[str] = []
        points: list[str] = []
        tail: list[str] = []
        in_points = False
        for line in lines:
            is_point = bool(re.match(r"^(?:[-*]|\d+[.)])\s+", line))
            if is_point:
                in_points = True
                if len(points) < max_items:
                    points.append(re.sub(r"^(?:[-*]|\d+[.)])\s+", "", line))
                continue
            if not in_points:
                intro.append(line)
            elif re.search(r"(?:说明|注意|需要说明|核验|线索|资料|无法|未能)", line):
                tail.append(line)
        output: list[str] = []
        if intro:
            output.append(ChatUseCase._short_public_text(" ".join(intro), limit=360))
        output.extend(f"- {ChatUseCase._short_public_text(point, limit=360)}" for point in points)
        # Do not expose the compaction implementation in the user-facing
        # answer.  Saying that "the remaining clues were omitted" makes the
        # response read like a search-result proxy and leaks an internal
        # truncation decision; the bounded points above are sufficient.
        if tail:
            output.append(ChatUseCase._short_public_text(tail[0], limit=280))
        return "\n".join(item for item in output if item)

    @staticmethod
    def _looks_like_incomplete_agent_answer(value: Any, question: str = "") -> bool:
        """Detect a model response that ended before it became user-facing prose.

        A bounded Agent turn can return a successful envelope after its last tool
        call even when generation stopped at a section heading.  Treating that
        envelope as a complete answer leaks implementation status and leaves the
        user with an empty ``## 过程`` section.  This detector is deliberately
        conservative and only targets clear truncation markers, dangling
        Markdown structure, or a heading-only analytical response.
        """

        text = str(value or "").strip()
        if not text:
            return True
        if re.search(
            r"(?:工具预算|调用预算|迭代预算|工具调用(?:次数)?(?:已)?(?:达到|超过)(?:上限|限制)|"
            r"预算(?:已)?(?:用尽|耗尽)|(?:输出|生成)(?:内容)?(?:已)?(?:被)?截断|"
            r"输出不完整|无法继续(?:调用|生成)|"
            r"(?:系统|服务|本轮|当前(?:回答|任务))[^。！？!?\n]{0,40}内部(?:执行|流程))",
            text,
            re.IGNORECASE,
        ):
            return True
        if text.count("```") % 2:
            return True
        if text.count("**") % 2:
            return True
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return True
        last = lines[-1]
        # A final Markdown heading (with or without a colon) has no answer body.
        if re.fullmatch(r"#{1,6}\s+\S(?:.*)?", last):
            return True
        if re.search(r"[:：]\s*$", last) and len(last) <= 180:
            return True
        analytical = ChatUseCase._is_open_ended_agent_synthesis(question) or bool(
            re.search(r"(?:为什么|为何|原因|战术|复盘|过程|讲讲|分析|解读)", question)
        )
        if analytical and re.search(r"[；;，,]\s*$", last):
            return True
        # A concise analytical answer can be perfectly complete (for example
        # ``凯尔特人靠防守赢下比赛。``).  Length alone is not evidence of a
        # transport truncation; using a fixed character threshold here caused
        # the application to discard short Hermes syntheses and replace them
        # with a structured/search recovery answer.  Only treat very short
        # non-sentence fragments as incomplete.  Terminal punctuation is a
        # strong signal that the model finished its thought, even when the
        # answer is intentionally brief.
        if analytical and len(text) < 12 and not re.search(r"[。！？!?]$", text):
            return True
        return False

    @staticmethod
    def _series_recommendation_recovery(
        question: str,
        observations: list[dict[str, Any]],
        series_candidates: Mapping[int, Game] | None = None,
        preferred_game_number: int | None = None,
    ) -> str | None:
        """Choose one observed series game using score and series-position facts.

        This is intentionally narrower than a generative recap.  It runs only
        when a recommendation turn lost its final prose after a completed
        typed series lookup.  The choice is based on minimum score margin, with
        the later game winning a tie because it carries more series leverage.
        No comeback, tactical, or play-by-play detail is inferred.
        """

        if not is_positive_series_selection_question(question):
            return None
        completed = [
            item
            for item in observations
            if isinstance(item, Mapping)
            and str(item.get("status", "")).lower() == "completed"
            and str(item.get("coverage", "")).lower() == "series_candidates_ready"
        ]
        if not completed:
            return None

        rows: list[dict[str, Any]] = []
        for number, game in sorted((series_candidates or {}).items()):
            if game.home_score is None or game.away_score is None:
                continue
            home_won = game.home_score > game.away_score
            rows.append(
                {
                    "number": int(number),
                    "home": game.home.display_name,
                    "away": game.away.display_name,
                    "home_score": int(game.home_score),
                    "away_score": int(game.away_score),
                    "winner": (
                        game.home.display_name if home_won else game.away.display_name
                    ),
                    "loser": (
                        game.away.display_name if home_won else game.home.display_name
                    ),
                    "winner_score": int(
                        game.home_score if home_won else game.away_score
                    ),
                    "loser_score": int(
                        game.away_score if home_won else game.home_score
                    ),
                    "margin": abs(int(game.home_score) - int(game.away_score)),
                }
            )
        if not rows:
            line_pattern = re.compile(
                r"(?m)^\s*-\s*G(?P<number>[1-7])\s*[：:]\s*"
                r"(?P<away>.+?)\s+(?P<away_score>\d{1,3})\s*[-–—]\s*"
                r"(?P<home_score>\d{1,3})\s+(?P<home>.+?)"
                r"（分差\s*(?P<margin>\d{1,3})\s*分"
                r"(?:[，,](?P<winner>.+?)胜)?）"
            )
            for match in line_pattern.finditer(
                str(completed[-1].get("answer_markdown") or "")
            ):
                away_score = int(match.group("away_score"))
                home_score = int(match.group("home_score"))
                home = match.group("home").strip()
                away = match.group("away").strip()
                home_won = home_score > away_score
                rows.append(
                    {
                        "number": int(match.group("number")),
                        "home": home,
                        "away": away,
                        "home_score": home_score,
                        "away_score": away_score,
                        "winner": home if home_won else away,
                        "loser": away if home_won else home,
                        "winner_score": home_score if home_won else away_score,
                        "loser_score": away_score if home_won else home_score,
                        "margin": int(match.group("margin")),
                    }
                )
        if len(rows) < 2:
            return None

        rows.sort(key=lambda row: int(row["number"]))
        query_scope = completed[-1].get("query_scope")
        active_number: int | None = None
        if isinstance(query_scope, Mapping):
            try:
                raw_active = query_scope.get("active_game_number")
                active_number = int(raw_active) if raw_active is not None else None
            except (TypeError, ValueError):
                active_number = None
        challenged_match = re.search(
            r"为什么\s*(?:并)?不是\s*G\s*([1-7])",
            str(question or ""),
            re.IGNORECASE,
        )
        if (
            active_number is not None
            and challenged_match is not None
            and int(challenged_match.group(1)) == active_number
        ):
            active = next(
                (row for row in rows if int(row["number"]) == active_number),
                None,
            )
            if active is not None:
                return (
                    f"我刚才推荐的就是 **G{active_number}**。"
                    f"这场由 {active['winner']} 以 "
                    f"**{active['winner_score']}–{active['loser_score']}** 取胜，"
                    f"分差只有 **{active['margin']} 分**；"
                    "如果您想比较的是另一场，我可以按胶着程度或系列赛转折继续对比。"
                )

        minimum_margin = min(int(row["margin"]) for row in rows)
        tied = [row for row in rows if int(row["margin"]) == minimum_margin]
        # If the Agent made a valid subjective choice among equally close
        # games but attached one incorrect objective relation, repair the
        # relation without silently changing its public recommendation. Keep
        # the established deterministic choice for a genuinely unsupported
        # candidate (for example a non-minimum-margin G3).
        preferred = next(
            (
                row
                for row in tied
                if preferred_game_number is not None
                and int(row["number"]) == int(preferred_game_number)
            ),
            None,
        )
        selected = preferred or max(tied, key=lambda row: int(row["number"]))
        selected_number = int(selected["number"])
        winner = str(selected["winner"])
        loser = str(selected["loser"])

        wins: dict[str, int] = {}
        for row in rows:
            if int(row["number"]) > selected_number:
                break
            row_winner = str(row["winner"])
            wins[row_winner] = wins.get(row_winner, 0) + 1
        winner_wins = wins.get(winner, 0)
        loser_wins = wins.get(loser, 0)

        if len(tied) > 1:
            other_ties = "、".join(
                f"G{int(row['number'])}"
                for row in tied
                if int(row["number"]) != selected_number
            )
            margin_reason = (
                f"它与 {other_ties} 同为全系列最小分差，都是 **{minimum_margin} 分**"
            )
        else:
            margin_reason = f"它是全系列分差最小的一场，只有 **{minimum_margin} 分**"

        previous = next(
            (
                row
                for row in reversed(rows)
                if int(row["number"]) < selected_number
            ),
            None,
        )
        if previous is not None and str(previous["winner"]) != winner:
            leverage_reason = (
                f"而且 {winner} 在 G{int(previous['number'])} 失利后拿下这一场，"
                f"把系列赛推进到 **{winner_wins}–{loser_wins}**"
            )
        else:
            leverage_reason = (
                f"拿下这一场后，{winner} 将系列赛推进到 "
                f"**{winner_wins}–{loser_wins}**，比赛节点更关键"
            )
        return (
            f"如果按比赛胶着程度和系列赛转折，我会选 **G{selected_number}**。"
            f"{winner} 以 **{selected['winner_score']}–{selected['loser_score']}** "
            f"击败 {loser}；{margin_reason}；{leverage_reason}。"
        )

    @staticmethod
    def _player_comparison_recovery(
        question: str,
        searches: list[dict[str, Any]],
    ) -> AgentObservationRecovery | None:
        """Recover a useful comparison without publishing search-result prose.

        Search snippets can ground a normal model synthesis, but they are a
        poor final-answer format when the model times out: choosing three
        high-scoring sentences produced headline fragments joined by repeated
        ``另外``. A subjective greatness question does not require invented
        statistics. A provider may cover only one of the requested players;
        that uneven evidence must not be used for player-specific claims, but
        it also does not make the user's complete comparison question
        ambiguous. Once the bounded search operation has finished, return a
        fact-free evaluation framework and leave its weighting to the user.
        """

        if not is_subjective_comparison_question(question):
            return None
        players: list[EntityRef] = []
        seen_ids: set[str] = set()
        for item in resolve_entities(str(question or "")):
            if item.kind is not EntityKind.PLAYER or item.canonical_id in seen_ids:
                continue
            seen_ids.add(item.canonical_id)
            players.append(item)
        if len(players) < 2:
            return None

        attempted = [
            item
            for item in searches
            if str(item.get("intent", "")).lower() in {"web_search", "nba_news"}
            and str(item.get("status", "")).lower()
            in {"completed", "no_data", "needs_clarification"}
            and str(item.get("answer_markdown") or "").strip()
        ]
        if not attempted:
            return None

        # Deliberately do not promote uneven snippets into factual claims.
        # The recovery below is identical whether the observation covers both
        # players, one player, or neither after a completed search miss.

        question_text = str(question or "").casefold()

        def public_label(player: EntityRef) -> str:
            # Prefer the surface form the user actually supplied. Joining two
            # full Chinese interpunct names with “和” can look like one long
            # proper name to the conservative output guard.
            matching = [
                str(alias)
                for alias in [*player.aliases, player.display_name]
                if str(alias).strip()
                and str(alias).casefold() in question_text
            ]
            return max(matching, key=len, default=player.display_name)

        first, second = players[:2]
        first_label = public_label(first)
        second_label = public_label(second)
        answer = (
            f"**结论**：{first_label}和{second_label}谁“更伟大”"
            "没有唯一客观口径，取决于您更看重哪一类标准。\n\n"
            "- **荣誉与团队成绩**：比较总冠军、MVP，以及作为球队核心时的季后赛成绩。\n"
            "- **个人峰值与数据**：比较巅峰期得分、攻防表现和高水平赛季的统治力。\n"
            "- **生涯长度与稳定性**：比较高水平持续时间、累计表现和长期稳定输出。\n"
            "- **时代与角色差异**：结合规则环境、球队职责，以及对比赛方式的影响力。\n\n"
            "如果您更重视冠军舞台和短期峰值，可以提高前两项权重；"
            "如果更重视漫长生涯和全面角色，则应提高后两项权重。"
        )
        return AgentObservationRecovery(answer, attempted[-1:], "completed")

    @staticmethod
    def _recover_observation_answer(
        question: str,
        observations: list[dict[str, Any]],
        series_candidates: Mapping[int, Game] | None = None,
        preferred_game_number: int | None = None,
    ) -> AgentObservationRecovery | None:
        """Compose a complete answer from server-owned observations.

        This is used when model prose is truncated or exposes an internal
        execution status.  For an open recap, retain the structured game facts
        and append a bounded search synthesis; for an objective turn, return
        the most specific typed observation.  No model-generated text is used
        as factual authority in this repair path.
        """

        usable = [
            item
            for item in observations
            if isinstance(item, Mapping)
            and str(item.get("status", "")).lower()
            in {"completed", "no_data", "needs_clarification"}
        ]
        if not usable:
            return None

        recommendation = ChatUseCase._series_recommendation_recovery(
            question,
            usable,
            series_candidates,
            preferred_game_number,
        )
        if recommendation:
            recommendation_observations = [
                item
                for item in usable
                if str(item.get("status", "")).lower() == "completed"
                and str(item.get("coverage", "")).lower()
                == "series_candidates_ready"
            ]
            return AgentObservationRecovery(
                recommendation,
                recommendation_observations[-1:],
                "completed",
            )

        comparison = ChatUseCase._player_comparison_recovery(question, usable)
        if comparison is not None:
            return comparison

        structured = [
            item
            for item in usable
            if str(item.get("intent", "")).lower() == "nba_query"
            and str(item.get("answer_markdown") or "").strip()
        ]
        searches = [
            item
            for item in usable
            if str(item.get("intent", "")).lower() in {"web_search", "nba_news"}
            and str(item.get("status", "")).lower() == "completed"
            and str(item.get("answer_markdown") or "").strip()
        ]
        analytical = ChatUseCase._is_open_ended_agent_synthesis(question) or bool(
            re.search(
                r"(?:为什么|为何|原因|战术|挡拆|防守策略|怎么限制|如何限制|复盘|"
                r"限制|如何防|怎么防|布置防守|关键转折|表现如何|评价|分析|解读|"
                r"比赛过程|怎么打的|讲讲)",
                str(question or ""),
                re.IGNORECASE,
            )
        )

        if analytical and structured and searches:
            # Prefer the last completed typed result; a no-data result is still
            # useful as a transparent limitation when no complete row exists.
            structured.sort(
                key=lambda item: str(item.get("status", "")).lower() == "completed",
                reverse=True,
            )
            primary = structured[0]
            primary_text = str(primary.get("answer_markdown") or "").strip()
            if (
                str(primary.get("status", "")).lower()
                in {"no_data", "needs_clarification"}
                and ChatUseCase._looks_like_failed_search_answer(primary_text)
            ):
                # A typed miss is internal routing state, not useful prose.
                # If search evidence can still support an answer, present that
                # synthesis directly and carry the weaker evidence state in
                # metadata instead of narrating the storage/retrieval process.
                primary_text = ""
            primary_text = neutralize_external_internal_names(primary_text)
            if (
                str(primary.get("status", "")).lower() == "completed"
                and primary_text
                and re.search(
                    r"(?:\*\*分析\*\*|(?:^|\n)分析[：:]|事实依据|"
                    r"(?:靠|通过|关键在于|核心是|建议|可以|应该).{0,80})",
                    primary_text,
                )
            ):
                # The typed game observation already answers the requested
                # explanation and carries game-scoped evidence.  Appending a
                # broad web result here can only weaken it: one search summary
                # may contain forum metadata, a different game, or an unrelated
                # award aside.  Search remains internal context, while the
                # complete scoped explanation is the public recovery answer.
                return AgentObservationRecovery(
                    primary_text,
                    [primary],
                    "completed",
                )
            search_text = ChatUseCase._synthesize_search_answer(
                question,
                searches[-1].get("answer_markdown"),
            )
            if primary_text and search_text:
                combined = (
                    f"{primary_text}\n\n"
                    f"分析：\n{search_text}"
                )
            else:
                combined = primary_text or search_text
            if combined:
                return AgentObservationRecovery(
                    ChatUseCase._neutralize_mixed_evidence_labels(combined),
                    [primary, searches[-1]],
                    "completed",
                )

        # A schedule observation is not a game recap.  If the Agent picked the
        # date tool for an open narrative question, keep the useful web
        # synthesis and omit the unrelated schedule projection entirely.
        if analytical and searches and not structured:
            search_text = ChatUseCase._synthesize_search_answer(
                question,
                searches[-1].get("answer_markdown"),
            )
            if search_text:
                return AgentObservationRecovery(
                    "分析：\n" + search_text,
                    [searches[-1]],
                    "completed",
                )

        # A score, fixture list, or generic clarification is not an answer to
        # a recommendation/comparison/tactical/recap prompt.  Let the caller
        # return a scoped evidence limitation instead of laundering it into a
        # successful Agent response.
        if analytical:
            completed_structured = [
                item
                for item in structured
                if str(item.get("status", "")).lower() == "completed"
            ]
            for item in reversed(completed_structured):
                text = str(item.get("answer_markdown") or "").strip()
                if re.search(
                    r"(?:\*\*分析\*\*|(?:^|\n)分析[：:]|事实依据|"
                    r"(?:靠|通过|关键在于|核心是|建议|可以|应该).{0,80})",
                    text,
                ):
                    return AgentObservationRecovery(
                        neutralize_external_internal_names(text),
                        [item],
                        "completed",
                    )
            return None

        # Objective or single-source recovery: choose a completed typed result,
        # then a bounded public-search result, preserving the observation's
        # server-owned wording.
        candidates = sorted(
            usable,
            key=lambda item: (
                str(item.get("status", "")).lower() == "completed",
                {"nba_query": 4, "schedule_result": 3, "web_search": 2, "nba_news": 1}.get(
                    str(item.get("intent", "")).lower(), 0
                ),
            ),
            reverse=True,
        )
        for item in candidates:
            text = str(item.get("answer_markdown") or "").strip()
            if not text:
                continue
            if str(item.get("intent", "")).lower() in {"web_search", "nba_news"}:
                text = ChatUseCase._synthesize_search_answer(question, text)
            item_status = str(item.get("status", "")).lower()
            return AgentObservationRecovery(
                neutralize_external_internal_names(text),
                [item],
                item_status
                if item_status in {"completed", "no_data", "needs_clarification"}
                else "no_data",
            )
        return None

    @staticmethod
    def _looks_like_uncomposed_search(value: Any) -> bool:
        """Detect when Hermes returned the search observation verbatim.

        A provider observation starts with the neutral ``公开资料线索`` heading
        and consists almost entirely of bullets.  It is valid evidence, but it
        is not a useful answer to a tactical/recap question by itself.  This
        narrow detector lets the application add a user-facing analytical
        frame without touching a model-generated answer that already contains
        a conclusion and reasoning.
        """

        raw = str(value or "").strip()
        marker_at = raw.find("公开资料线索")
        if not raw or marker_at < 0:
            return False
        bullet_count = len(
            re.findall(r"(?m)^\s*(?:[-*]|\d+[.)])\s+", raw)
        )
        if bullet_count == 0:
            return False
        # A composed answer normally contains explicit analytical markers.
        # Search titles can contain these words incidentally, so only inspect
        # the lead before the first bullet.
        lead = raw[:marker_at]
        return not re.search(r"(?:结论|分析|建议|可能因素|思路|不足以确认)", lead)

    @staticmethod
    def _is_tactical_advice_question(question: Any) -> bool:
        """Return whether the user asks for actionable defensive tactics.

        Retrospective questions such as “为什么赢” require game-specific
        evidence and are intentionally excluded.  This gate targets the PDF's
        strategy-advice class, where a useful answer should describe concrete
        coverage actions instead of repeating search snippets.
        """

        text = str(question or "")
        return bool(
            re.search(
                r"(?:如何|怎么|该怎么|应该怎么|怎样).{0,20}"
                r"(?:限制|防守|防住|应对|布置)|"
                r"(?:限制|防守|防住|应对).{0,24}"
                r"(?:无球|跑动|挡拆|持球|投射|突破)|"
                r"防守端.{0,18}(?:怎么|如何|应该|布置|策略)",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _tactical_answer_is_high_quality(
        question: Any,
        answer: Any,
        observations: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Require a composed conclusion and multiple executable actions.

        Search observations remain useful grounding, but their presence alone
        cannot make a headline/summary collage a tactical answer.  The check
        is lexical and deliberately generic: it validates basketball actions,
        not one hard-coded player or scheme.
        """

        del observations  # Evidence authority is enforced by OutputGuard.
        if not ChatUseCase._is_tactical_advice_question(question):
            return True
        text = str(answer or "").strip()
        if len(text) < 32:
            return False
        if any(
            marker in text
            for marker in (
                "球迷热议",
                "文章提到",
                "报道提到",
                "公开资料认为",
                "搜索摘要",
                "公开资料线索",
                "补充线索",
            )
        ) or text.count("另外") >= 2:
            return False

        has_conclusion = bool(
            re.search(
                r"(?:核心(?:建议|思路|原则)|总体(?:建议|思路)|"
                r"建议|可以|应当|应该|需要|优先|关键(?:在于|是))",
                text,
            )
        )
        if not has_conclusion:
            return False

        action_re = re.compile(
            r"(?:追防|阻断|挤过|绕过|延误|换防|夹击|轮转|补位|"
            r"收缩|回补|保护|干扰|逼迫|放投|封堵|绕前|切断)"
        )
        object_re = re.compile(
            r"(?:接球|掩护|挡拆|持球|无球|跑动|弱侧|强侧|篮下|"
            r"顺下|外线|三分|突破|传球|出球|投篮空间)"
        )
        clauses = [
            part.strip()
            for part in re.split(r"(?:\n+|[。；;])", text)
            if part.strip()
        ]
        actionable = sum(
            bool(action_re.search(clause) and object_re.search(clause))
            for clause in clauses
        )
        return actionable >= 2

    @staticmethod
    def _tactical_advice_recovery(question: Any) -> str:
        """Return concise, question-scoped advice after a poor Agent draft."""

        players = [
            item
            for item in resolve_entities(str(question or ""))
            if item.kind is EntityKind.PLAYER
        ]
        subject = "对方核心外线"
        if players:
            question_text = str(question or "").casefold()
            mentioned_aliases = [
                str(alias)
                for alias in players[0].aliases
                if str(alias).strip()
                and str(alias).casefold() in question_text
            ]
            # Prefer the user's own compact reference.  Besides sounding more
            # natural, this avoids inventing a formal name variant that may
            # not appear in the completed observation.
            subject = min(mentioned_aliases, key=len) if mentioned_aliases else "这位球员"
        return (
            f"核心建议：不要只靠单一夹击，要同时压缩{subject}的无球接球路线和挡拆空间。\n"
            "- 无球端保持贴身追防，在其惯用启动侧提前阻断接球路线，减少舒服起跑和接球投篮。\n"
            "- 挡拆端由外线挤过掩护，内线短暂延误持球；根据场上阵容在换防与夹击之间变化。\n"
            "- 弱侧轮转要提前补位，先保护篮下和顺下，再快速回补外线，避免协防后漏出空位。"
        )

    @staticmethod
    def _compact_question_player_names(question: Any, answer: Any) -> str:
        """Reuse a short player alias already present in the user question."""

        question_text = str(question or "")
        projected = str(answer or "")
        for player in resolve_entities(question_text):
            if player.kind is not EntityKind.PLAYER:
                continue
            aliases = [
                str(alias)
                for alias in player.aliases
                if str(alias).strip()
                and str(alias).casefold() in question_text.casefold()
            ]
            if not aliases:
                continue
            shortest = min(aliases, key=len)
            if player.display_name != shortest:
                projected = projected.replace(player.display_name, shortest)
        return projected

    @staticmethod
    def _frame_search_analysis(value: Any, question: str) -> str:
        """Turn a raw search list into bounded, honest analytical prose."""

        synthesized = ChatUseCase._synthesize_search_answer(
            question,
            value,
            public=True,
        )
        if not synthesized:
            return ""
        question_text = str(question or "")
        if re.search(r"(?:限制|防守|挡拆|怎么防|如何防)", question_text):
            lead = (
                "可参考的通用防守思路（分析建议）："
            )
        elif re.search(r"(?:为什么能赢|为何能赢|为什么输|为何失利|复盘|战术)", question_text):
            lead = (
                "目前资料不足以确认本场的完整战术原因，下面是可参考的分析方向："
            )
        else:
            lead = "初步分析："
        # The raw provider list is deliberately not returned.  Keep only the
        # bounded natural-language synthesis so a degraded model turn still
        # answers the question instead of exposing article titles/snippets.
        return f"{lead}\n\n{synthesized}"

    @staticmethod
    def _agent_observation_answer(
        question: str,
        result: AgentTurnResult,
        *,
        series_candidates: Mapping[int, Game] | None = None,
        expected_game_id: str | None = None,
    ) -> AgentObservationRecovery | None:
        """Return a safe answer from a completed Agent observation.

        A model turn can end at the iteration/latency boundary after the NBA
        tool already returned a complete answer.  Treating that as a hard
        failure discarded useful context and made ordinary questions appear
        to "fall back".  This helper reuses only server-sanitised observation
        text; it never promotes the model's ungrounded prose to fact.
        """

        usable = [
            item
            for item in result.observations
            if isinstance(item, Mapping)
            and str(item.get("status", "")).lower()
            in {"completed", "no_data", "needs_clarification"}
        ]
        if not usable:
            return None
        # Prefer the observation repair path for open recaps.  This also
        # handles a timeout/iteration-boundary result where the model emitted
        # no final prose but both the typed game record and web context are
        # already complete.
        relevant = ChatUseCase._agent_result_relevant(
            question,
            result,
            expected_game_id=expected_game_id,
        )
        repaired = ChatUseCase._recover_observation_answer(
            question,
            usable,
            series_candidates,
        )
        recommendation_repair = bool(
            repaired is not None
            and is_positive_series_selection_question(question)
            and any(
                str(item.get("coverage", "")).lower()
                == "series_candidates_ready"
                for item in repaired.observations
            )
        )
        # A malformed, generic or truncated model answer is exactly when
        # ``_agent_result_relevant`` becomes false.  Do not let that advisory
        # envelope discard a complete repair derived only from trusted NBA or
        # search observations.  For open-ended questions the recovery helper
        # has already rejected schedule-only/candidate-only evidence; keep the
        # stricter relevance requirement for objective questions so a wrong
        # tool still cannot answer another task.
        open_ended_repair = (
            repaired is not None
            and ChatUseCase._is_open_ended_agent_synthesis(question)
        )
        if repaired is not None and (
            relevant or recommendation_repair or open_ended_repair
        ):
            return repaired
        if not relevant:
            return None
        # Prefer the typed NBA answer, then public search/news context.  The
        # observation list is ordered by tool execution, so reverse order
        # keeps the final, most-specific result while still allowing a search
        # result to answer a long-tail question.
        priority = {
            "nba_query": 4,
            "public_reverification": 4,
            "schedule_result": 3,
            "nba_news": 2,
            "web_search": 1,
        }
        status_rank = {"completed": 3, "needs_clarification": 2, "no_data": 1}
        candidates = sorted(
            usable,
            key=lambda item: (
                status_rank.get(str(item.get("status", "")).lower(), 0),
                priority.get(str(item.get("intent", "")).lower(), 0),
            ),
            reverse=True,
        )
        for item in candidates:
            answer = str(item.get("answer_markdown") or "").strip()
            if answer:
                intent = str(item.get("intent", "")).lower()
                if intent in {"web_search", "nba_news"}:
                    answer = ChatUseCase._synthesize_search_answer(question, answer)
                item_status = str(item.get("status", "")).lower()
                return AgentObservationRecovery(
                    ChatUseCase._ground_agent_answer(question, answer, [item]),
                    [item],
                    item_status
                    if item_status in {"completed", "no_data", "needs_clarification"}
                    else "no_data",
                )
        return None

    @staticmethod
    def _structured_score_relations(
        observations: list[dict[str, Any]],
    ) -> list[tuple[EntityRef, EntityRef, int, int]]:
        """Extract winner/loser score relations from deterministic projections.

        Numeric and proper-name membership checks cannot detect a fluent
        inversion such as “马刺以 94–90 赢了尼克斯” when all four tokens are
        present in the observation.  Keep this parser intentionally narrow: it
        reads only completed typed-result lines where a score sits between two
        recognized teams, including the canonical matchup table and series
        aggregate emitted by the deterministic renderer.
        """

        relations: list[tuple[EntityRef, EntityRef, int, int]] = []
        seen: set[tuple[str, str, int, int]] = set()
        for observation in observations:
            if not isinstance(observation, Mapping):
                continue
            if str(observation.get("status", "")).lower() != "completed":
                continue
            if str(observation.get("intent", "")).lower() not in {
                "nba_query",
                "schedule_result",
                "public_reverification",
            }:
                continue
            raw = str(observation.get("answer_markdown") or "")
            for raw_line in raw.splitlines():
                line = re.sub(r"[*_`|]", " ", raw_line)
                line = " ".join(line.split())
                if not line:
                    continue
                teams = [
                    item
                    for item in resolve_entities(line)
                    if item.kind is EntityKind.TEAM
                ]
                unique_teams: list[EntityRef] = []
                for team in teams:
                    if not any(
                        old.canonical_id == team.canonical_id for old in unique_teams
                    ):
                        unique_teams.append(team)
                if len(unique_teams) < 2:
                    continue

                def mention_at(team: EntityRef) -> int:
                    positions = [
                        line.casefold().find(str(alias).casefold())
                        for alias in [team.display_name, *team.aliases]
                        if line.casefold().find(str(alias).casefold()) >= 0
                    ]
                    return min(positions, default=-1)

                positioned = sorted(
                    ((mention_at(team), team) for team in unique_teams),
                    key=lambda item: item[0],
                )
                for score in re.finditer(
                    r"(?<!\d)(\d{1,3})\s*[-–—:：比]\s*(\d{1,3})(?!\d)",
                    line,
                ):
                    left_score, right_score = int(score.group(1)), int(score.group(2))
                    if left_score == right_score:
                        continue
                    before = [item for item in positioned if 0 <= item[0] < score.start()]
                    after = [item for item in positioned if item[0] > score.end()]
                    if not before or not after:
                        continue
                    left_team = before[-1][1]
                    right_team = after[0][1]
                    if left_team.canonical_id == right_team.canonical_id:
                        continue
                    if left_score > right_score:
                        winner, loser = left_team, right_team
                        winner_score, loser_score = left_score, right_score
                    else:
                        winner, loser = right_team, left_team
                        winner_score, loser_score = right_score, left_score
                    key = (
                        winner.canonical_id,
                        loser.canonical_id,
                        winner_score,
                        loser_score,
                    )
                    if key not in seen:
                        seen.add(key)
                        relations.append((winner, loser, winner_score, loser_score))
            # Single-game templates state the matchup and the winning sentence
            # on separate lines ("对阵双方 …" then "X 以 108–104 取胜").
            # Recover that relation without treating arbitrary prose as typed
            # evidence: both teams and the exact winner-first template must be
            # present in the same deterministic observation.
            all_teams: list[EntityRef] = []
            for item in resolve_entities(raw):
                if item.kind is EntityKind.TEAM and not any(
                    old.canonical_id == item.canonical_id for old in all_teams
                ):
                    all_teams.append(item)
            if len(all_teams) == 2:
                plain = re.sub(r"[*_`]", "", raw)
                for winner in all_teams:
                    aliases = sorted(
                        {
                            str(alias).strip()
                            for alias in [winner.display_name, *winner.aliases]
                            if str(alias).strip()
                        },
                        key=len,
                        reverse=True,
                    )
                    winner_pattern = "(?:" + "|".join(
                        re.escape(alias) for alias in aliases
                    ) + ")"
                    match = re.search(
                        rf"{winner_pattern}\s*以\s*(\d{{1,3}})\s*"
                        r"[-–—:：比]\s*(\d{1,3})\s*取胜",
                        plain,
                        re.IGNORECASE,
                    )
                    if match is None:
                        continue
                    winner_score, loser_score = int(match.group(1)), int(match.group(2))
                    if winner_score <= loser_score:
                        continue
                    loser = next(
                        team
                        for team in all_teams
                        if team.canonical_id != winner.canonical_id
                    )
                    key = (
                        winner.canonical_id,
                        loser.canonical_id,
                        winner_score,
                        loser_score,
                    )
                    if key not in seen:
                        seen.add(key)
                        relations.append((winner, loser, winner_score, loser_score))
        return relations

    @staticmethod
    def _answer_inverts_structured_score(
        answer: str,
        observations: list[dict[str, Any]],
    ) -> bool:
        """Reject an Agent sentence that assigns a typed score to the loser."""

        text = re.sub(r"[*_`]", "", str(answer or ""))
        if not text:
            return False
        win_word = r"(?:战胜|击败|取胜|获胜|赢(?:了|下)?)"
        lose_word = r"(?:不敌|负于|输给)"

        def alias_pattern(team: EntityRef) -> str:
            aliases = sorted(
                {
                    str(alias).strip()
                    for alias in [team.display_name, *team.aliases]
                    if str(alias).strip()
                },
                key=len,
                reverse=True,
            )
            return "(?:" + "|".join(re.escape(alias) for alias in aliases) + ")"

        for winner, loser, winner_score, loser_score in (
            ChatUseCase._structured_score_relations(observations)
        ):
            winner_name = alias_pattern(winner)
            loser_name = alias_pattern(loser)
            score = (
                rf"(?:{winner_score}\s*[-–—:：比]\s*{loser_score}|"
                rf"{loser_score}\s*[-–—:：比]\s*{winner_score})"
            )
            for sentence in re.split(r"[。！？!?；;\n]+", text):
                if not re.search(score, sentence) or not re.search(loser_name, sentence):
                    continue
                # Negative statements such as “马刺没有赢” are not inversions.
                if re.search(
                    rf"{loser_name}[^。！？!?；;]{{0,30}}(?:没有|没|未能|并未|不曾){win_word}",
                    sentence,
                    re.IGNORECASE,
                ):
                    continue
                if re.search(
                    rf"{loser_name}[^。！？!?；;]{{0,80}}(?:以\s*)?{score}"
                    rf"[^。！？!?；;]{{0,50}}{win_word}",
                    sentence,
                    re.IGNORECASE,
                ) or re.search(
                    rf"{loser_name}[^。！？!?；;]{{0,80}}{win_word}"
                    rf"[^。！？!?；;]{{0,80}}{winner_name}",
                    sentence,
                    re.IGNORECASE,
                ):
                    return True
                if re.search(
                    rf"{winner_name}[^。！？!?；;]{{0,80}}{lose_word}"
                    rf"[^。！？!?；;]{{0,80}}{loser_name}",
                    sentence,
                    re.IGNORECASE,
                ):
                    return True
        return False

    @staticmethod
    def _normalize_series_award_scope(value: Any) -> str:
        """Repair wording that incorrectly turns a series honor into a game award."""

        text = str(value or "")
        if not text:
            return ""
        game_scope = r"(?:本场(?:比赛)?|这场(?:比赛)?|该场(?:比赛)?|单场|G\s*\d+)"
        verb = r"(?:当选|获选|获评|获得|荣膺|拿下)(?:了)?"
        award = (
            r"(?:(?:总决赛|系列赛)\s*(?:的)?\s*"
            r"(?:MVP|最有价值球员)|FMVP)"
        )

        def replacement(match: re.Match[str]) -> str:
            raw_award = str(match.group("award") or "")
            scope = "系列赛" if "系列赛" in raw_award else "总决赛"
            subject = str(match.groupdict().get("subject") or "").strip()
            # ``本场`` can describe the basis for an eventual series award
            # without claiming that the award belonged to that one game:
            # ``凭借本场表现当选 FMVP`` and ``本场结束后获得 FMVP`` are both
            # legitimate.  In those forms the broad subject capture below
            # sees ``表现``/``结束后…``; leave the temporal/basis wording
            # untouched instead of turning it into ``表现最终当选``.
            if re.search(r"(?:表现|发挥|结束|赛后|终场)", subject):
                return match.group(0)
            return f"{subject}最终当选{scope} MVP"

        # Subject before the game scope: ``布伦森在这场比赛荣膺…``.
        text = re.sub(
            rf"(?:在\s*)?{game_scope}(?:中)?\s*{verb}\s*"
            rf"(?P<award>{award})",
            replacement,
            text,
            flags=re.IGNORECASE,
        )
        # Game scope before the subject: ``本场布伦森当选…`` or
        # ``G5 布伦森获得 FMVP``.  Keep the recognized subject while moving
        # the honor to the series boundary.
        text = re.sub(
            rf"{game_scope}(?:中)?\s*"
            rf"(?P<subject>[\u4e00-\u9fffA-Za-z·.'’\-]{{1,32}})\s*"
            rf"{verb}\s*(?P<award>{award})",
            replacement,
            text,
            flags=re.IGNORECASE,
        )
        return text

    @staticmethod
    def _sanitize_agent_semantic_claims(
        question: str,
        answer: str,
        observations: list[dict[str, Any]],
    ) -> str:
        """Keep an Agent synthesis inside the scope of its factual observations.

        The normal output guard traces numbers and proper names.  Open recaps
        also need a small relation guard: a search headline must not authorize
        an event claim, series honors must not become single-game awards, and
        concrete causal events must occur in an observation summary before the
        answer states that they happened.  This is intentionally sentence-level
        so the Agent's useful score/result prose survives a bad extra clause.
        """

        text = ChatUseCase._normalize_series_award_scope(answer).strip()
        if not text:
            return ""
        text = re.sub(
            r"(?:拿下|赢下|取得)(?:了)?\s*系列赛\s*"
            r"第\s*([一二三四五六七八九十\d]+)\s*场\s*胜利",
            r"赢下系列赛第\1场比赛",
            text,
        )
        text = re.sub(
            r"(以\s*\d+\s*[–—-]\s*\d+\s*)淘汰"
            r"([^，。！？!?；;\n]{1,24}?)(?=夺冠)",
            r"\1击败\2",
            text,
        )

        def walk_text(value: Any) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, Mapping):
                output: list[str] = []
                for item in value.values():
                    output.extend(walk_text(item))
                return output
            if isinstance(value, (list, tuple)):
                output = []
                for item in value:
                    output.extend(walk_text(item))
                return output
            return []

        structured_parts: list[str] = []
        search_parts: list[str] = []
        search_scope_pairs: list[tuple[str, str]] = []
        for item in observations:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("status", "")).lower() != "completed":
                continue
            intent = str(item.get("intent", "")).lower()
            raw = str(item.get("answer_markdown") or "")
            if intent in {"web_search", "nba_news"}:
                # Titles are retrieval labels, not factual support.  Only the
                # bounded summary portion of each search item may authorize a
                # concrete event or honor in public prose.
                for title, summary in ChatUseCase._search_evidence_items(raw):
                    if not summary:
                        continue
                    search_parts.append(summary)
                    # A title cannot authorize any fact, but an explicit G#/date
                    # in it can safely narrow the summary's scope.  This prevents
                    # a contextless G4 snippet from supporting a G5 causal claim.
                    search_scope_pairs.append((title, summary))
            elif intent in {
                "nba_query",
                "schedule_result",
                "public_reverification",
            }:
                structured_parts.append(raw)
                structured_parts.extend(walk_text(item.get("blocks") or []))

        structured_corpus = "\n".join(structured_parts)
        search_corpus = "\n".join(search_parts)
        support_corpus = f"{structured_corpus}\n{search_corpus}"
        if not support_corpus.strip():
            # Direct seam tests and zero-observation conversational turns are
            # validated separately by OutputGuard.  There is no evidence set
            # here against which a semantic relation can be compared.
            return text
        question_text = str(question or "")
        explicit_award_question = bool(
            re.search(r"(?:FMVP|MVP|最有价值球员|什么奖|奖项)", question_text, re.IGNORECASE)
        )
        broad_scope_question = bool(
            re.search(
                r"(?:这轮|整轮|整个|整届|系列赛|赛季整体|季后赛整体|"
                r"场均|平均每场)",
                question_text,
                re.IGNORECASE,
            )
        )
        single_game_explanation = bool(
            is_game_recap_question(question_text) and not broad_scope_question
        )
        explicit_history_context_question = bool(
            re.search(
                r"(?:历史|意义|纪录|冠军荒|等待了多久|时隔(?:多少|几年)|"
                r"上次夺冠|队史)",
                question_text,
                re.IGNORECASE,
            )
        )
        explicit_score_total_question = bool(
            re.search(r"(?:分差|总得分|合计得分|一共多少分)", question_text)
        )
        award_re = re.compile(
            r"(?:(?:总决赛|系列赛)\s*(?:的)?\s*"
            r"(?:MVP|最有价值球员)|FMVP)",
            re.IGNORECASE,
        )
        award_supported = bool(award_re.search(support_corpus))
        aggregate_re = re.compile(
            r"(?:整届|整个|本届|本赛季|赛季|季后赛|系列赛|总决赛)"
            r"[^。！？!?；;\n]{0,36}(?:场均|平均每场)",
            re.IGNORECASE,
        )
        aggregate_clause_re = re.compile(
            r"(?:整届|整个|本届|本赛季|赛季|季后赛|系列赛|总决赛)"
            r"[^。！？!?；;，,\n]{0,48}(?:场均|平均每场)"
            r"[^。！？!?；;，,\n]{0,64}",
            re.IGNORECASE,
        )
        unrelated_history_re = re.compile(
            r"(?:"
            r"(?:终结|结束|打破)[^。！？!?；;\n]{0,56}"
            r"(?:\d+\s*年|多年|时隔)[^。！？!?；;\n]{0,40}"
            r"(?:冠军荒|等待|纪录)|"
            r"(?:终结|结束|打破)[^。！？!?；;\n]{0,56}"
            r"(?:冠军荒|冠军空缺|冠军等待)|"
            r"自\s*(?:19|20)\d{2}\s*年(?:以来)?"
            r"[^。！？!?；;\n]{0,56}(?:首次|再度|夺冠|登顶)|"
            r"(?:时隔|暌违)\s*\d+\s*年[^。！？!?；;\n]{0,56}"
            r"(?:夺冠|捧杯|总冠军|冠军)|"
            r"(?:仅次于|追平|超越)[^。！？!?；;\n]{0,72}"
            r"(?:历史|队史|联盟|NBA)[^。！？!?；;\n]{0,40}|"
            r"(?:历史|队史|联盟|NBA)[^。！？!?；;\n]{0,72}"
            r"(?:第[一二三四五六七八九十\d]+|纪录|最高|最久)"
            r")",
            re.IGNORECASE,
        )
        unasked_score_total_clause_re = re.compile(
            r"(?:^|[；;，,])\s*"
            r"(?:(?:这|本|该)场(?:比赛)?\s*[：:]?\s*)?"
            r"(?:总得分|合计得分|两队合计|"
            r"总比分(?=\s*(?:\*\*|__)?\s*\d{3}))"
            r"\s*(?:\*\*|__)?\s*\d{2,3}\s*(?:\*\*|__)?\s*(?:分)?",
            re.IGNORECASE,
        )
        award_action = (
            r"(?:最终)?(?:当选|获选|获评|获得|荣膺|拿下)(?:了)?\s*"
            r"(?:(?:总决赛|系列赛)\s*(?:的)?\s*"
            r"(?:MVP|最有价值球员)|FMVP)"
        )
        causal_re = re.compile(
            r"(?:靠|凭借|因为|由于|得益于|关键在于|决定胜负|"
            r"帮助[^。！？!?；;\n]{0,30}(?:赢|取胜)|取胜原因|胜因|"
            r"锁定(?:胜局|比赛)|奠定胜局|胜在|是(?:取胜)?关键|"
            r"是制胜点|是胜负手)",
            re.IGNORECASE,
        )
        hedge_re = re.compile(
            r"(?:可能|或许|大概率|一般|通常|建议|可以考虑|理论上|如果)",
            re.IGNORECASE,
        )
        concepts: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
            (
                re.compile(r"(?:关键|进攻|防守)?篮板|篮板保护|二次进攻"),
                re.compile(r"关键篮板|篮板(?:球)?(?:优势|占优)|进攻篮板|篮板保护|二次进攻"),
            ),
            (
                re.compile(r"(?:关键|连续)?(?:封盖|盖帽)"),
                re.compile(r"(?:关键|连续|完成|送出)[^。！？!?；;\n]{0,8}(?:封盖|盖帽)"),
            ),
            (
                re.compile(r"(?:关键|连续)?(?:抢断|断球)"),
                re.compile(r"(?:关键|连续|完成)[^。！？!?；;\n]{0,8}(?:抢断|断球)|(?:抢断|断球)[^。！？!?；;\n]{0,20}(?:锁定|反超|守住)"),
            ),
            (
                re.compile(r"失误"),
                re.compile(r"失误(?:过多|控制|问题)|迫使[^。！？!?；;\n]{0,12}失误"),
            ),
            (
                re.compile(r"三分|外线"),
                re.compile(r"三分(?:火力|命中|优势|手感)|外线(?:火力|命中|优势|手感)"),
            ),
            (
                re.compile(r"罚球"),
                re.compile(r"罚球(?:命中|线|稳定|优势)"),
            ),
            (re.compile(r"禁区|内线"), re.compile(r"禁区|内线")),
            (re.compile(r"挡拆"), re.compile(r"挡拆")),
            (re.compile(r"转换进攻|快攻"), re.compile(r"转换进攻|快攻")),
            (re.compile(r"防守|限制"), re.compile(r"防守|限制")),
        )

        chinese_game_numbers = {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
        }

        def game_number_mentions(value: str) -> list[tuple[int, int, int]]:
            mentions: list[tuple[int, int, int]] = []
            for match in re.finditer(
                r"(?:(?<![A-Za-z0-9])G\s*([1-7])(?!\d)|"
                r"第\s*([一二三四五六七1-7])\s*场)",
                str(value or ""),
                re.IGNORECASE,
            ):
                raw = match.group(1) or match.group(2)
                number = (
                    int(raw)
                    if str(raw).isdigit()
                    else chinese_game_numbers[str(raw)]
                )
                mentions.append((match.start(), match.end(), number))
            return mentions

        def game_numbers(value: str) -> set[int]:
            return {number for _start, _end, number in game_number_mentions(value)}

        def game_date_mentions(value: str) -> list[tuple[int, int, str]]:
            dates: list[tuple[int, int, str]] = []
            for match in re.finditer(
                r"(?<!\d)(20\d{2})\s*(?:[-/.]|年)\s*(\d{1,2})"
                r"\s*(?:[-/.]|月)\s*(\d{1,2})\s*日?",
                str(value or ""),
            ):
                year, month, day = (int(part) for part in match.groups())
                try:
                    date = datetime(year, month, day).date().isoformat()
                except ValueError:
                    continue
                dates.append((match.start(), match.end(), date))
            return dates

        def game_dates(value: str) -> set[str]:
            return {date for _start, _end, date in game_date_mentions(value)}

        target_game_numbers = game_numbers(question_text)
        if not target_game_numbers:
            structured_game_numbers = game_numbers(structured_corpus)
            if len(structured_game_numbers) == 1:
                target_game_numbers = structured_game_numbers
        target_game_dates = game_dates(question_text)
        if not target_game_dates:
            structured_game_dates = game_dates(structured_corpus)
            if len(structured_game_dates) == 1:
                target_game_dates = structured_game_dates

        score_relations = ChatUseCase._structured_score_relations(observations)
        target_winner_ids = {
            winner.canonical_id for winner, _loser, _winner_score, _loser_score in score_relations
        }
        if len(target_winner_ids) != 1:
            target_winner_ids = set()
        if not target_winner_ids:
            question_teams = [
                entity
                for entity in resolve_entities(question_text)
                if entity.kind is EntityKind.TEAM
            ]
            if len(question_teams) == 1:
                target_winner_ids = {question_teams[0].canonical_id}

        support_units: list[tuple[str, str, str]] = [
            ("structured", unit.strip(), "")
            for unit in re.split(r"[。！？!?；;\n]+", structured_corpus)
            if unit.strip()
        ]
        for title, summary in search_scope_pairs:
            support_units.extend(
                ("search", unit.strip(), title)
                for unit in re.split(r"[。！？!?；;\n]+", summary)
                if unit.strip()
            )

        def support_is_negated(sentence: str, match: re.Match[str]) -> bool:
            before = sentence[max(0, match.start() - 48) : match.start()]
            after = sentence[match.end() : match.end() + 28]
            return bool(
                re.search(
                    r"(?:没有(?:任何)?(?:证据(?:表明|显示)?)?|并未|未曾|未能|"
                    r"并非|不是|无|缺少|无法(?:确认)?|不能(?:确认)?|否认)"
                    r"[^。！？!?；;]{0,36}$",
                    before,
                    re.IGNORECASE,
                )
                or re.match(
                    r"[^。！？!?；;]{0,24}(?:并未发生|没有发生|并不存在|"
                    r"无法确认|不能确认|尚未确认|并非|并不是|不是|"
                    r"不构成|与胜负无关)",
                    after,
                    re.IGNORECASE,
                )
            )

        def explicit_support_team(sentence: str, detail_at: int) -> str | None:
            entities = [
                entity
                for entity in resolve_entities(sentence)
                if entity.kind is EntityKind.TEAM
            ]
            unique = list({entity.canonical_id: entity for entity in entities}.values())
            if len(unique) == 1:
                return unique[0].canonical_id
            if not unique:
                return None
            prefix = sentence[:detail_at]
            candidates: list[tuple[int, str]] = []
            for entity in unique:
                for alias in (entity.display_name, *entity.aliases):
                    for mention in re.finditer(
                        re.escape(str(alias)), prefix, re.IGNORECASE
                    ):
                        trailing = prefix[mention.end() :]
                        if len(trailing) <= 28 and (
                            re.search(
                                r"(?:靠|依靠|凭借|通过|完成|送出|抢下|利用)",
                                trailing,
                                re.IGNORECASE,
                            )
                            or re.fullmatch(r"\s*的\s*", trailing)
                        ):
                            candidates.append((mention.start(), entity.canonical_id))
            return max(candidates)[1] if candidates else None

        def concept_support_sources(
            support_re: re.Pattern[str],
        ) -> set[str]:
            sources: set[str] = set()
            for source, sentence, scope_hint in support_units:
                sentence_games = game_numbers(sentence)
                sentence_dates = game_dates(sentence)
                for match in support_re.finditer(sentence):
                    scope_games = game_numbers(scope_hint)
                    if (
                        target_game_numbers
                        and scope_games
                        and target_game_numbers.isdisjoint(scope_games)
                    ):
                        continue
                    scope_dates = game_dates(scope_hint)
                    if (
                        target_game_dates
                        and scope_dates
                        and target_game_dates.isdisjoint(scope_dates)
                    ):
                        continue
                    game_mentions = game_number_mentions(sentence)
                    prior_games = [
                        mention for mention in game_mentions if mention[1] <= match.start()
                    ]
                    later_games = [
                        mention
                        for mention in game_mentions
                        if mention[0] >= match.end() and mention[0] - match.end() <= 32
                    ]
                    detail_game = (
                        prior_games[-1][2]
                        if prior_games
                        else later_games[0][2]
                        if later_games
                        else None
                    )
                    if target_game_numbers and (
                        (
                            detail_game is not None
                            and detail_game not in target_game_numbers
                        )
                        or (
                            detail_game is None
                            and sentence_games
                            and target_game_numbers.isdisjoint(sentence_games)
                        )
                    ):
                        continue
                    date_mentions = game_date_mentions(sentence)
                    prior_dates = [
                        mention for mention in date_mentions if mention[1] <= match.start()
                    ]
                    later_dates = [
                        mention
                        for mention in date_mentions
                        if mention[0] >= match.end() and mention[0] - match.end() <= 36
                    ]
                    detail_date = (
                        prior_dates[-1][2]
                        if prior_dates
                        else later_dates[0][2]
                        if later_dates
                        else None
                    )
                    if target_game_dates and (
                        (
                            detail_date is not None
                            and detail_date not in target_game_dates
                        )
                        or (
                            detail_date is None
                            and sentence_dates
                            and target_game_dates.isdisjoint(sentence_dates)
                        )
                    ):
                        continue
                    if support_is_negated(sentence, match):
                        continue
                    support_team = explicit_support_team(sentence, match.start())
                    if (
                        target_winner_ids
                        and support_team is not None
                        and support_team not in target_winner_ids
                    ):
                        continue
                    sources.add(source)
                    break
            return sources

        def causal_is_qualified(segment: str, match: re.Match[str]) -> bool:
            # A hedge applies only inside the causal clause itself.  Earlier
            # prose such as ``可能还有其他因素，但事实是靠…`` must not bless
            # the later unqualified claim.
            clause_start = max(
                segment.rfind(delimiter, 0, match.start())
                for delimiter in ("，", ",", "；", ";", "。", "!", "！", "?", "？")
            )
            prefix = segment[clause_start + 1 : match.start()]
            if re.search(
                r"(?:但|不过|然而)\s*(?:事实|实际|可以确认|确定)?"
                r"\s*(?:是|上)?\s*$",
                prefix,
            ):
                return False
            return bool(hedge_re.search(prefix[-28:]))

        def strip_award_clause(segment: str) -> str:
            # Remove the action first so a valid single-game statistic in the
            # same comma clause survives (``45 分并当选 FMVP``).
            cleaned = re.sub(
                rf"(?:并|且|同时|也)\s*{award_action}",
                "",
                segment,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                rf"[，,；;]\s*[^，,；;。！？!?\n]{{0,48}}?{award_action}",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
            if award_re.search(cleaned):
                # The remaining segment is an award-only statement.  Dropping
                # it is safer than leaving a dangling player name; the score
                # and player-line sentence is a separate segment and survives.
                return ""
            return tidy_pruned_segment(cleaned)

        def tidy_pruned_segment(segment: str) -> str:
            cleaned = re.sub(
                r"(?:并且|而且|同时|以及|且|并)\s*(?=[，,；;。！？!?])",
                "",
                segment,
            )
            cleaned = re.sub(
                r"^[，,；;\s]*(?:但|不过|然而|而|同时)\s*",
                "",
                cleaned,
            )
            cleaned = re.sub(
                r"[，,；;]\s*(?:但|不过|然而|而|同时)?\s*([。！？!?])",
                r"\1",
                cleaned,
            )
            cleaned = re.sub(r"[，,；;]\s*([，,；;])", r"\1", cleaned)
            return cleaned.strip()

        def strip_aggregate_clause(segment: str) -> str:
            """Remove a long-scope average without discarding a game stat."""

            return tidy_pruned_segment(aggregate_clause_re.sub("", segment))

        def keep_supported_prefix(segment: str, detail_at: int) -> str:
            """Keep a score/result clause before one unsupported detail."""

            boundary = max(
                segment.rfind(delimiter, 0, detail_at)
                for delimiter in ("，", ",", "：", ":", "；", ";")
            )
            if boundary < 0:
                return ""
            prefix = segment[:boundary].rstrip()
            plain = re.sub(r"[*_`#>\-]", "", prefix)
            if not re.search(
                r"(?:\d|战胜|击败|取胜|获胜|赢(?:了|下)?|比分|领先|得到)",
                plain,
                re.IGNORECASE,
            ):
                return ""
            terminal = "。" if re.search(r"[。.!！]$", segment.strip()) else ""
            return prefix + terminal

        def remove_unsupported_concepts(
            segment: str,
            patterns: list[re.Pattern[str]],
        ) -> str:
            cleaned = segment
            for pattern in patterns:
                wrapped = rf"(?:{pattern.pattern})"
                cleaned = re.sub(
                    rf"(?:、|和|以及|及)\s*{wrapped}",
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                )
                cleaned = re.sub(
                    rf"{wrapped}\s*(?:、|和|以及|及)",
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                )
                cleaned = re.sub(
                    wrapped,
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                )
            cleaned = re.sub(r"(?:、|和|以及|及){2,}", "", cleaned)
            cleaned = re.sub(r"靠\s*(?:、|和|以及|及)", "靠", cleaned)
            return cleaned

        output: list[str] = []
        for segment in re.split(r"(?<=[。！？!?；;])", text):
            if not segment:
                continue
            plain_segment = re.sub(r"[*_`#>\-]", "", segment).strip()
            if (
                single_game_explanation
                and not explicit_score_total_question
                and re.fullmatch(
                    r"(?:这|本|该)场比赛\s*[：:]\s*(?:分差|总得分)"
                    r"[^。！？!?；;]{0,80}[。！？!?；;]?",
                    plain_segment,
                    re.IGNORECASE,
                )
            ):
                continue
            if single_game_explanation and not explicit_score_total_question:
                pruned_segment = unasked_score_total_clause_re.sub("", segment)
                if pruned_segment != segment:
                    segment = tidy_pruned_segment(pruned_segment)
                    if not segment:
                        continue
            if single_game_explanation and target_game_numbers:
                mismatched_games = [
                    mention
                    for mention in game_number_mentions(segment)
                    if mention[2] not in target_game_numbers
                ]
                if mismatched_games:
                    prefix = keep_supported_prefix(segment, mismatched_games[0][0])
                    if prefix:
                        output.append(prefix)
                    continue
            if single_game_explanation and target_game_dates:
                mismatched_dates = [
                    mention
                    for mention in game_date_mentions(segment)
                    if mention[2] not in target_game_dates
                ]
                if mismatched_dates:
                    prefix = keep_supported_prefix(segment, mismatched_dates[0][0])
                    if prefix:
                        output.append(prefix)
                    continue
            history_match = unrelated_history_re.search(segment)
            if (
                single_game_explanation
                and not explicit_history_context_question
                and history_match
            ):
                prefix = keep_supported_prefix(segment, history_match.start())
                if prefix:
                    output.append(prefix)
                continue
            aggregate_match = aggregate_re.search(segment)
            if single_game_explanation and aggregate_match:
                segment = strip_aggregate_clause(segment)
                if not segment:
                    continue
            if award_re.search(segment):
                if (
                    not award_supported
                    or (single_game_explanation and not explicit_award_question)
                ):
                    segment = strip_award_clause(segment)
                    if not segment.strip():
                        continue
            if (
                single_game_explanation
                and (causal_match := causal_re.search(segment))
                and not causal_is_qualified(segment, causal_match)
            ):
                mentioned = [
                    (claim_re, support_re)
                    for claim_re, support_re in concepts
                    if claim_re.search(segment)
                ]
                unsupported = [
                    claim_re
                    for claim_re, support_re in mentioned
                    if not concept_support_sources(support_re)
                ]
                if unsupported:
                    if len(unsupported) == len(mentioned):
                        prefix = keep_supported_prefix(
                            segment,
                            causal_match.start() if causal_match else 0,
                        )
                        if prefix:
                            output.append(prefix)
                        continue
                    segment = remove_unsupported_concepts(segment, unsupported)
            output.append(segment)
        return "".join(output).strip()

    @staticmethod
    def _ground_agent_answer(
        question: str,
        answer: str,
        observations: list[dict[str, Any]],
    ) -> str:
        """Return the public projection of a grounded Agent answer.

        Grounding may use provider-shaped search observations internally, but
        callers of this seam are public-answer paths.  Apply the same
        search-section projection here as at the HTTP boundary so recovery,
        tests, and future integrations cannot accidentally render internal
        headings or caveats.
        """

        grounded = ChatUseCase._ground_agent_answer_impl(
            question,
            answer,
            observations,
        )
        grounded = ChatUseCase._sanitize_agent_semantic_claims(
            question,
            grounded,
            observations,
        )
        return ChatUseCase._strip_public_search_sections(grounded)

    @staticmethod
    def _ground_agent_answer_impl(
        question: str,
        answer: str,
        observations: list[dict[str, Any]],
    ) -> str:
        """Project objective NBA observations through server-owned wording.

        The Agent owns intent understanding and tool selection.  It does not
        own score/team, venue, statistic or play-event relations: fluent model
        paraphrases can invert a winner or turn a free throw/terminal marker
        into a field goal while reusing only observed names and numbers.  For
        non-analytical ``nba_query`` turns, return the deterministic sanitized
        observation verbatim.  Analytical turns may still use the model, while
        the normal output guard enforces numbers, safety and implementation
        secrecy.
        """

        analytical = ChatUseCase._is_open_ended_agent_synthesis(question) or bool(
            re.search(
                r"(?:为什么|为何|原因|战术|挡拆|防守策略|怎么限制|如何限制|复盘|"
                r"限制|如何防|怎么防|布置防守|关键转折|表现如何|评价|分析|解读)",
                str(question or ""),
                re.IGNORECASE,
            )
        )
        news_question = bool(
            re.search(
                r"(?:新闻|消息|资讯|报道|动态|近况|头条|背景|\bnews\b|\bheadline\b)",
                str(question or ""),
                re.IGNORECASE,
            )
        )

        def clean_agent_prose(value: str) -> str:
            """Remove internal workflow narration from model-authored prose.

            The model is instructed not to mention tools, retries, or provider
            details, but a fluent answer can still leak phrases such as
            ``我尝试了新闻工具三次``.  Those details are neither useful to a
            fan nor part of the public contract.  Strip only complete process
            clauses/sentences and retain the surrounding evidence summary.
            Provider/framework names are neutralised as a second defence;
            objective facts continue to be grounded by the observation path
            and OutputGuard below.
            """

            text = neutralize_external_internal_names(str(value or "")).strip()
            if not text:
                return text
            removed_internal_process = False
            # Drop clauses that explicitly describe internal calls, retries,
            # validation failures, or switching between tools.  Delimiters
            # include semicolons/colons because search answers often introduce
            # a following bullet list with a colon rather than a full stop.
            process_clause = re.compile(
                r"(?:^|(?<=[。！？!?；;\n]))\s*(?:"
                r"(?:[^。！？!?；;\n]{0,80}?"
                r"(?:我|助手|系统|服务|本轮|当前(?:回答|任务))"
                r"[^。！？!?；;\n]{0,40})"
                r"(?:尝试|调用|使用|切换|改用|重试|返回|失败|超时|校验)"
                r"[^。！？!?；;\n]{0,180}?(?:工具|API|接口|模型|搜索)|"
                r"(?:尝试|调用|切换|改用|重试)"
                r"[^。！？!?；;\n]{0,180}?(?:工具|API|接口|模型|搜索)"
                r")[^。！？!?；;\n]{0,180}?"
                r"(?:[。！？!?；;]|：|:|$)",
                re.IGNORECASE,
            )
            previous = None
            while text != previous:
                previous = text
                text = process_clause.sub("", text).strip()
                if text != previous:
                    removed_internal_process = True
            # Hermes may emit a terse runtime status without the usual
            # "调用/尝试工具" sentence (for example, when the iteration cap
            # is reached).  Remove that complete clause before the answer is
            # validated; the detector below will then recover from the
            # server-owned observations instead of showing this implementation
            # detail to the user.
            internal_status_clause = re.compile(
                r"(?:^|(?<=[。！？!?；;\n]))\s*"
                r"(?:工具预算|调用预算|迭代预算|工具调用(?:次数)?(?:已)?(?:达到|超过)(?:上限|限制)|"
                r"预算(?:已)?(?:用尽|耗尽)|(?:输出|生成)(?:内容)?(?:已)?(?:被)?截断|"
                r"输出不完整|无法继续(?:调用|生成)|"
                r"(?:系统|服务|本轮|当前(?:回答|任务))[^。！？!?；;\n]{0,40}内部(?:执行|流程))"
                r"[^。！？!?；;\n]{0,220}(?:[。！？!?；;]|(?=\n)|$)",
                re.IGNORECASE,
            )
            previous = None
            while text != previous:
                previous = text
                text = internal_status_clause.sub("", text).strip()
                if text != previous:
                    removed_internal_process = True
            # If the removed lead-in introduced a list, restore a neutral
            # heading so the remaining bullets read naturally.
            text = re.sub(
                r"^\s*(?:(?:随后|然后|接着)\s*)?"
                r"(?:(?:改用|使用|切换到)\s*)?"
                r"(?:(?:公开)?(?:网页|资料)?(?:检索|搜索)|"
                r"(?:拿到|找到|整理出|得到)(?:以下)?(?:公开)?"
                r"(?:网页|资料)?(?:线索|信息)?)"
                r"[^：\n]{0,140}\s*[:：]",
                "公开资料线索：",
                text,
                flags=re.IGNORECASE,
            )
            if removed_internal_process and re.match(
                r"^(?:[-*]|\d+[.)])\s+", text
            ):
                text = "公开资料线索：\n" + text
            # Avoid leaving a dangling leading punctuation after clause
            # removal.  Do not touch punctuation within article content.
            return text.lstrip("，,；;：:").strip()

        # Repair obvious truncation before the normal evidence projection.  A
        # model can stop at a heading after tools have already completed; in
        # that case the observation recovery below is more useful than
        # accepting a dangling section or exposing a budget status.
        initially_cleaned = clean_agent_prose(answer)
        if ChatUseCase._answer_inverts_structured_score(
            initially_cleaned, observations
        ):
            # Keep the turn on the Agent route but project the server-owned
            # typed result when the generated prose reverses a winner/score
            # relation.  This is rarer and narrower than replacing every
            # open-ended answer with a deterministic table.
            for item in reversed(observations):
                if (
                    isinstance(item, Mapping)
                    and str(item.get("status", "")).lower() == "completed"
                    and str(item.get("intent", "")).lower()
                    in {"nba_query", "schedule_result", "public_reverification"}
                ):
                    grounded = str(item.get("answer_markdown") or "").strip()
                    if grounded:
                        return grounded
        if ChatUseCase._looks_like_incomplete_agent_answer(
            initially_cleaned, question
        ):
            recovered = ChatUseCase._recover_observation_answer(question, observations)
            if recovered is not None:
                return recovered.markdown
            answer = initially_cleaned

        for item in reversed(observations):
            if not isinstance(item, Mapping):
                continue
            if str(item.get("intent", "")).lower() != "public_reverification":
                continue
            if str(item.get("status", "")).lower() not in {
                "completed",
                "no_data",
                "needs_clarification",
            }:
                continue
            grounded = str(item.get("answer_markdown") or "").strip()
            if grounded:
                return clean_agent_prose(grounded)

        completed_search = [
            item
            for item in observations
            if isinstance(item, Mapping)
            and str(item.get("intent", "")).lower() in {"web_search", "nba_news"}
            and str(item.get("status", "")).lower() == "completed"
            and str(item.get("answer_markdown") or "").strip()
        ]
        completed_structured = any(
            isinstance(item, Mapping)
            and str(item.get("intent", "")).lower()
            in {"nba_query", "schedule_result", "public_reverification"}
            and str(item.get("status", "")).lower() == "completed"
            for item in observations
        )

        # A completed Hermes synthesis is the preferred public answer for an
        # open-ended turn.  Search observations are grounding only; they must
        # not cause the application to replace a coherent answer with a
        # provider-shaped result list or a deterministic "no data" template.
        # Keep this check before the search-only/news recovery branches below.
        # The final API boundary applies the same projection once more, so a
        # model that appended a search heading cannot leak it to the UI.
        composed = clean_agent_prose(answer)
        schedule_projection = bool(
            analytical
            and re.search(r"(?:^|\n)\s*(?:北京时间|NBA)\s*[^\n]{0,80}赛程", composed)
        )
        if (
            composed
            and completed_search
            and (analytical or news_question or not completed_structured)
            and not ChatUseCase._looks_like_incomplete_agent_answer(
                composed, question
            )
            and not ChatUseCase._looks_like_uncomposed_search(composed)
            and not ChatUseCase._looks_like_failed_search_answer(composed)
            and not schedule_projection
        ):
            return ChatUseCase._neutralize_mixed_evidence_labels(composed)

        if completed_search and not completed_structured:
            if (
                composed
                and not ChatUseCase._looks_like_uncomposed_search(composed)
                and not ChatUseCase._looks_like_failed_search_answer(composed)
            ):
                compact = ChatUseCase._compact_news_answer(composed)
                if compact:
                    return compact
            return ChatUseCase._synthesize_search_answer(
                question,
                completed_search[-1].get("answer_markdown"),
            )
        # For news/background turns the model is expected to synthesize the
        # search context. Returning the raw observation here produced a long
        # article dump and made the Agent look like a pass-through proxy.
        # Objective score/stat/PBP turns remain grounded verbatim below.
        if news_question and any(
            isinstance(item, Mapping)
            and str(item.get("intent", "")).lower()
            in {"nba_query", "web_search", "nba_news"}
            and str(item.get("status", "")).lower() == "completed"
            for item in observations
        ):
            composed_news = clean_agent_prose(answer)
            if (
                ChatUseCase._looks_like_uncomposed_search(composed_news)
                or ChatUseCase._looks_like_failed_search_answer(composed_news)
            ):
                return ChatUseCase._synthesize_search_answer(
                    question,
                    composed_news,
                )
            return ChatUseCase._compact_news_answer(composed_news)

        # A clear recap question must not retain a model clarification merely
        # because the typed lookup missed a field.  When a web observation is
        # available, rebuild the answer from the structured limitation plus
        # the bounded public-report synthesis.
        structured_miss = any(
            isinstance(item, Mapping)
            and str(item.get("intent", "")).lower()
            in {"nba_query", "schedule_result"}
            and str(item.get("status", "")).lower()
            in {"no_data", "needs_clarification"}
            and ChatUseCase._looks_like_failed_search_answer(
                item.get("answer_markdown")
            )
            for item in observations
        )
        structured_schedule_only = (
            analytical
            and completed_search
            and any(
                isinstance(item, Mapping)
                and str(item.get("intent", "")).lower() == "schedule_result"
                and str(item.get("status", "")).lower()
                in {"completed", "no_data", "needs_clarification"}
                for item in observations
            )
            and not any(
                isinstance(item, Mapping)
                and str(item.get("intent", "")).lower() == "nba_query"
                and str(item.get("status", "")).lower() == "completed"
                for item in observations
            )
        )

        # A complete Hermes synthesis is the best answer for an open-ended
        # explanation.  A structured miss only means that the typed record did
        # not contain the requested field; it does not invalidate a relevant
        # model synthesis grounded in the completed search observation.  The
        # old ordering entered ``_recover_observation_answer`` first and
        # replaced that prose with a generic “补充线索” list, which is why
        # questions such as “最近一场尼克斯赢了吗，怎么赢的” looked like a
        # fallback even though Hermes had already answered them.
        composed = clean_agent_prose(answer)
        schedule_projection = bool(
            structured_schedule_only
            and re.search(r"(?:^|\n)\s*(?:北京时间|NBA)\s*[^\n]{0,80}赛程", composed)
        )
        if (
            analytical
            and completed_search
            and composed
            and not ChatUseCase._looks_like_incomplete_agent_answer(
                composed, question
            )
            and not ChatUseCase._looks_like_uncomposed_search(composed)
            and not ChatUseCase._looks_like_failed_search_answer(composed)
            and not schedule_projection
        ):
            return ChatUseCase._neutralize_mixed_evidence_labels(composed)

        if analytical and completed_search and (structured_miss or structured_schedule_only):
            recovered = ChatUseCase._recover_observation_answer(question, observations)
            if recovered is not None:
                return recovered.markdown

        # Weaker model/provider combinations occasionally return the bounded
        # search observation itself after a tactical tool call.  Preserve the
        # evidence, but add an explicit analytical frame so the UI does not
        # present a bare article list as if it answered “怎么防/为什么赢”.
        if analytical and ChatUseCase._looks_like_uncomposed_search(answer):
            framed = ChatUseCase._frame_search_analysis(answer, question)
            if framed:
                cleaned_frame = clean_agent_prose(framed)
                if completed_search:
                    cleaned_frame = ChatUseCase._neutralize_mixed_evidence_labels(
                        cleaned_frame
                    )
                return cleaned_frame

        # A typed query can recover from a structured miss by appending a
        # completed, sanitised public-search observation.  That combined
        # observation is useful grounding, but it is no longer a deterministic
        # score/stat/PBP answer.  Keep Hermes' concise synthesis when it
        # actually composed one; otherwise retain the bounded evidence list as
        # a safe fallback.  Previously the generic objective-answer guard
        # always replaced the synthesis with the raw list.
        mixed_search_observation = any(
            isinstance(item, Mapping)
            and str(item.get("intent", "")).lower() == "nba_query"
            and str(item.get("status", "")).lower() == "completed"
            and "公开资料线索" in str(item.get("answer_markdown") or "")
            for item in observations
        )
        if not analytical and mixed_search_observation:
            composed = clean_agent_prose(answer)
            if (
                composed
                and not ChatUseCase._looks_like_uncomposed_search(composed)
                and not ChatUseCase._looks_like_failed_search_answer(composed)
            ):
                compact = ChatUseCase._compact_news_answer(composed)
                if compact:
                    return compact
            synthesized = ChatUseCase._synthesize_search_answer(question, composed)
            if synthesized:
                return synthesized

        if not analytical:
            # Prefer a completed structured answer, but do not let an empty
            # ``nba_query`` result mask a completed public-search observation
            # from the same Agent turn.  The latter is explicitly labelled as
            # background/partial by the tool and is more useful than asking
            # the user to restate an already clear question.
            candidates = [
                item
                for item in observations
                if isinstance(item, Mapping)
                and str(item.get("intent", "")).lower() == "nba_query"
                and str(item.get("status", "")).lower()
                in {"completed", "no_data", "needs_clarification"}
            ]
            candidates.sort(
                key=lambda item: (
                    str(item.get("status", "")).lower() == "completed",
                    str(item.get("status", "")).lower() != "no_data",
                ),
                reverse=True,
            )
            if not candidates or all(
                str(item.get("status", "")).lower() == "no_data"
                for item in candidates
            ):
                candidates.extend(
                    item
                    for item in reversed(observations)
                    if isinstance(item, Mapping)
                    and str(item.get("intent", "")).lower()
                    in {"web_search", "nba_news"}
                    and str(item.get("status", "")).lower() == "completed"
                )
            for item in candidates:
                if not isinstance(item, Mapping):
                    continue
                grounded = str(item.get("answer_markdown") or "").strip()
                if grounded:
                    if str(item.get("intent", "")).lower() in {"web_search", "nba_news"}:
                        grounded = ChatUseCase._compact_search_markdown(grounded)
                    return clean_agent_prose(grounded)

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
        cleaned_answer = clean_agent_prose(answer)
        if completed_search and completed_structured:
            # A fluent synthesis may accidentally place search-only details
            # under an “already verified facts” heading.  Mixed evidence is
            # partial by contract, so neutralise that heading while keeping
            # the Agent's prose and the genuinely verified score/stat content.
            cleaned_answer = ChatUseCase._neutralize_mixed_evidence_labels(
                cleaned_answer
            )
        return cleaned_answer

    @staticmethod
    def _neutralize_mixed_evidence_labels(value: str) -> str:
        """Keep search-derived details out of verified-fact headings."""

        text = re.sub(
            r"(?m)^(\s*)(?:\*\*)?"
            r"(?:已核验的?硬事实|已核验事实|确定性事实)"
            r"(?:（结构化数据）)?(?:\*\*)?\s*(?:[：:]\s*)?",
            r"\1**综合判断**\n",
            str(value or ""),
        )
        return re.sub(
            r"当前能核验的硬事实",
            "当前可以确认的信息",
            text,
        )

    @staticmethod
    def _is_open_ended_agent_synthesis(question: str) -> bool:
        """Identify prompts where a concise Agent-authored synthesis is useful.

        These phrasings ask for an overview, explanation or interpretation,
        not one isolated objective field.  Objective score/stat/PBP/location/
        coach/duration wording is deliberately absent and continues through
        deterministic grounding.
        """

        text = str(question or "")
        if (
            is_game_recap_question(text)
            or is_contextual_series_selection_question(text)
            or is_subjective_comparison_question(text)
            or bool(
                re.search(
                    r"(?:概括|简介|介绍|讲讲|聊聊|说说|复盘|回顾|"
                    r"比赛过程|怎么个过程|怎么打的|情况怎样|啥情况|"
                    r"怎么看|分析|解读|为什么|为何|原因|战术)",
                    text,
                    re.IGNORECASE,
                )
            )
        ):
            return True
        team_ids = {
            item.canonical_id
            for item in resolve_entities(text)
            if item.kind is EntityKind.TEAM
        }
        if len(team_ids) < 2:
            return False
        # A bare matchup is an invitation to summarize the series/context, not
        # a request for one isolated field.  Explicit score, winner, statistic,
        # metadata, game-number and play-by-play wording stays deterministic.
        return not bool(
            re.search(
                r"(?:比分|大比分|赛果|结果|谁赢|赢了吗|胜负|得分|篮板|助攻|"
                r"命中率|三分|出场|场馆|球馆|地点|教练|时长|多久|"
                r"最后|关键回合|逐回合|回放|\bG\s*\d+\b|第[一二三四五六七八九十\d]+场)",
                text,
                re.IGNORECASE,
            )
        )

    def _open_ended_scope_is_resolved(self, question: str, context: Any) -> bool:
        """Return whether an analytical turn is clear enough to avoid clarification."""

        try:
            parsed = IntentParser(
                clock=self.clock,
                input_timezone=str(getattr(context, "timezone", "Asia/Shanghai")),
                include_fixture_games=self._fixture_game_aliases_enabled,
            ).parse(question, context)
        except (TypeError, ValueError):
            return False
        return not parsed.missing_slots and not parsed.ambiguity_reasons

    @staticmethod
    def _open_ended_unavailable_answer(question: str) -> str:
        """Return a topic-specific limitation without routing/process narration."""

        text = str(question or "")
        if is_contextual_series_selection_question(text):
            return (
                "这轮系列赛的范围已经明确，但现有资料暂时不足以完成这项选场判断。"
                "请稍后重试。"
            )
        if is_subjective_comparison_question(text):
            return (
                "现有资料暂时不足以完成这两位球员的多维比较，"
                "因此无法给出可靠判断。请稍后重试。"
            )
        if re.search(r"(?:战术|挡拆|防守策略|怎么限制|如何限制|如何防|怎么防)", text):
            return (
                "当前资料不足以支持这项战术分析，暂时无法给出可靠结论。"
                "请稍后重试。"
            )
        return (
            "当前资料不足以可靠还原这场比赛的过程或胜因。"
            "请稍后重试。"
        )

    @staticmethod
    def _is_bare_matchup_query(question: str) -> bool:
        """Return whether the turn contains only a season/year and two teams.

        A bare ``2026尼克斯-马刺`` is an overview request even though it has
        no interrogative.  Detect it independently from the broader open-ended
        predicate so only this narrow shape receives the compact matchup
        observation used by the Agent; explicit score/stat/recap questions
        retain their richer typed evidence.
        """

        text = unicodedata.normalize("NFKC", str(question or "")).casefold()
        teams = []
        seen: set[str] = set()
        for item in resolve_entities(text):
            if item.kind is not EntityKind.TEAM or item.canonical_id in seen:
                continue
            seen.add(item.canonical_id)
            teams.append(item)
        if len(teams) < 2:
            return False

        aliases = sorted(
            {
                unicodedata.normalize("NFKC", str(alias)).casefold()
                for team in teams
                for alias in (team.display_name, *team.aliases)
                if str(alias).strip()
            },
            key=len,
            reverse=True,
        )
        residual = text
        for alias in aliases:
            residual = residual.replace(alias, " ")
        residual = re.sub(
            r"(?<!\d)(?:19|20)\d{2}\s*[-/]\s*\d{2,4}(?!\d)", " ", residual
        )
        residual = re.sub(r"(?<!\d)(?:19|20)\d{2}(?!\d)", " ", residual)
        residual = re.sub(
            r"(?:nba|赛季|对阵|交手|versus|vs\.?|对|和|与)",
            " ",
            residual,
            flags=re.IGNORECASE,
        )
        residual = re.sub(r"[\s\-–—_:：/\\、，,。.!！?？()（）]+", "", residual)
        return not residual

    @staticmethod
    def _compact_bare_matchup_observation(
        answer: Any,
        blocks: Any,
    ) -> str | None:
        """Turn a matchup result table into a concise, user-ready fact digest.

        The full relational table remains available to the application, but a
        model that receives it as its only observation can simply repeat it.
        For a bare matchup overview, supply the same facts as natural prose so
        even a conservative Agent response is readable while every score and
        winner relation remains server-owned.
        """

        def block_value(block: Any, key: str, default: Any = None) -> Any:
            if isinstance(block, Mapping):
                return block.get(key, default)
            return getattr(block, key, default)

        block_items = list(blocks or [])
        candidates = [str(answer or "")]
        candidates.extend(
            str(block_value(block, "content", "") or "")
            for block in block_items
            if str(
                getattr(
                    block_value(block, "type", ""),
                    "value",
                    block_value(block, "type", ""),
                )
            ).lower()
            == "text"
        )
        series_match = None
        for candidate in candidates:
            series_match = re.search(
                r"系列赛大比分\s*[：:]\s*\*{0,2}"
                r"(?P<team_a>[^*\n]+?)\s+(?P<wins_a>\d+)\s*[–—-]\s*"
                r"(?P<wins_b>\d+)\s+(?P<team_b>[^*。\n]+?)\*{0,2}(?:[。.!！]|$)",
                candidate,
            )
            if series_match:
                break
        if series_match is None:
            return None

        team_a = series_match.group("team_a").strip()
        team_b = series_match.group("team_b").strip()
        wins_a = int(series_match.group("wins_a"))
        wins_b = int(series_match.group("wins_b"))
        if not team_a or not team_b or wins_a == wins_b:
            return None

        table_rows: list[list[Any]] = []
        table_columns: list[str] = []
        recorded_series_count: int | None = None
        for block in block_items:
            raw_type = block_value(block, "type", "")
            block_type = str(getattr(raw_type, "value", raw_type)).lower()
            if block_type == "table":
                columns = [str(item) for item in list(block_value(block, "columns", []) or [])]
                required = {"客队", "比分", "主队"}
                if required.issubset(columns):
                    table_columns = columns
                    table_rows = [
                        list(row)
                        for row in list(block_value(block, "rows", []) or [])
                        if isinstance(row, (list, tuple))
                    ]
            elif block_type == "fact" and str(block_value(block, "label", "")) == "已计入场次":
                try:
                    recorded_series_count = int(block_value(block, "value"))
                except (TypeError, ValueError):
                    recorded_series_count = None
        if not table_rows or not table_columns:
            return None

        away_index = table_columns.index("客队")
        score_index = table_columns.index("比分")
        home_index = table_columns.index("主队")
        expected_count = wins_a + wins_b
        series_count = min(
            len(table_rows),
            recorded_series_count
            if recorded_series_count is not None and recorded_series_count > 0
            else expected_count,
        )
        if series_count != expected_count:
            return None
        series_rows = table_rows[:series_count]

        def row_result(row: list[Any]) -> tuple[str, str, int, int, str] | None:
            if max(away_index, score_index, home_index) >= len(row):
                return None
            away = str(row[away_index]).strip()
            home = str(row[home_index]).strip()
            score_match = re.fullmatch(
                r"\s*(\d+)\s*[–—-]\s*(\d+)\s*", str(row[score_index])
            )
            if not away or not home or score_match is None:
                return None
            away_score = int(score_match.group(1))
            home_score = int(score_match.group(2))
            if away_score == home_score:
                return None
            winner = away if away_score > home_score else home
            return away, home, away_score, home_score, winner

        chronological = list(reversed(series_rows))
        game_wins: dict[str, list[str]] = {team_a: [], team_b: []}
        for game_number, row in enumerate(chronological, start=1):
            result = row_result(row)
            if result is None or result[-1] not in game_wins:
                return None
            game_wins[result[-1]].append(f"G{game_number}")
        if len(game_wins[team_a]) != wins_a or len(game_wins[team_b]) != wins_b:
            return None

        winner = team_a if wins_a > wins_b else team_b
        sentences = [
            f"这组系列赛的结论很清楚：**{team_a} {wins_a}–{wins_b} {team_b}**，"
            f"{winner}赢下系列赛。"
        ]
        win_summaries = []
        for team in (team_a, team_b):
            games = "、".join(game_wins[team])
            if games:
                verb = "拿下" if len(game_wins[team]) > 1 else "赢了"
                win_summaries.append(f"{team}{verb} {games}")
        if win_summaries:
            sentences.append(f"{series_count} 场比赛里，" + "；".join(win_summaries) + "。")

        closing = row_result(series_rows[0])
        if closing is not None:
            away, home, away_score, home_score, closing_winner = closing
            winner_score = away_score if closing_winner == away else home_score
            loser_score = home_score if closing_winner == away else away_score
            location = "客场" if closing_winner == away else "主场"
            sentences.append(
                f"收官战 G{series_count}，{closing_winner}{location}以 "
                f"**{winner_score}–{loser_score}** 取胜。"
            )

        other_rows = table_rows[series_count:]
        other_wins = {team_a: 0, team_b: 0}
        for row in other_rows:
            result = row_result(row)
            if result is not None and result[-1] in other_wins:
                other_wins[result[-1]] += 1
        counted_other = sum(other_wins.values())
        if counted_other:
            if other_wins[team_a] == other_wins[team_b]:
                detail = f"两队各胜 {other_wins[team_a]} 场"
            else:
                detail = f"{team_a} {other_wins[team_a]} 胜，{team_b} {other_wins[team_b]} 胜"
            sentences.append(f"同赛季另外 {counted_other} 场交手中，{detail}。")

        return " ".join(sentences)

    @staticmethod
    def _team_match_tokens(team: EntityRef) -> set[str]:
        values = [team.display_name, *team.aliases]
        tokens: set[str] = set()
        for value in values:
            normalized = unicodedata.normalize("NFKC", str(value)).casefold()
            token = "".join(char for char in normalized if char.isalnum())
            if token:
                tokens.add(token)
        return tokens

    @classmethod
    def _same_matchup(cls, selected: Game, candidate: Game) -> bool:
        return bool(
            cls._team_match_tokens(selected.home)
            & cls._team_match_tokens(candidate.home)
        ) and bool(
            cls._team_match_tokens(selected.away)
            & cls._team_match_tokens(candidate.away)
        )

    @staticmethod
    def _public_reverification_target(question: str, context: Any) -> str:
        """Recover the prior factual question for a short “recheck online” turn."""

        stripped = _PUBLIC_REVERIFICATION_RE.sub("", str(question or "")).strip()
        stripped = stripped.strip("，,。.!！?？下")
        if len(stripped) >= 4 and re.search(
            r"(?:比分|谁|什么|哪里|哪儿|场馆|地点|最后|得分|篮板|助攻|战术|为什么)",
            stripped,
        ):
            return stripped
        for summary in reversed(
            list(getattr(context, "recent_turn_summaries", []) or [])
        ):
            previous = str(getattr(summary, "user_message", "") or "").strip()
            if previous and not _requests_public_reverification(previous):
                return previous
        return "这场比赛的结果和场馆信息"

    async def _agent_public_reverification_observation(
        self,
        *,
        selected_game_id: str,
        question: str,
        context: Any,
        deadline_at_utc: datetime,
        budget: RequestBudget,
        token: CancelToken,
    ) -> Mapping[str, Any]:
        """Resolve a selected card to one primary-source event without fallback.

        Internal snapshot IDs are never sent to the public summary endpoint.
        The selected date and matchup are first matched against a force-refreshed
        public scoreboard.  Only one exact match authorizes a public summary;
        zero or multiple matches remain an honest, non-upgraded snapshot.
        """

        selected = self._selected_game(selected_game_id)
        if selected is None:
            message = "当前选中的比赛无法从服务器记录中确认，请重新选择比赛后再核验。"
            return {
                "status": "needs_clarification",
                "intent": "public_reverification",
                "query_scope": None,
                "answer_markdown": message,
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
                "data_origin": "none",
            }

        zone_name = str(getattr(context, "timezone", "Asia/Shanghai"))
        # The calendar scope must use the caller's display timezone.  Convert
        # the selected UTC instant first; passing its UTC date directly would
        # be wrong for evening games crossing Beijing midnight.
        selected_local_day = selected.start_utc.astimezone(
            validate_timezone(zone_name)
        ).date()
        date_range = local_date_range(selected_local_day, zone_name)
        try:
            scoreboard = await self._await_with_cancel(
                self.gateway.search_games(
                    GameFilters(date_range=date_range),
                    budget=budget,
                    allow_fallback=False,
                    force_refresh=True,
                ),
                token,
            )
        except (TypeError, ValueError):
            scoreboard = None

        candidates: list[Game] = []
        if (
            scoreboard is not None
            and getattr(scoreboard, "error", None) is None
            and self._data_origin(getattr(scoreboard, "evidence", [])) == "public"
        ):
            for item in list(getattr(scoreboard, "data", None) or []):
                if not isinstance(item, Game):
                    continue
                item_day = item.start_utc.astimezone(
                    validate_timezone(zone_name)
                ).date()
                if item_day != selected_local_day:
                    continue
                if self._same_matchup(selected, item):
                    candidates.append(item)
        unique = {item.game_id: item for item in candidates}
        if len(unique) != 1:
            message = (
                f"已重新查询北京时间 **{selected_local_day.isoformat()}** 的公开赛事记录，"
                "但没有找到与当前对阵唯一匹配的比赛。当前选中内容仍是固定演示快照，"
                "不能升级为实时公开核验。"
            )
            return {
                "status": "no_data",
                "intent": "public_reverification",
                "query_scope": {"date": selected_local_day.isoformat()},
                "answer_markdown": message,
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
                "data_origin": "none",
            }

        matched = next(iter(unique.values()))
        try:
            summary = await self._await_with_cancel(
                self.gateway.get_game_summary(
                    matched.game_id,
                    budget=budget,
                    allow_fallback=False,
                    force_refresh=True,
                ),
                token,
            )
        except (TypeError, ValueError):
            summary = None
        if (
            summary is None
            or getattr(summary, "error", None) is not None
            or not isinstance(getattr(summary, "data", None), GameBundle)
            or self._data_origin(getattr(summary, "evidence", [])) != "public"
        ):
            message = (
                "已在公开赛程中唯一匹配到这场比赛，但比赛详情暂时没有返回可核验记录；"
                "当前演示快照中的细节不会被当作实时公开结果。"
            )
            return {
                "status": "no_data",
                "intent": "public_reverification",
                "query_scope": {
                    "date": selected_local_day.isoformat(),
                    "game_id": matched.game_id,
                },
                "answer_markdown": message,
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
                "data_origin": "none",
            }

        bundle = summary.data
        try:
            self.game_registry[matched.game_id] = bundle.game
            self.game_origin_registry[matched.game_id] = "public"
        except (AttributeError, TypeError):
            # Embedders may inject a read-only Mapping.  The current answer is
            # still grounded by the local public bundle; only future selected-
            # card reuse is unavailable in that non-standard configuration.
            pass
        target = self._public_reverification_target(question, context)
        parser = IntentParser(clock=self.clock, input_timezone=zone_name)
        try:
            parsed = parser.parse(target, context)
            parsed = self._attach_selected_game(
                parsed,
                bundle.game,
                self._selected_game_ref(matched.game_id, bundle.game),
            )
            facts, game, parsed_bundle, derived = self._facts_for(
                parsed,
                bundle,
                summary.evidence,
                provider_partial=bool(summary.partial),
            )
            draft = self.template_composer.compose(
                parsed.intent,
                facts,
                game=game,
                bundle=parsed_bundle,
                derived=derived,
                retrieved_at=summary.retrieved_at_utc,
                corrections=facts.corrections,
            )
            guarded = self.output_guard.validate(draft, facts)
            answer = (
                "已通过公开赛事记录重新核验。\n\n" + guarded.markdown
            )
            blocks = [
                block.model_dump(mode="json") for block in guarded.blocks
            ]
            evidence_state = guarded.evidence_state.value.lower()
        except (TypeError, ValueError, OutputGuardError):
            game = bundle.game
            score = (
                f"{game.away_score}–{game.home_score}"
                if game.away_score is not None and game.home_score is not None
                else "尚未产生终场比分"
            )
            answer = (
                "已通过公开赛事记录重新核验："
                f"{game.away.display_name} 对 {game.home.display_name}，{score}。"
            )
            blocks = []
            evidence_state = "partial" if summary.partial else "verified"
        return {
            "status": "completed",
            "intent": "public_reverification",
            "query_scope": {
                "date": selected_local_day.isoformat(),
                "game_id": matched.game_id,
            },
            "answer_markdown": answer,
            "blocks": blocks,
            "evidence_state": evidence_state,
            "as_of_beijing": format_beijing(summary.retrieved_at_utc),
            "data_origin": "public",
        }

    async def _agent_series_selection_observation(
        self,
        question: str,
        *,
        context: Any,
        selected_game_id: str | None,
        budget: RequestBudget,
        token: CancelToken,
        candidate_sink: dict[int, Game] | None = None,
    ) -> Mapping[str, Any]:
        """Return all trusted games in the active series for subjective ranking.

        A recommendation such as ``哪场最精彩`` cannot be answered from the
        latest game alone.  Resolve the session's typed matchup and season,
        then expose a compact candidate list to the Agent without rewriting
        the user's question or allowing a public profile to read demo rows.
        """

        refs = self._agent_search_scope_refs(
            question,
            context=context,
            selected_game_id=selected_game_id,
        )
        teams: list[EntityRef] = []
        seen_team_ids: set[str] = set()
        for ref in refs:
            if ref.kind is not EntityKind.TEAM or ref.canonical_id in seen_team_ids:
                continue
            seen_team_ids.add(ref.canonical_id)
            teams.append(ref)
        if len(teams) < 2:
            return {
                "status": "needs_clarification",
                "intent": "nba_query",
                "query_scope": None,
                "answer_markdown": "请先说明要比较哪一轮系列赛。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
                "data_origin": "none",
            }

        season = getattr(context, "active_season", None)
        filters = GameFilters(
            season=season,
            team_ids=[item.canonical_id for item in teams[:2]],
        )
        try:
            lookup = self.gateway.search_games(
                filters,
                budget=budget,
                fallback_on_empty=self._fixture_game_aliases_enabled,
                allow_fallback=self._fixture_game_aliases_enabled,
            )
        except TypeError:
            lookup = self.gateway.search_games(filters, budget=budget)
        result = await self._await_with_cancel(lookup, token)
        if result.error is not None:
            return {
                "status": "failed",
                "intent": "nba_query",
                "query_scope": None,
                "answer_markdown": "赛事资料暂时不可用，请稍后再试。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
                "data_origin": "none",
            }

        result_origin = self._data_origin(result.evidence)
        if (
            not self._fixture_game_aliases_enabled
            and result_origin in {"demo_snapshot", "mixed"}
        ):
            result = result.model_copy(
                update={
                    "data": [],
                    "evidence": [],
                    "partial": False,
                    "used_fallback": False,
                }
            )

        candidates = [
            item
            for item in (result.data or [])
            if isinstance(item, Game)
            and item.status is GameStatus.FINAL
            and {item.home.canonical_id, item.away.canonical_id}
            == set(seen_team_ids)
        ]
        groups: dict[str, list[Game]] = {}
        for game in candidates:
            if game.series_id and game.series_game_number is not None:
                groups.setdefault(game.series_id, []).append(game)
        if groups:
            candidates = max(
                groups.values(),
                key=lambda games: (len(games), max(game.start_utc for game in games)),
            )
        candidates = sorted(
            candidates,
            key=lambda game: (game.series_game_number or 99, game.start_utc),
        )
        if not candidates:
            matchup = f"{teams[0].display_name} 对 {teams[1].display_name}"
            return {
                "status": "no_data",
                "intent": "nba_query",
                "query_scope": {"matchup": matchup},
                "answer_markdown": f"暂时没有找到 {matchup} 的系列赛比赛记录。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": format_beijing(result.retrieved_at_utc),
                "data_origin": self._data_origin(result.evidence),
            }

        active_game_id = str(
            getattr(getattr(context, "active_game", None), "canonical_id", "")
            or ""
        )
        active_game_number = next(
            (
                int(game.series_game_number)
                for game in candidates
                if game.series_game_number is not None
                and game.game_id == active_game_id
            ),
            None,
        )
        wins: dict[str, int] = {item.canonical_id: 0 for item in teams[:2]}
        names = {item.canonical_id: item.display_name for item in teams[:2]}
        lines = ["本轮可供比较的比赛："]
        if active_game_number is not None:
            lines.append(f"当前会话已推荐场次：G{active_game_number}。")
        for index, game in enumerate(candidates, start=1):
            number = game.series_game_number or index
            if game.home_score is None or game.away_score is None:
                score = "比分未完整记录"
                margin = "分差未知"
                winner = None
            else:
                score = (
                    f"{game.away.display_name} {game.away_score}–{game.home_score} "
                    f"{game.home.display_name}"
                )
                margin = f"分差 {abs(game.home_score - game.away_score)} 分"
                winner = game.home if game.home_score > game.away_score else game.away
                if winner.canonical_id in wins:
                    wins[winner.canonical_id] += 1
            winner_text = f"，{winner.display_name}胜" if winner is not None else ""
            progress = (
                f"，赛后系列赛 {names[teams[0].canonical_id]} "
                f"{wins[teams[0].canonical_id]}–{wins[teams[1].canonical_id]} "
                f"{names[teams[1].canonical_id]}"
                if winner is not None
                else ""
            )
            lines.append(
                f"- G{number}：{score}（{margin}{winner_text}{progress}）"
            )
        ordered_wins = sorted(wins.items(), key=lambda item: item[1], reverse=True)
        if len(ordered_wins) == 2:
            lines.append(
                "系列赛结果："
                f"{names[ordered_wins[0][0]]} {ordered_wins[0][1]}–{ordered_wins[1][1]} "
                f"{names[ordered_wins[1][0]]}。"
            )
        data_origin = self._data_origin(result.evidence)
        evidence_state = (
            "partial"
            if result.partial or data_origin == "demo_snapshot"
            else "verified"
        )
        if candidate_sink is not None:
            candidate_sink.clear()
            # ``partial`` here can mean that venue, coaches or box-score
            # fields are incomplete; it does not invalidate the canonical
            # public game id, teams, score or series number used for bounded
            # relation checks and conversational scope. Demo/mixed rows remain
            # excluded by the origin gate above.
            if data_origin == "public":
                candidate_sink.update(
                    {
                        int(game.series_game_number): game
                        for game in candidates
                        if game.series_game_number is not None
                    }
                )
        return {
            "status": "completed",
            "intent": "nba_query",
            "query_scope": {
                "team_ids": [item.canonical_id for item in teams[:2]],
                "season": getattr(season, "label", None),
                "active_game_number": active_game_number,
            },
            "answer_markdown": "\n".join(lines),
            "blocks": [],
            "evidence_state": evidence_state,
            "as_of_beijing": format_beijing(result.retrieved_at_utc),
            "data_origin": data_origin,
            "coverage": "series_candidates_ready",
        }

    def _agent_resolved_objective_game_plan(
        self,
        question: str,
        *,
        context: Any,
    ) -> QueryPlan | None:
        """Resolve an unambiguous objective game/PBP turn for grounding.

        This intentionally excludes schedules, broad history, player-career
        statistics and open analysis.  It is a narrow factual-follow-up seam:
        the current game or matchup must already be resolved and the existing
        typed planner must know exactly which operation to perform.
        """

        try:
            parsed = IntentParser(
                clock=self.clock,
                input_timezone=str(getattr(context, "timezone", "Asia/Shanghai")),
                include_fixture_games=self._fixture_game_aliases_enabled,
            ).parse(question, context)
            if (
                parsed.missing_slots
                or parsed.ambiguity_reasons
                or parsed.intent.mode is not QueryMode.OBJECTIVE
            ):
                return None
            plan = self.planner.build(parsed.intent)
        except (TypeError, ValueError):
            return None
        if plan is None or plan.operation not in {
            "get_game_summary",
            "get_play_by_play",
            "search_matchup",
        }:
            return None
        return plan

    def _agent_typed_pbp_game_id(
        self,
        question: str,
        *,
        context: Any,
    ) -> str | None:
        """Resolve an exact event-level follow-up to its trusted game id.

        Agent planning normally decides when it needs a tool; the evidence
        boundary may also add one when a factual answer arrived with no
        observation.  In either case, an already-resolved request such as
        ``你刚推荐的那场最后一分钟`` must observe the typed play-by-play for
        that game.  A broad web result cannot become authority for a different
        Finals game.  Reuse the same parser and planner as the deterministic
        path so this seam does not maintain a second list of pronouns or
        time-window phrases.
        """

        plan = self._agent_resolved_objective_game_plan(
            question,
            context=context,
        )
        if plan is None or plan.operation != "get_play_by_play" or not plan.args:
            return None
        game_id = str(plan.args[0] or "").strip()
        return game_id or None

    def _agent_tool_runner(
        self,
        *,
        session_id: UUID,
        context: Any,
        selected_game_id: str | None,
        original_question: str,
        deadline_at_utc: datetime,
        budget: RequestBudget,
        token: CancelToken,
        sink: Any,
        series_candidates: dict[int, Game] | None = None,
    ):
        typed_pbp_game_id = self._agent_typed_pbp_game_id(
            original_question,
            context=context,
        )

        async def run(tool_name: str, arguments: dict[str, str]) -> Mapping[str, Any]:
            token.raise_if_cancelled()
            await _emit(
                sink,
                "run.status",
                {"stage": "agent_tool", "text": "正在核对相关信息"},
            )
            if selected_game_id and _requests_public_reverification(original_question):
                return await self._agent_public_reverification_observation(
                    selected_game_id=selected_game_id,
                    question=original_question,
                    context=context,
                    deadline_at_utc=deadline_at_utc,
                    budget=budget,
                    token=token,
                )
            if is_contextual_series_selection_question(original_question):
                # A series recommendation has one useful typed operation: load
                # all games in the active series.  Rebind a tempting search or
                # schedule choice to that observation so the single-call
                # budget still leaves a final synthesis pass and comparisons
                # such as “为什么不是 G2” cannot drift out of the series.
                return await self._agent_series_selection_observation(
                    original_question,
                    context=context,
                    selected_game_id=selected_game_id,
                    budget=budget,
                    token=token,
                    candidate_sink=series_candidates,
                )
            if typed_pbp_game_id is not None:
                # The deterministic parser has already resolved the pronoun,
                # window and game.  Preserve Agent planning while making the
                # sole observation the exact typed PBP instead of an unrelated
                # search/news/schedule result.
                tool_name = "nba_query"
            if tool_name == "nba_schedule":
                return await self._agent_schedule_observation(
                    arguments,
                    timezone_name=context.timezone,
                    deadline_at_utc=deadline_at_utc,
                    budget=budget,
                    token=token,
                )
            if tool_name == "nba_query":
                # The user's wording plus any server-owned selected_game_id
                # is the authoritative query scope.  The Agent chooses the
                # tool, but a planning paraphrase must not silently change a
                # requested metric, matchup, date, or game number.
                message = (
                    original_question
                    if original_question.strip()
                    else arguments.get("question", "")
                )
                intent = "nba_query"
            elif tool_name == "nba_news":
                message = arguments.get("subject", "")
                date_expression = arguments.get("date_expression")
                if date_expression:
                    message = f"{message}，时间范围：{date_expression}"
                message = f"{message} NBA 新闻"
                intent = "nba_news"
            elif tool_name == "nba_search":
                return await self._agent_web_search_observation(
                    arguments.get("query", ""),
                    subject_refs=self._agent_search_scope_refs(
                        original_question,
                        context=context,
                        selected_game_id=selected_game_id,
                    ),
                    deadline_at_utc=deadline_at_utc,
                    budget=budget,
                    token=token,
                )
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
                    selected_game_id=selected_game_id,
                ),
                cancel=token,
                _internal_tool=True,
                _parent_deadline=deadline_at_utc,
                _parent_budget=budget,
            )
            resolved_game = (
                nested.resolved_game
                if isinstance(nested.resolved_game, Game)
                else None
            )
            resolved_game_id = typed_pbp_game_id
            if resolved_game is not None:
                # Only the server-owned typed result can populate this
                # registry.  The canonical id is retained for application
                # context after the Agent turn, while the tool bridge removes
                # it from the model-visible observation.
                resolved_game_id = resolved_game.game_id
                origin = str(nested.data_origin or "none").lower()
                if origin in {"public", "demo_snapshot"}:
                    try:
                        self.game_registry[resolved_game.game_id] = resolved_game
                        self.game_origin_registry[resolved_game.game_id] = origin
                    except (TypeError, AttributeError):
                        # Custom callers may inject a read-only Mapping.  The
                        # answer remains usable; only follow-up persistence is
                        # unavailable for that embedding.
                        pass
            status = nested.status
            if status not in {"completed", "no_data", "needs_clarification"}:
                status = "failed"
            primary_observation = {
                "status": status,
                "intent": intent,
                "query_scope": (
                    {"game_id": resolved_game_id}
                    if resolved_game_id is not None
                    else None
                ),
                "answer_markdown": nested.answer_markdown,
                "blocks": [
                    block.model_dump(mode="json")
                    if hasattr(block, "model_dump")
                    else block
                    for block in nested.blocks
                ],
                "evidence_state": nested.evidence_state,
                "as_of_beijing": nested.as_of_beijing,
                "data_origin": nested.data_origin,
                # Kept out of ``sanitise_observation`` and consumed only by
                # the Agent task bridge to attach provider-neutral notices to
                # the public request envelope.
                "_public_notices": list(nested.notices or []),
            }
            if status == "failed" and isinstance(nested.error, Mapping):
                nested_code = str(nested.error.get("code") or "")
                if nested_code:
                    primary_observation["_error_code"] = nested_code
                    primary_observation["_retryable"] = bool(
                        nested.error.get("retryable", False)
                    )
            if tool_name == "nba_query" and self._is_bare_matchup_query(
                original_question
            ):
                compact_matchup = self._compact_bare_matchup_observation(
                    nested.answer_markdown,
                    nested.blocks,
                )
                if compact_matchup:
                    # The full table remains server-side.  The Agent receives a
                    # natural, fact-equivalent digest so a conservative model
                    # cannot turn a bare matchup overview into a database-like
                    # table dump merely by echoing its observation.
                    primary_observation["answer_markdown"] = compact_matchup
                    primary_observation["blocks"] = []
            if tool_name == "nba_query" and self._is_open_ended_agent_synthesis(
                original_question
            ):
                requested_detail_missing = status in {
                    "no_data",
                    "needs_clarification",
                } or bool(
                    re.search(
                        r"(?:没有|缺少|暂无|无法还原|不能还原|"
                        r"未包含|未提供|只能给出).{0,30}"
                        r"(?:逐回合|分节|走势|比赛过程|战术|细节)",
                        nested.answer_markdown,
                    )
                )
                if requested_detail_missing:
                    # Coverage is data-quality metadata, not a server-owned
                    # tool decision.  It tells the Agent that the requested
                    # recap dimension is absent so it can decide whether to
                    # search for public reporting instead of mistaking a
                    # complete score row for a complete answer.
                    primary_observation["coverage"] = "requested_detail_missing"
            # ``nba_query`` can resolve a news intent through the typed
            # provider.  Do not hand the Agent the entire article body: a
            # several-thousand-character observation encourages verbatim
            # copying instead of synthesis and consumes the model budget.
            if tool_name in {"nba_query", "nba_news"} and re.search(
                r"(?:新闻|消息|资讯|报道|动态|近况|头条|背景|\bnews\b|\bheadline\b)",
                message,
                re.IGNORECASE,
            ):
                compact = self._compact_news_blocks(getattr(nested, "blocks", []))
                if compact:
                    primary_observation["answer_markdown"] = compact
                else:
                    # Some adapters expose a single markdown answer rather
                    # than FACT/TEXT blocks.  Apply the same bounded
                    # projection so a raw article can never become the Agent
                    # observation verbatim.
                    compact = self._compact_search_markdown(nested.answer_markdown)
                    if compact:
                        primary_observation["answer_markdown"] = compact
            return primary_observation

        return run

    def _agent_search_scope_refs(
        self,
        question: str,
        *,
        context: Any,
        selected_game_id: str | None,
    ) -> list[EntityRef]:
        """Attach trusted entity scope without rewriting the Agent query.

        The search phrase is entirely Agent-owned.  These typed references
        only help the provider rank/filter candidates for the event or subject
        the user actually named.  Explicit entities take precedence; selected
        or recent-game context is used only for a game reference or a genuine
        deictic follow-up such as ``这场``.
        """

        text = str(question or "")
        explicit = list(
            resolve_entities(
                text,
                include_fixture_games=self._fixture_game_aliases_enabled,
            )
        )
        refs: list[EntityRef] = list(explicit)
        explicit_subject = any(
            item.kind in {EntityKind.TEAM, EntityKind.PLAYER} for item in explicit
        )
        game_refs = [item for item in explicit if item.kind is EntityKind.GAME]
        deictic = is_contextual_series_selection_question(text) or bool(
            re.search(
                r"(?:这场|本场|那场|上场|刚才|该场|这个比赛|"
                r"本场比赛|这轮系列赛|这轮|(?:最近|最后)(?:的)?一场(?:比赛)?)",
                text,
            )
        )

        scoped_game_ids: list[str] = [item.canonical_id for item in game_refs]
        if selected_game_id and (deictic or not explicit_subject):
            scoped_game_ids.append(str(selected_game_id))
        if deictic and not scoped_game_ids:
            active = getattr(context, "active_game", None)
            if isinstance(active, EntityRef):
                refs.append(active)
                scoped_game_ids.append(active.canonical_id)
            for summary in reversed(
                list(getattr(context, "recent_turn_summaries", []) or [])
            ):
                summary_refs = list(getattr(summary, "active_refs", []) or [])
                refs.extend(
                    item
                    for item in summary_refs
                    if isinstance(item, EntityRef)
                    and item.kind in {EntityKind.GAME, EntityKind.TEAM, EntityKind.PLAYER}
                )
                scoped_game_ids.extend(
                    item.canonical_id
                    for item in summary_refs
                    if isinstance(item, EntityRef) and item.kind is EntityKind.GAME
                )
                summary_team_ids = {
                    item.canonical_id
                    for item in summary_refs
                    if isinstance(item, EntityRef) and item.kind is EntityKind.TEAM
                }
                if scoped_game_ids or len(summary_team_ids) >= 2:
                    break

        # Expand a server-known game into its two teams.  This makes an Agent
        # query such as ``2026总决赛系列赛综述`` useful without
        # changing that query to a server-authored phrase.
        for game_id in dict.fromkeys(scoped_game_ids):
            game = self._selected_game(game_id)
            if game is None:
                continue
            refs.append(self._selected_game_ref(game_id, game))
            refs.extend(
                EntityRef(
                    kind=EntityKind.TEAM,
                    canonical_id=team.canonical_id,
                    display_name=team.display_name,
                    aliases=list(team.aliases),
                    confidence=1,
                )
                for team in (game.away, game.home)
            )

        deduplicated: list[EntityRef] = []
        seen: set[tuple[EntityKind, str]] = set()
        for item in refs:
            key = (item.kind, item.canonical_id)
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(item)
            if len(deduplicated) >= 8:
                break
        return deduplicated

    async def _agent_web_search_observation(
        self,
        query_text: str,
        *,
        subject_refs: list[EntityRef] | None = None,
        deadline_at_utc: datetime,
        budget: RequestBudget,
        token: CancelToken,
    ) -> Mapping[str, Any]:
        """Run a bounded public-search lookup for long-tail Agent questions.

        The search provider is composed at the application root (managed search
        first, with configured bounded fallbacks).  This method intentionally calls
        the typed gateway and projects only titles/summaries into the Agent;
        raw HTML, URLs and provider fields never cross the tool boundary.
        """

        text = " ".join(str(query_text or "").strip().split())[:80]
        if not text:
            return {
                "status": "needs_clarification",
                "intent": "web_search",
                "query_scope": None,
                "answer_markdown": "请补充要检索的 NBA 球员、球队、比赛或主题。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
                "data_origin": "none",
            }
        # NewsQuery caps each keyword at 80 characters.  Preserve the Agent's
        # exact wording inside that public contract; typed ``subject_refs``
        # carry trustworthy scope separately and never mutate the search text.
        keyword = text[:80]
        search_query = NewsQuery(
            subject_refs=list(subject_refs or []),
            keywords=[keyword],
            limit=5,
        )
        # Use the dedicated web operation so a long-tail search does not
        # consume an unnecessary structured-news call.  A few embedding/test
        # callers still inject a legacy gateway; retain a narrow compatibility
        # fallback for those objects only.
        search_method = getattr(self.gateway, "search_web", None)
        if not callable(search_method):
            search_method = getattr(self.gateway, "search_news", None)
        if not callable(search_method):
            result = ProviderResult(
                data=[],
                evidence=[],
                partial=True,
                retrieved_at_utc=datetime.now(UTC),
            )
        else:
            try:
                result = await self._await_with_cancel(
                    search_method(
                        search_query,
                        budget=budget,
                        fallback_on_empty=False,
                        allow_fallback=self._fixture_game_aliases_enabled,
                    ),
                    token,
                )
            except (TypeError, ValueError, AttributeError):
                result = await self._await_with_cancel(
                    search_method(search_query, budget=budget),
                    token,
                )
        if result.error is not None:
            error_code = (
                "SEARCH_QUOTA_EXHAUSTED"
                if result.error.kind is ProviderErrorKind.QUOTA_EXHAUSTED
                else {
                    ProviderErrorKind.TIMEOUT: ErrorCode.UPSTREAM_TIMEOUT.value,
                    ProviderErrorKind.RATE_LIMITED: ErrorCode.UPSTREAM_RATE_LIMITED.value,
                    ProviderErrorKind.AUTH: ErrorCode.UPSTREAM_AUTH.value,
                    ProviderErrorKind.INVALID_JSON: ErrorCode.INVALID_UPSTREAM_DATA.value,
                    ProviderErrorKind.SCHEMA_MISMATCH: ErrorCode.INVALID_UPSTREAM_DATA.value,
                }.get(result.error.kind, ErrorCode.COMPOSER_UNAVAILABLE.value)
            )
            return {
                "status": "failed",
                "intent": "web_search",
                "query_scope": None,
                "answer_markdown": "公开资料检索暂时不可用，请稍后重试。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
                "data_origin": "none",
                # These fields are consumed by the request-local bridge and
                # deliberately omitted from the model-visible observation.
                "_error_code": error_code,
                "_retryable": bool(result.error.retryable),
            }
        request_capability_issues = [
            {
                "kind": issue.kind.value,
                "retryable": bool(issue.retryable),
            }
            for issue in result.capability_issues[:8]
        ]

        def with_capability_issues(observation: dict[str, Any]) -> dict[str, Any]:
            if request_capability_issues:
                observation["_capability_issues"] = request_capability_issues
            return observation

        search_origin = self._data_origin(result.evidence)
        if (
            not self._fixture_game_aliases_enabled
            and search_origin in {"demo_snapshot", "mixed"}
        ):
            return with_capability_issues({
                "status": "no_data",
                "intent": "web_search",
                "query_scope": None,
                "answer_markdown": f"暂未检索到与“{text}”直接相关的公开 NBA 资料。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": None,
                "data_origin": "none",
            })
        items = [item for item in (result.data or []) if isinstance(item, NewsItem)]
        # For an explicit head-to-head query, generic team-news results (for
        # example a Spurs front-office article) are not useful context.  Keep
        # only entries mentioning both requested teams.  Retaining unrelated
        # candidates when every provider missed the matchup previously leaked
        # generic offseason and single-player articles into the answer.
        typed_refs = list(subject_refs or [])
        team_entities = [
            item
            for item in [*typed_refs, *resolve_entities(text)]
            if item.kind is EntityKind.TEAM
        ]
        team_entities = list(
            {item.canonical_id: item for item in team_entities}.values()
        )
        recap_search = is_game_recap_question(text)
        recommendation_search = is_positive_series_selection_question(text)
        search_retrieved_at = result.retrieved_at_utc
        if len({item.canonical_id for item in team_entities}) >= 2:
            haystacks = [
                f"{item.title} {item.summary}".casefold()
                for item in items
            ]
            relevant: list[NewsItem] = []
            for index, haystack in enumerate(haystacks):
                if all(
                    any(
                        str(alias).casefold() in haystack
                        for alias in [team.display_name, *team.aliases]
                    )
                    for team in team_entities[:2]
                ):
                    relevant.append(items[index])
            items = relevant
            if recap_search:
                # A single-game recap must not be polluted by post-title
                # roster speculation. Keep a roster/offseason story only if
                # it also contains a concrete same-game anchor.
                filtered: list[NewsItem] = []
                for item in items:
                    haystack = f"{item.title} {item.summary}".casefold()
                    roster_topic = re.search(
                        r"(?:夺冠后|阵容|休赛期|补强|交易|续约|下赛季)", haystack
                    )
                    game_anchor = re.search(
                        r"(?:\bg\s*5\b|第五场|6月\s*14|06[-/.]14|94\s*[-–—比]\s*90|"
                        r"末节|第四节|半场|关键回合|比赛过程|全场)",
                        haystack,
                        re.IGNORECASE,
                    )
                    if roster_topic and game_anchor is None:
                        continue
                    filtered.append(item)
                items = filtered
            # Search providers may return a mixture of single-game recaps and
            # series overview pages. For a bare matchup query, prefer pages
            # that can answer the likely intent (champion/G5/series result),
            # while retaining the provider order as a tie-breaker.
            def matchup_priority(item: NewsItem) -> tuple[int, int]:
                haystack = f"{item.title} {item.summary}".casefold()
                score = 0
                if recommendation_search:
                    weights = (
                        ("逆转", 10),
                        ("绝杀", 10),
                        ("加时", 9),
                        ("一分", 8),
                        ("1分", 8),
                        ("29分", 8),
                        ("关键", 6),
                        ("g4", 3),
                        ("第四场", 3),
                    )
                elif recap_search:
                    weights = (
                        ("g5", 8),
                        ("第五场", 8),
                        ("94-90", 8),
                        ("94–90", 8),
                        ("6月14", 7),
                        ("末节", 6),
                        ("第四节", 6),
                        ("半场", 5),
                        ("关键", 4),
                        ("比赛过程", 4),
                        ("全场", 3),
                    )
                else:
                    weights = (
                        ("总冠军", 6),
                        ("夺冠", 5),
                        ("g5", 5),
                        ("4-1", 5),
                        ("总决赛", 3),
                        ("系列赛", 2),
                    )
                for term, weight in weights:
                    if term in haystack:
                        score += weight
                return score, -items.index(item)

            items = sorted(items, key=matchup_priority, reverse=True)
        # Player comparisons need evidence for *both* subjects.  The previous
        # any-alias filter could leave the Agent with three Jordan-only pages
        # for a Jordan/LeBron question (and, when no alias matched, retained
        # every unrelated result).  Prefer direct comparison pages; when the
        # provider only returns individual profiles, order a collectively
        # complete set first so the three-item observation still covers both.
        player_entities = [
            item
            for item in [*typed_refs, *resolve_entities(text)]
            if item.kind is EntityKind.PLAYER
        ]
        player_entities = list(
            {item.canonical_id: item for item in player_entities}.values()
        )
        if player_entities:
            aliases_by_player = {
                entity.canonical_id: {
                    str(alias).casefold()
                    for alias in [entity.display_name, *entity.aliases]
                    if str(alias).strip()
                }
                for entity in player_entities
            }

            def player_mentions(item: NewsItem) -> set[str]:
                haystack = f"{item.title} {item.summary}".casefold()
                return {
                    player_id
                    for player_id, aliases in aliases_by_player.items()
                    if any(alias in haystack for alias in aliases)
                }

            rows = [
                (item, player_mentions(item))
                for item in items
            ]
            rows = [(item, mentions) for item, mentions in rows if mentions]
            required_players = set(aliases_by_player)
            covered_players = set().union(
                *(mentions for _, mentions in rows)
            ) if rows else set()
            if not required_players.issubset(covered_players):
                # Returning evidence for only one side biases the requested
                # judgment more than returning an honest search miss.
                items = []
            elif len(required_players) >= 2:
                paired = [
                    item
                    for item, mentions in rows
                    if required_players.issubset(mentions)
                ]
                prioritized: list[NewsItem] = list(paired)
                prioritized_ids = {id(item) for item in prioritized}
                uncovered = (
                    set()
                    if paired
                    else set(required_players)
                )
                # With no direct comparison page, greedily put one or more
                # complementary profiles first.  Stable provider ordering is
                # retained for equal coverage.
                remaining = [
                    (item, mentions)
                    for item, mentions in rows
                    if id(item) not in prioritized_ids
                ]
                while uncovered:
                    best = max(
                        remaining,
                        key=lambda row: len(row[1] & uncovered),
                        default=None,
                    )
                    if best is None or not (best[1] & uncovered):
                        break
                    prioritized.append(best[0])
                    prioritized_ids.add(id(best[0]))
                    uncovered.difference_update(best[1])
                    remaining = [row for row in remaining if row[0] is not best[0]]
                prioritized.extend(
                    item
                    for item, _ in rows
                    if id(item) not in prioritized_ids
                )
                items = prioritized
            else:
                items = [item for item, _ in rows]
        else:
            # Preserve support for well-known names not yet in the canonical
            # entity table, but never keep unrelated provider results merely
            # because none of them contained the requested name.
            player_terms = [
                alias.casefold()
                for alias in _SEARCH_SUBJECT_ALIASES
                if alias.casefold() in text.casefold()
            ]
            if player_terms:
                items = [
                    item
                    for item in items
                    if any(
                        alias in f"{item.title} {item.summary}".casefold()
                        for alias in player_terms
                    )
                ]
        if not items:
            return with_capability_issues({
                "status": "no_data",
                "intent": "web_search",
                "query_scope": None,
                "answer_markdown": f"暂未检索到与“{text}”直接相关的公开 NBA 资料。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": format_beijing(search_retrieved_at),
                "data_origin": "public",
            })
        lines = ["公开资料线索（待交叉核验）："]
        seen_titles: set[str] = set()
        total_bytes = len(lines[0].encode("utf-8"))
        # Three short entries are enough for the Agent to identify the
        # relevant story.  More entries increase duplication and make the
        # model copy search results instead of answering the question.
        for item in items:
            title = self._short_public_text(item.title, limit=120)
            summary = self._short_public_text(item.summary, limit=240)
            # A title-only hit is not reliable evidence and must not cross the
            # Agent observation boundary.  Some search providers emit
            # knowledge-card headlines without a snippet; retaining those rows
            # made Hermes repeat headlines as if they were verified facts.
            if not summary:
                continue
            # Baidu/AI-search occasionally emits a year-only knowledge-card
            # title (for example ``2026``). It carries no identifying context
            # and looks broken in the UI; retain the item only when the title
            # is a meaningful headline.
            if not title or re.fullmatch(r"(?:19|20|21)\d{2}", title) or len(title) < 4:
                continue
            title_key = re.sub(r"\s+", "", title).casefold()
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            line = f"- **{title}**" + (f"：{summary}" if summary else "")
            encoded = len(line.encode("utf-8"))
            # Keep the observation compact enough for Hermes to synthesize a
            # response instead of copying several full articles verbatim.
            if total_bytes + encoded > 1_800:
                break
            lines.append(line)
            total_bytes += encoded
            if len(seen_titles) >= 3:
                break
        if len(lines) == 1:
            return with_capability_issues({
                "status": "no_data",
                "intent": "web_search",
                "query_scope": None,
                "answer_markdown": f"暂未检索到与“{text}”直接相关的公开 NBA 资料。",
                "blocks": [],
                "evidence_state": "none",
                "as_of_beijing": format_beijing(search_retrieved_at),
                "data_origin": "public",
            })
        return with_capability_issues({
            "status": "completed",
            "intent": "web_search",
            "query_scope": None,
            "answer_markdown": "\n".join(lines),
            "blocks": [],
            # Search snippets remain contextual evidence even when the
            # provider request itself completed fully.  They cannot upgrade a
            # game/stat/PBP claim to verified without a typed record.
            "evidence_state": "partial",
            "as_of_beijing": format_beijing(search_retrieved_at),
            "data_origin": "public",
        })

    async def _agent_schedule_observation(
        self,
        arguments: Mapping[str, str],
        *,
        timezone_name: str,
        deadline_at_utc: datetime,
        budget: RequestBudget,
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
                    GameFilters(date_range=date_range, team_ids=team_ids),
                    budget=budget,
                    allow_fallback=self._fixture_game_aliases_enabled,
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
        data_origin = self._data_origin(provider_result.evidence)
        if (
            not self._fixture_game_aliases_enabled
            and data_origin in {"demo_snapshot", "mixed"}
        ):
            games = []
            data_origin = "none"
        else:
            games = [
                item for item in (provider_result.data or []) if isinstance(item, Game)
            ]
        as_of = (
            None
            if data_origin == "demo_snapshot"
            else format_beijing(provider_result.retrieved_at_utc)
        )
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
                "data_origin": data_origin,
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
                "data_origin": data_origin,
            }
        return {
            "status": "completed",
            "intent": "schedule_result",
            "query_scope": scope,
            "answer_markdown": guarded.markdown,
            "blocks": [block.model_dump(mode="json") for block in guarded.blocks],
            "evidence_state": guarded.evidence_state.value.lower(),
            "as_of_beijing": as_of,
            "data_origin": data_origin,
        }

    async def _call_plan(
        self,
        plan: QueryPlan,
        budget: RequestBudget,
        token: CancelToken,
        *,
        authorized_demo_game_id: str | None = None,
    ):
        token.raise_if_cancelled()

        def snapshot_allowed(operation: str, args: tuple[Any, ...]) -> bool:
            if self._fixture_game_aliases_enabled:
                return True
            return bool(
                authorized_demo_game_id
                and operation in {"get_game_summary", "get_play_by_play"}
                and args
                and str(args[0]) == authorized_demo_game_id
            )

        def enforce_origin(result: Any, *, allow_snapshot: bool) -> Any:
            """Drop fixture evidence that was not authorized by a demo card.

            Per-call fallback flags protect the production gateway.  This
            second boundary also covers injected/composed providers that may
            themselves return fixture evidence while configured as hybrid.
            Treating it as an empty lookup is safer than surfacing a private
            snapshot as a public fact.
            """

            if (
                self._fixture_game_aliases_enabled
                or allow_snapshot
                or not isinstance(result, ProviderResult)
            ):
                return result
            if self._data_origin(result.evidence) not in {
                "demo_snapshot",
                "mixed",
            }:
                return result
            return result.model_copy(
                update={
                    "data": [],
                    "evidence": [],
                    "partial": False,
                    "used_fallback": False,
                    "error": None,
                }
            )

        if plan.operation == "search_matchup":
            # Resolve both teams against the scoreboard before loading any
            # statistics.  This prevents a query such as “2026尼克斯-马刺”
            # from being interpreted as a single-team (the first mention)
            # lookup.  A unique/most-recent match can then be expanded to its
            # canonical summary for score, leaders, venue, coaches and PBP.
            filters = plan.args[0]
            allow_snapshot = snapshot_allowed(plan.operation, plan.args)
            fallback_on_empty = bool(
                plan.kwargs.get("fallback_on_empty", False) and allow_snapshot
            )
            summary_if_match = bool(plan.kwargs.get("summary_if_match", False))
            try:
                lookup = self.gateway.search_games(
                    filters,
                    budget=budget,
                    fallback_on_empty=fallback_on_empty,
                    allow_fallback=allow_snapshot,
                )
            except TypeError:
                lookup = self.gateway.search_games(filters, budget=budget)
            result = enforce_origin(
                await self._await_with_cancel(lookup, token),
                allow_snapshot=allow_snapshot,
            )
            if (
                summary_if_match
                and result.error is None
                and isinstance(result.data, list)
                and result.data
            ):
                games = [item for item in result.data if isinstance(item, Game)]
                if games:
                    games.sort(key=lambda item: item.start_utc, reverse=True)
                    selected = games[0]
                    try:
                        summary_call = self.gateway.get_game_summary(
                            selected.game_id,
                            budget=budget,
                            fallback_on_empty=fallback_on_empty,
                            allow_fallback=allow_snapshot,
                        )
                    except TypeError:
                        summary_call = self.gateway.get_game_summary(
                            selected.game_id, budget=budget
                        )
                    summary = enforce_origin(
                        await self._await_with_cancel(summary_call, token),
                        allow_snapshot=allow_snapshot,
                    )
                    if summary.error is None and summary.data is not None:
                        return summary
            return result
        if plan.operation == "get_recent_play_by_play":
            # Resolve “最近一场” at the typed provider boundary. Restrict to
            # completed games. Public/hybrid chat never substitutes the fixed
            # demo snapshot for an unscoped "recent" question.
            allow_snapshot = self._fixture_game_aliases_enabled
            try:
                lookup = self.gateway.search_games(
                    GameFilters(status=GameStatus.FINAL),
                    budget=budget,
                    fallback_on_empty=allow_snapshot,
                    allow_fallback=allow_snapshot,
                )
            except TypeError:
                # Keep custom/legacy gateway implementations compatible with
                # this additive plan operation; the typed status filter still
                # prevents scheduled games from being treated as “recent”.
                lookup = self.gateway.search_games(
                    GameFilters(status=GameStatus.FINAL), budget=budget
                )
            recent = enforce_origin(
                await self._await_with_cancel(lookup, token),
                allow_snapshot=allow_snapshot,
            )
            if recent.error is not None or not recent.data:
                return recent
            games = [item for item in recent.data if isinstance(item, Game)]
            if not games:
                return recent.model_copy(update={"data": []})
            games.sort(key=lambda item: item.start_utc, reverse=True)
            return enforce_origin(
                await self._await_with_cancel(
                    self.gateway.get_game_summary(
                        games[0].game_id,
                        budget=budget,
                        allow_fallback=allow_snapshot,
                    ),
                    token,
                ),
                allow_snapshot=allow_snapshot,
            )
        method = getattr(self.gateway, plan.operation)
        kwargs = dict(plan.kwargs)
        allow_snapshot = snapshot_allowed(plan.operation, plan.args)
        # Fixture data is a labelled product-demo surface, not a silent
        # substitute for public chat facts. Only fixture deployments or an
        # exact server-registered demo card may authorize it.
        kwargs["allow_fallback"] = allow_snapshot
        if plan.operation in {"get_game_summary", "get_history", "search_news", "get_standings"}:
            kwargs["fallback_on_empty"] = bool(
                kwargs.get("fallback_on_empty", True) and allow_snapshot
            )
        result = await self._await_with_cancel(
            method(*plan.args, **kwargs, budget=budget), token
        )
        return enforce_origin(result, allow_snapshot=allow_snapshot)

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
                    parsed.intent.clock_window or game_end_window(5),
                    period=parsed.intent.period,
                )
            elif (
                parsed.intent.intent_name in {IntentName.TACTICAL, IntentName.RECAP}
                and data.plays
            ):
                # “为什么能赢” cannot be grounded by a final score alone.
                # Add a bounded final-minute event window to the same verified
                # box-score bundle, without claiming tactical detail that the
                # public PBP record does not contain.
                pbp = derive_pbp(data.plays, game_end_window(60))
                score_fact_ids = [
                    fact.fact_id
                    for fact in facts.facts
                    if fact.fact_id
                    in {
                        f"{data.game.game_id}:home_score",
                        f"{data.game.game_id}:away_score",
                    }
                ]
                totals = derive_game_totals(data.game, score_fact_ids)
                leaders = derive_leaders(data)
                derived = DerivedResult(
                    facts=[*totals.facts, *leaders.facts, *pbp.facts],
                    missing=[*totals.missing, *leaders.missing, *pbp.missing],
                    partial=totals.partial or leaders.partial or pbp.partial,
                    events=pbp.events,
                )
            elif (
                parsed.intent.metrics
                and getattr(parsed.intent.metrics[0].scope, "value", parsed.intent.metrics[0].scope)
                == "SERIES"
            ):
                derived = derive_leaders(data)
            else:
                score_fact_ids = [
                    fact.fact_id
                    for fact in facts.facts
                    if fact.fact_id
                    in {
                        f"{data.game.game_id}:home_score",
                        f"{data.game.game_id}:away_score",
                    }
                ]
                totals = derive_game_totals(data.game, score_fact_ids)
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
                evidence_ids = [
                    str(item.evidence_id).strip()
                    for item in evidence
                    if getattr(item, "evidence_id", None)
                    and str(item.evidence_id).strip()
                ]
                verified_rows = [
                    verify_game(item, evidence_ids or [f"game:{item.game_id}"])
                    for item in derived.games
                ]
                facts = FactBundle(
                    facts=[fact for row in verified_rows for fact in row.facts],
                    missing=[missing for row in verified_rows for missing in row.missing],
                    evidence_state=(
                        EvidenceState.PARTIAL
                        if provider_partial
                        or (derived and derived.partial)
                        or any(
                            row.evidence_state is EvidenceState.PARTIAL
                            for row in verified_rows
                        )
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
                if getattr(parsed.intent, "matchup", False) and parsed.intent.game_number is None:
                    series_groups: dict[str, list[Game]] = {}
                    for item in unique_games:
                        if item.series_id:
                            series_groups.setdefault(item.series_id, []).append(item)
                    if series_groups:
                        series_id, series_games = max(
                            series_groups.items(), key=lambda pair: len(pair[1])
                        )
                        if len(series_games) >= 2:
                            series = derive_series(series_games, series_id=series_id)
                            derived.facts.extend(series.facts)
                            derived.missing.extend(series.missing)
                            derived.partial = derived.partial or series.partial
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
            derived = derive_pbp(
                data, parsed.intent.clock_window or game_end_window(5), period=parsed.intent.period
            )
            # A play-by-play payload intentionally carries only its canonical
            # game id.  Reattach the already server-validated game projection
            # for rendering so home/away score fields are never shown as an
            # unlabeled pair (an away win such as 94–90 otherwise appears to
            # contradict a terminal provider-order value of 90–94).
            scoped_game = self._selected_game(getattr(data, "game_id", None))
            derived_bundle = FactBundle(
                facts=derived.facts,
                missing=derived.missing,
                evidence_state=(
                    EvidenceState.PARTIAL
                    if provider_partial and derived.facts
                    else derived.evidence_state
                ),
            )
            return derived_bundle, scoped_game, None, derived
        return FactBundle(facts=[], evidence_state=EvidenceState.NONE), None, None, None

    @staticmethod
    def _uniquely_resolved_game(data: Any) -> Game | None:
        """Return one canonical game only when the provider result is unique.

        Series and schedule lists must never bind their first row as session
        state.  A detail bundle (or a genuinely one-row game result) has one
        objective identity and is safe to carry across the private nested-tool
        boundary.
        """

        if isinstance(data, GameBundle):
            return data.game
        if isinstance(data, Game):
            return data
        if (
            isinstance(data, list)
            and len(data) == 1
            and isinstance(data[0], Game)
        ):
            return data[0]
        return None

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
            return (
                "请先从左侧“赛事下钻”（精彩回顾）选择最近一场比赛，"
                "或补充对阵双方，我再帮您核对关键回合。"
            )
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
        data_origin: str = "none",
        composition: Mapping[str, Any] | None = None,
        notices: list[dict[str, Any]] | None = None,
        resolved_game: Game | None = None,
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
            data_origin=data_origin,
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
            notices=list(notices or []),
            resolved_game=resolved_game,
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
        retryable: bool | None = None,
        message: str | None = None,
        notices: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        kind = getattr(error, "kind", ProviderErrorKind.HTTP)
        mapping = {
            ProviderErrorKind.TIMEOUT: ("UPSTREAM_TIMEOUT", True, "数据暂时不可用，请稍后重试。"),
            ProviderErrorKind.RATE_LIMITED: (
                "UPSTREAM_RATE_LIMITED",
                True,
                "数据服务暂时繁忙，请稍后重试。",
            ),
            ProviderErrorKind.QUOTA_EXHAUSTED: (
                "UPSTREAM_RATE_LIMITED",
                False,
                "在线检索额度已用完，暂时无法完成本次查询。",
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
        error_code, mapped_retryable, mapped_message = mapping.get(
            kind, (code or "SERVICE_BUSY", True, "服务暂时不可用，请稍后重试。")
        )
        if code:
            error_code = code
        if retryable is None:
            retryable = mapped_retryable
            if hasattr(error, "retryable"):
                retryable = bool(error.retryable)
        if message is None:
            message = mapped_message
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
            notices=list(notices or []),
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
