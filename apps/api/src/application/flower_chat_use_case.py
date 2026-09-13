"""Application boundary for the default flower-growing assistant.

The legacy basketball implementation is intentionally not reused for flower
queries.  This module adapts the small, deterministic flower core to the same
HTTP/SSE envelope used by the existing service and optionally lets the bounded
Hermes runtime compose a natural answer.  Search/model failures are
recoverable: a safe local answer is returned whenever possible and only
provider-neutral notices cross the API boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from apps.api.src.application.chat_use_case import ChatResult
from apps.api.src.application.flower_service import FlowerAssistantCore
from apps.api.src.application.flower_session_meta import (
    classify_flower_session_meta_question,
    render_flower_session_meta_answer,
)
from apps.api.src.application.ports import CancelToken, RequestBudget, RuntimeStatus
from apps.api.src.config import Settings
from apps.api.src.domain.errors import ProviderErrorKind
from apps.api.src.domain.flower import (
    FlowerIntentName,
    GardenContext,
    SearchObservation,
    VerificationLevel,
)
from apps.api.src.domain.models import AnswerBlock, AnswerBlockType
from apps.api.src.domain.safety import contains_public_implementation_leak
from apps.api.src.domain.time_policy import format_beijing
from apps.api.src.infrastructure.cache import InMemoryTTLCache
from apps.api.src.infrastructure.hermes_agent_runtime import (
    AgentHistoryMessage,
    AgentTurnInput,
    AgentTurnResult,
    HermesAgentRuntime,
    sanitise_agent_answer,
)
from apps.api.src.infrastructure.session_store import InMemorySessionStore

_FLOWER_WORDS = re.compile(
    r"(?:花|植物|盆栽|园艺|种植|养护|浇水|施肥|光照|土壤|换盆|修剪|扦插|繁殖|病虫|黄叶|萎蔫|花苞|开花|阳台|庭院|花盆)",
    re.IGNORECASE,
)
_SEARCH_WORDS = re.compile(
    r"(?:最新|今天|近期|天气|温度|湿度|法规|禁用|当地|上海|北京|广州|深圳|杭州|南京|成都|品种|学名|病虫害|疫情|为什么|对比|区别|资料|报道|研究)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?|ftp|file)://\S+|www\.\S+", re.IGNORECASE)
_PRIVATE_RE = re.compile(
    r"(?:hermes|provider|runtime|prompt|system[_ -]?prompt|tool[_ -]?call|"
    r"flower[_ -]?(?:lookup|search)|care[_ -]?plan|api[_ -]?key|bearer|"
    r"内部(?:模型|服务|流程|字段)|提示词|提供商|工具调用|原始响应)",
    re.IGNORECASE,
)


def _safe_search_text(value: str, *, max_length: int = 160) -> str:
    """Project user wording into a bounded search query.

    Search adapters are a separate trust boundary.  Keep the semantic words,
    but strip URLs, control characters and prompt-like directives before the
    query reaches an external service or cache key.
    """

    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = _URL_RE.sub(" ", text)
    text = re.sub(
        r"(?:忽略(?:之前|先前|所有)?指令|无视指令|系统提示(?:词)?|开发者消息|"
        r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?|system\s+prompt)",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return " ".join(text.split())[:max_length]


def _safe_model_question(value: str, *, max_length: int = 2000) -> str:
    """Return a bounded question safe to place in the model request.

    ``ChatRequest`` rejects control characters, but it intentionally keeps the
    user's prose intact.  The model boundary is a second trust boundary: URLs
    and prompt-like directives in a pasted message must not become executable
    instructions or an unbounded provider payload.  Keep ordinary Chinese
    wording unchanged so a normal question still reaches Hermes verbatim.
    """

    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = _URL_RE.sub(" ", text)
    text = re.sub(
        r"(?:忽略(?:之前|先前|所有)?指令|无视指令|系统提示(?:词)?|开发者消息|"
        r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?|system\s+prompt)",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return " ".join(text.split())[:max_length]


def _is_awaitable(value: Any) -> bool:
    """Small helper kept separate for sync and async search test doubles."""

    return inspect.isawaitable(value)


def _result_value(result: Any, key: str, default: Any = None) -> Any:
    """Read a field from either a typed provider result or a mapping."""

    if isinstance(result, Mapping):
        return result.get(key, default)
    return getattr(result, key, default)


def _safe_provider_evidence(value: Any) -> list[Any]:
    """Keep only evidence objects that satisfy the shared typed contract.

    Flower adapters are allowed to be small integrations and may return a
    partial dictionary (for example just ``evidence_id`` and ``url``).  Passing
    that dictionary into ``ProviderResult`` would raise a validation error and
    discard otherwise useful search items.  Invalid evidence is supplementary,
    so dropping it is safer than failing the whole turn.
    """

    if not isinstance(value, (list, tuple)):
        return []
    from apps.api.src.domain.models import Evidence

    clean: list[Any] = []
    for item in value[:32]:
        if isinstance(item, Evidence):
            clean.append(item)
            continue
        if isinstance(item, Mapping):
            try:
                clean.append(Evidence.model_validate(item))
            except Exception:
                continue
    return clean


def _now_utc(clock: Any | None = None) -> datetime:
    if clock is None:
        return datetime.now(UTC)
    value = clock() if callable(clock) else clock.now_utc()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an aware timestamp")
    return value.astimezone(UTC)


class FlowerSearchProvider:
    """Turn the existing typed web-search adapters into a flower provider."""

    def __init__(self, adapter: Any | None = None, *, prefix: str = "园艺 花卉") -> None:
        self.adapter = adapter
        self.prefix = prefix
        self.calls = 0

    async def search_web(self, query: Any, budget: RequestBudget):
        from apps.api.src.application.ports import ProviderResult
        from apps.api.src.domain.errors import ProviderError

        self.calls += 1
        if self.adapter is None:
            return ProviderResult(
                data=None,
                partial=False,
                error=ProviderError(
                    kind=ProviderErrorKind.AUTH,
                    retryable=False,
                    safe_message="search is not configured",
                ),
                retrieved_at_utc=datetime.now(UTC),
            )
        method = (
            getattr(self.adapter, "search_web", None)
            or getattr(self.adapter, "search_news", None)
            or getattr(self.adapter, "search", None)
        )
        if not callable(method):
            raise TypeError("search adapter has no supported operation")
        # Production adapters use ``(query, budget)`` and are async, while
        # lightweight deployments/tests often expose a synchronous
        # ``search(query)`` or return a plain mapping.  Inspect the signature
        # before invoking so an adapter-internal TypeError is not accidentally
        # retried (which could duplicate an external request).
        signature_parameters: Mapping[str, inspect.Parameter] = {}
        try:
            signature = inspect.signature(method)
            signature_parameters = signature.parameters
            parameters = list(signature_parameters.values())
            accepts_varargs = any(
                item.kind is inspect.Parameter.VAR_POSITIONAL for item in parameters
            )
            positional = [
                item
                for item in parameters
                if item.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                }
            ]
            accepts_budget = "budget" in signature_parameters or accepts_varargs
        except (TypeError, ValueError):
            # Builtins/mocks without an inspectable signature follow the
            # canonical adapter contract.
            positional = [None, None]
            accepts_budget = True
        budget_parameter = signature_parameters.get("budget")
        request_budget_parameter = signature_parameters.get("request_budget")
        if budget_parameter is not None and budget_parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            raw_result = method(query, budget=budget)
        elif request_budget_parameter is not None:
            raw_result = method(query, request_budget=budget)
        elif accepts_budget or len(positional) >= 2:
            raw_result = method(query, budget)
        else:
            raw_result = method(query)
        result = await raw_result if _is_awaitable(raw_result) else raw_result
        if isinstance(result, ProviderResult):
            return result
        # Keep the application boundary typed even when a custom adapter uses
        # a small dictionary/list contract.  The search projection below only
        # needs ``data``, ``evidence`` and ``error``; unknown fields are ignored.
        if isinstance(result, Mapping):
            retrieved = _result_value(result, "retrieved_at_utc") or datetime.now(UTC)
            if not isinstance(retrieved, datetime) or retrieved.tzinfo is None:
                retrieved = datetime.now(UTC)
            return ProviderResult(
                data=_result_value(result, "data", _result_value(result, "results", [])),
                evidence=_safe_provider_evidence(_result_value(result, "evidence", [])),
                partial=bool(_result_value(result, "partial", True)),
                error=_result_value(result, "error"),
                retrieved_at_utc=retrieved,
            )
        if result is None:
            result = []
        return ProviderResult(
            data=list(result) if isinstance(result, (list, tuple)) else [result],
            partial=True,
            retrieved_at_utc=datetime.now(UTC),
        )

    search_news = search_web


class FlowerSearchGateway:
    """Small gateway with the same ``cache`` surface expected by health routes."""

    def __init__(self, provider: FlowerSearchProvider | None, *, settings: Any) -> None:
        self.provider = provider
        self.max_entries = max(1, int(getattr(settings, "cache_max_entries", 10_000)))
        self.cache = InMemoryTTLCache(
            max_entries=self.max_entries
        )
        self._entries: dict[str, tuple[float, Any]] = {}
        self.call_count = 0
        self.cache_read_count = 0
        self.cache_hit_count = 0
        self.cache_write_count = 0

    @staticmethod
    def _key(query: Any) -> str:
        if hasattr(query, "model_dump"):
            payload = query.model_dump(mode="json")
        else:
            payload = repr(query)
        return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()

    async def search_web(
        self,
        query: Any,
        budget: RequestBudget,
        *,
        force_refresh: bool = False,
        **_: Any,
    ):
        from apps.api.src.application.ports import ProviderResult
        from apps.api.src.domain.errors import ProviderError

        key = self._key(query)
        now = time.monotonic()
        self.cache_read_count += 1
        if not force_refresh:
            cached = self._entries.get(key)
            if cached and cached[0] > now:
                self.cache_hit_count += 1
                return cached[1]
        # Remove expired entries opportunistically and enforce the same bound
        # advertised by the shared cache object.  The former implementation
        # used an unbounded side dictionary, allowing repeated long-tail
        # searches to grow process memory indefinitely.
        for stale_key, (expires_at, _value) in list(self._entries.items()):
            if expires_at <= now:
                self._entries.pop(stale_key, None)
        if self.provider is None:
            return ProviderResult(
                data=None,
                partial=False,
                error=ProviderError(
                    kind=ProviderErrorKind.AUTH,
                    retryable=False,
                    safe_message="search is not configured",
                ),
                retrieved_at_utc=datetime.now(UTC),
            )
        self.call_count += 1
        raw_result = self.provider.search_web(query, budget)
        result = await raw_result if _is_awaitable(raw_result) else raw_result
        # ``FlowerSearchProvider`` normally returns ProviderResult, but keep
        # injected gateways/adapters compatible with a plain mapping/list.
        if not isinstance(result, ProviderResult):
            if isinstance(result, Mapping):
                retrieved = _result_value(result, "retrieved_at_utc") or datetime.now(UTC)
                if not isinstance(retrieved, datetime) or retrieved.tzinfo is None:
                    retrieved = datetime.now(UTC)
                result = ProviderResult(
                    data=_result_value(result, "data", _result_value(result, "results", [])),
                    evidence=_safe_provider_evidence(_result_value(result, "evidence", [])),
                    partial=bool(_result_value(result, "partial", True)),
                    error=_result_value(result, "error"),
                    retrieved_at_utc=retrieved,
                )
            elif result is None:
                result = ProviderResult(
                    data=[], partial=True, retrieved_at_utc=datetime.now(UTC)
                )
            else:
                result = ProviderResult(
                    data=(
                        list(result)
                        if isinstance(result, (list, tuple))
                        else [result]
                    ),
                    partial=True,
                    retrieved_at_utc=datetime.now(UTC),
                )
        if isinstance(result, ProviderResult) and result.error is None:
            if len(self._entries) >= self.max_entries and key not in self._entries:
                oldest_key = min(self._entries, key=lambda item: self._entries[item][0])
                self._entries.pop(oldest_key, None)
            self._entries[key] = (
                now + 300,
                result.model_copy(update={"capability_issues": []}),
            )
            self.cache_write_count += 1
        return result

    search_news = search_web

    def counters(self) -> dict[str, int]:
        return {
            "provider_call_count": self.call_count,
            "cache_read_count": self.cache_read_count,
            "cache_hit_count": self.cache_hit_count,
            "cache_write_count": self.cache_write_count,
        }


def _notice_for_error(error: Any, *, intelligence: bool = False) -> dict[str, Any]:
    # Provider errors expose ``kind`` while runtime errors generally expose an
    # ErrorCode/string directly.  Normalize both shapes so quota/auth/timeout
    # messages remain actionable instead of being collapsed into an opaque
    # generic failure.
    candidate = getattr(error, "kind", error)
    kind = str(getattr(candidate, "value", candidate or "")).upper()
    if "QUOTA" in kind or "BALANCE" in kind or "CREDIT" in kind:
        kind = "QUOTA_EXHAUSTED"
    elif "AUTH" in kind or "CONFIG" in kind or "KEY" in kind:
        kind = "AUTH"
    elif "RATE" in kind or "BUSY" in kind:
        kind = "RATE_LIMITED"
    elif "TIME" in kind or "DEADLINE" in kind:
        kind = "TIMEOUT"
    if kind == "QUOTA_EXHAUSTED":
        code = "INTELLIGENCE_QUOTA_EXHAUSTED" if intelligence else "SEARCH_QUOTA_EXHAUSTED"
        message = "智能回答额度已用完，请稍后重试或关闭全智能模式。" if intelligence else "在线资料额度已用完，可稍后重试。"
        retryable = False
    elif kind == "AUTH":
        code = "INTELLIGENCE_AUTH_UNAVAILABLE" if intelligence else "SEARCH_AUTH_UNAVAILABLE"
        message = "智能回答服务尚未配置或暂不可用。" if intelligence else "在线资料服务尚未配置或暂不可用。"
        retryable = False
    elif kind in {"RATE_LIMITED", "TIMEOUT"}:
        code = "INTELLIGENCE_TEMPORARILY_UNAVAILABLE" if intelligence else "SEARCH_TEMPORARILY_UNAVAILABLE"
        message = "智能回答服务暂时繁忙，请稍后重试。" if intelligence else "在线资料暂时繁忙，请稍后重试。"
        retryable = True
    else:
        code = "INTELLIGENCE_TEMPORARILY_UNAVAILABLE" if intelligence else "SEARCH_TEMPORARILY_UNAVAILABLE"
        message = "智能回答服务暂时不可用，请稍后重试。" if intelligence else "在线资料暂时不可用，请稍后重试。"
        retryable = True
    return {"code": code, "message": message, "retryable": retryable}


class FlowerChatUseCase:
    """Flower-domain implementation of the synchronous and SSE chat contract."""

    def __init__(
        self,
        *,
        settings: Settings | Any | None = None,
        search_provider: Any | None = None,
        search_gateway: Any | None = None,
        core: FlowerAssistantCore | None = None,
        session_store: InMemorySessionStore | None = None,
        hermes_runtime: HermesAgentRuntime | Any | None = None,
        agent_runtime: Any | None = None,
        clock: Any | None = None,
        legacy_usecase: Any | None = None,
    ) -> None:
        self.settings = settings or Settings()
        # ``datetime.now`` without a timezone returns a naive value and used to
        # make every default request fail at the first timestamp conversion.
        # Keep the clock contract explicit: all internal instants are aware UTC;
        # injected clocks used by deterministic tests must return an aware value.
        self.clock = clock if clock is not None else (lambda: datetime.now(UTC))
        self.core = core or FlowerAssistantCore()
        self.session_store = session_store or InMemorySessionStore(
            ttl_seconds=int(getattr(self.settings, "session_ttl_seconds", 86_400)),
            max_turns=int(getattr(self.settings, "max_session_turns", 8)),
            clock=self.clock,
        )
        self._context_locks: dict[UUID, asyncio.Lock] = {}
        self._history: dict[UUID, list[AgentHistoryMessage]] = {}
        self._history_guard = asyncio.Lock()
        self.search_provider = search_provider
        # A custom gateway may own its provider internally, so checking only
        # ``search_provider`` would incorrectly disable it.  Keep this flag
        # explicit instead of probing the gateway (which could make a network
        # call during construction).
        self._search_configured = search_provider is not None or search_gateway is not None
        self.gateway = search_gateway or FlowerSearchGateway(
            FlowerSearchProvider(search_provider), settings=self.settings
            if search_provider is not None
            else None
        )
        # Expose the legacy use case only to the composition router; it is not
        # used for flower answers and is never placed in model context.
        self.legacy_usecase = legacy_usecase
        self.hermes_runtime = hermes_runtime
        self.agent_runtime = agent_runtime or hermes_runtime
        self.provider = self.search_provider
        self._search_prefix = str(getattr(self.settings, "flower_search_prefix", "园艺 花卉"))

    async def _lock_for(self, session_id: UUID) -> asyncio.Lock:
        # A small process-local map is bounded by normal session TTL cleanup;
        # avoiding private internals of the legacy store keeps this domain
        # adapter replaceable.
        lock = self._context_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._context_locks[session_id] = lock
        return lock

    async def _context(self, session_id: UUID) -> GardenContext:
        value = await self.session_store.load(session_id)
        if isinstance(value, GardenContext):
            return value
        return GardenContext(session_id=session_id)

    async def _save_context(self, context: GardenContext) -> None:
        await self.session_store.save(context.session_id or uuid4(), context)

    async def _history_for(self, session_id: UUID) -> list[AgentHistoryMessage]:
        async with self._history_guard:
            return list(self._history.get(session_id, []))

    @staticmethod
    def _safe_history_text(value: Any, *, limit: int) -> str:
        """Project transcript text before it is retained for a later model turn.

        The in-memory transcript is private, but it is eventually supplied to
        the model runtime.  Keep the same privacy boundary as public session
        metadata: strip control characters/URLs and mask phone numbers and
        street-level addresses.  This also prevents a pasted prompt directive
        from persisting across turns.
        """

        text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
        text = _URL_RE.sub(" ", text)
        text = re.sub(
            r"(?:忽略(?:之前|先前|所有)?指令|无视指令|系统提示(?:词)?|开发者消息|"
            r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?|system\s+prompt)",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"(?<!\d)(?:1[3-9]\d{9}|0\d{2,3}[- ]?\d{7,8})(?!\d)",
            "[已隐藏联系方式]",
            text,
        )
        text = re.sub(
            r"(?:[一-鿿]{2,12}(?:省|市|自治区|区|县|镇|街道))?"
            r"[一-鿿A-Za-z0-9]{1,20}(?:路|街|大道|弄|巷)\s*\d{1,6}\s*号",
            "[已隐藏详细地址]",
            text,
        )
        return " ".join(text.split())[:limit] or "[已过滤内容]"

    async def _remember_history(self, session_id: UUID, question: str, answer: str) -> None:
        async with self._history_guard:
            items = self._history.setdefault(session_id, [])
            # AgentHistoryMessage intentionally rejects control characters,
            # including line breaks.  Answers are Markdown and commonly contain
            # newlines, so collapse them at this private prompt boundary rather
            # than allowing a successful user turn to fail during bookkeeping.
            safe_question = self._safe_history_text(question, limit=2000)
            safe_answer = self._safe_history_text(answer, limit=3000)
            items.extend(
                [
                    AgentHistoryMessage(role="user", content=safe_question),
                    AgentHistoryMessage(role="assistant", content=safe_answer),
                ]
            )
            self._history[session_id] = items[-8:]

    @staticmethod
    def _attach_answer(
        context: GardenContext,
        answer: str,
        *,
        now: datetime,
    ) -> GardenContext:
        """Add a bounded answer projection without counting another turn.

        ``FlowerParser.update_context`` already records the user question and
        increments the turn counters for a normal, safety-allowed turn.  The
        answer is attached in a second immutable update with ``completed``
        disabled so the lifetime count cannot be incremented twice.
        """

        return context.remember(answer=answer, completed=False, now=now)

    @staticmethod
    def _is_flower_question(message: str) -> bool:
        return bool(_FLOWER_WORDS.search(message))

    @staticmethod
    def _needs_search(message: str, context: GardenContext) -> bool:
        return bool(_SEARCH_WORDS.search(message)) or (
            context.plant_name is None and "什么" not in message and len(message) > 10
        )

    async def _search(
        self,
        message: str,
        context: GardenContext,
        budget: RequestBudget,
    ) -> tuple[list[SearchObservation], dict[str, Any] | None]:
        if not self._search_configured:
            return [], None
        from apps.api.src.domain.models import EntityKind, EntityRef, NewsQuery

        subjects: list[EntityRef] = [
            EntityRef(kind=EntityKind.UNKNOWN, canonical_id="flower", display_name="花卉园艺")
        ]
        if context.plant_name:
            subjects.append(
                EntityRef(
                    kind=EntityKind.UNKNOWN,
                    canonical_id="plant",
                    display_name=context.plant_name,
                )
            )
        query_text = _safe_search_text(" ".join(
            item
            for item in (self._search_prefix, message, context.location or "", context.season or "")
            if item
        ), max_length=160)
        if not query_text:
            return [], None
        query = NewsQuery(
            domain="flower",
            subject_refs=subjects,
            keywords=[query_text[:80]],
            limit=5,
        )
        try:
            raw_result = self.gateway.search_web(query, budget=budget)
            result = await raw_result if _is_awaitable(raw_result) else raw_result
        except Exception:
            return [], {
                "code": "SEARCH_TEMPORARILY_UNAVAILABLE",
                "message": "在线资料暂时不可用，请稍后重试。",
                "retryable": True,
            }
        error = _result_value(result, "error")
        if error is not None:
            return [], _notice_for_error(error, intelligence=False)
        evidence_by_id: dict[str, Any] = {}
        for evidence_item in (_result_value(result, "evidence", []) or []):
            evidence_id = (
                evidence_item.get("evidence_id")
                if isinstance(evidence_item, Mapping)
                else getattr(evidence_item, "evidence_id", None)
            )
            if evidence_id is not None:
                evidence_by_id[str(evidence_id)] = evidence_item
        observations: list[SearchObservation] = []
        raw_items = _result_value(result, "data", []) or []
        if isinstance(raw_items, Mapping):
            raw_items = list(raw_items.values())
        elif not isinstance(raw_items, (list, tuple)):
            raw_items = [raw_items]
        for item in list(raw_items)[:5]:
            if isinstance(item, Mapping):
                title_value = item.get("title") or item.get("headline") or "公开资料"
                summary_value = item.get("summary") or item.get("description")
                evidence_id = item.get("evidence_id", "")
            else:
                title_value = getattr(item, "title", "公开资料") or "公开资料"
                summary_value = getattr(item, "summary", None)
                evidence_id = getattr(item, "evidence_id", "")
            evidence = (
                evidence_by_id.get(str(evidence_id))
                if evidence_id is not None
                else None
            )
            if isinstance(evidence, Mapping):
                source_class = evidence.get("source_class", "公开资料")
                source_label = getattr(source_class, "value", source_class)
                source_url = evidence.get("url")
            else:
                source_label = (
                    getattr(getattr(evidence, "source_class", None), "value", "公开资料")
                    if evidence is not None
                    else None
                )
                source_url = getattr(evidence, "url", None) if evidence is not None else None
            try:
                observations.append(
                    SearchObservation(
                        title=title_value,
                        snippet=summary_value or None,
                        source_label=str(source_label) if source_label is not None else None,
                        source_url=source_url,
                        query=query_text,
                        relevance=0.5,
                        verification=VerificationLevel.UNVERIFIED,
                        freshness="unknown",
                    )
                )
            except (TypeError, ValueError):
                # One malformed or prompt-injected result must not discard the
                # clean local answer or make the entire request fail.
                continue
        return observations, None

    @staticmethod
    def _search_observation_payload(observations: list[SearchObservation]) -> dict[str, Any]:
        lines: list[str] = []
        for item in observations[:5]:
            if item.snippet:
                lines.append(f"- {item.title}：{item.snippet}")
            else:
                lines.append(f"- {item.title}")
        return {
            "status": "completed" if observations else "no_data",
            "intent": "flower_search",
            "answer_markdown": "\n".join(lines)[:6000] or "未找到相关资料。",
            "evidence_state": "partial" if observations else "none",
            "data_origin": "search" if observations else "none",
            "coverage": "search_background",
        }

    @staticmethod
    def _search_evidence_sentence(
        observations: list[Any],
    ) -> str | None:
        """Project search observations into one safe, useful background line.

        The deterministic (non-full) path must not become a search-results
        proxy.  Titles, snippets and URLs are untrusted and often contain
        stale or instruction-like text, so they never get copied into the
        public answer.  We only retain a small topic vocabulary and phrase the
        result as conditional background; the local care answer remains the
        actionable source of truth.
        """

        if not observations:
            return None
        topic_patterns = (
            ("浇水", re.compile(r"浇水|盆土|干湿|排水")),
            ("光照", re.compile(r"光照|日照|散射光|遮阴")),
            ("温度", re.compile(r"温度|高温|低温|霜冻|湿度|通风")),
            ("施肥", re.compile(r"施肥|肥料|营养")),
            ("病虫观察", re.compile(r"病虫|蚜虫|红蜘蛛|叶斑|白粉")),
        )
        corpus_parts: list[str] = []
        for item in observations[:5]:
            if isinstance(item, Mapping):
                title = item.get("title") or item.get("headline") or ""
                snippet = item.get("snippet") or item.get("summary") or ""
            else:
                title = getattr(item, "title", "") or ""
                snippet = getattr(item, "snippet", None) or getattr(item, "summary", "") or ""
            corpus_parts.extend((str(title), str(snippet)))
        corpus = " ".join(corpus_parts)
        topics = [label for label, pattern in topic_patterns if pattern.search(corpus)]
        if topics:
            focus = "、".join(topics[:3])
            return (
                f"补充背景：公开资料主要提醒关注{focus}，但做法会随品种和环境变化，"
                "仍应以植株状态和产品标签为准。"
            )
        return (
            "补充背景：公开资料只能作为一般参考，具体安排仍应结合品种、温度和盆土状态调整。"
        )

    @classmethod
    def _append_search_evidence(
        cls,
        answer: str,
        observations: list[Any],
    ) -> str:
        """Keep a local answer and add at most one controlled evidence line."""

        line = cls._search_evidence_sentence(observations)
        base = str(answer or "").strip()
        if not line:
            return base
        if line in base:
            return base
        if not base:
            return line
        return f"{base}\n\n{line}"

    @staticmethod
    def _looks_like_raw_search_answer(
        answer: str | None,
        observations: list[Any],
    ) -> bool:
        """Recognise an Agent echo of the search payload, not a synthesis."""

        text = str(answer or "").strip()
        if not text:
            return False
        if re.search(
            r"(?:补充(?:线索|资料)|公开(?:网页|资料)(?:检索到以下)?(?:线索|摘要|结果)|"
            r"搜索(?:结果|摘要|线索)|检索(?:结果|摘要|线索))\s*[：:]",
            text,
            re.IGNORECASE,
        ):
            return True
        titles: list[str] = []
        raw_payloads: list[str] = []
        has_search_observation = False
        for item in observations[:5]:
            if isinstance(item, Mapping):
                title = item.get("title") or item.get("headline")
                raw_payload = item.get("answer_markdown")
                intent = str(item.get("intent", "")).casefold()
                coverage = str(item.get("coverage", "")).casefold()
                has_search_observation = has_search_observation or (
                    intent in {"flower_search", "web_search"}
                    or coverage == "search_background"
                )
            else:
                title = getattr(item, "title", None)
                raw_payload = getattr(item, "answer_markdown", None)
            if title:
                titles.append(str(title).strip())
            if raw_payload:
                raw_payloads.append(str(raw_payload).strip())
        if titles and any(title and title in text for title in titles):
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            bullets = sum(bool(re.match(r"^(?:[-*]|\d+[.)、])\s+", line)) for line in lines)
            if len(lines) >= 2 and bullets >= max(2, len(lines) // 2):
                return True
        if has_search_observation:
            # The tool projection intentionally exposes only an answer-shaped
            # payload (not provider metadata).  If the model simply mirrors two
            # or more of those bullet lines, it is still an uncomposed search
            # response and should fall back to the local answer.
            normalized_text = re.sub(r"\s+", "", text)
            for payload in raw_payloads:
                normalized_payload = re.sub(r"\s+", "", payload)
                if normalized_payload and normalized_payload in normalized_text:
                    return True
                payload_lines = [line.strip() for line in payload.splitlines() if line.strip()]
                overlap = sum(line and line in text for line in payload_lines)
                if len(payload_lines) >= 2 and overlap >= 2:
                    return True
        return False

    async def _tool_runner(
        self,
        name: str,
        args: dict[str, str],
        *,
        context: GardenContext,
        question: str,
        budget: RequestBudget,
        token: CancelToken,
    ) -> Mapping[str, Any]:
        token.raise_if_cancelled()
        if name == "flower_search":
            observations, notice = await self._search(args.get("query", question), context, budget)
            payload = self._search_observation_payload(observations)
            if notice:
                payload["_public_notices"] = [notice]
            return payload
        plant = args.get("plant") or context.plant_name
        target_question = args.get("question") or question
        local_context = context
        if plant:
            local_context = context.model_copy(update={"plant": plant})
        turn = self.core.answer(
            target_question,
            context=local_context,
            session_id=context.session_id,
            now=_now_utc(self.clock),
        )
        payload: dict[str, Any] = {
            "status": "completed",
            "intent": turn.parse.intent_name.value,
            "answer_markdown": turn.answer_markdown,
            "evidence_state": "verified" if turn.safety_notice is None else "none",
            "data_origin": "local",
            "coverage": "care_plan" if name == "care_plan" else "plant_profile",
            "query_scope": {
                "plant": turn.context.plant_name or plant or "",
                "location": turn.context.location or "",
                "light": str(turn.context.light or ""),
                "container": str(turn.context.container or ""),
                "season": turn.context.season or "",
            },
        }
        return payload

    async def _run_agent(
        self,
        *,
        request_id: UUID,
        session_id: UUID,
        question: str,
        context: GardenContext,
        budget: RequestBudget,
        token: CancelToken,
    ) -> tuple[AgentTurnResult | None, list[dict[str, Any]], list[dict[str, Any]]]:
        runtime = self.agent_runtime
        if runtime is None or not hasattr(runtime, "run"):
            return None, [], []
        model_question = _safe_model_question(question)
        if not model_question:
            return None, [], [
                {
                    "code": "INTELLIGENCE_TEMPORARILY_UNAVAILABLE",
                    "message": "问题内容无法安全提交，请换一种问法。",
                    "retryable": False,
                }
            ]
        deadline = _now_utc(self.clock) + timedelta(
            milliseconds=int(getattr(self.settings, "request_deadline_ms", 10_000))
        )
        async def runner(name: str, args: dict[str, str]) -> Mapping[str, Any]:
            return await self._tool_runner(
                name,
                args,
                context=context,
                question=model_question,
                budget=budget,
                token=token,
            )

        turn = AgentTurnInput(
            request_id=str(request_id),
            opaque_session_id=InMemorySessionStore.hash_session(session_id),
            sanitized_question=model_question,
            timezone="Asia/Shanghai",
            now_beijing=format_beijing(_now_utc(self.clock)),
            context_hint=context.summary(),
            conversation_history=await self._history_for(session_id),
            deadline_at_utc=deadline,
            max_iterations=min(int(getattr(self.settings, "agent_max_iterations", 4)), 4),
            max_tool_calls=min(int(getattr(self.settings, "agent_max_tool_calls", 4)), 4),
        )
        try:
            result = await runtime.run(turn, tool_runner=runner, cancel=token)
        except Exception:
            return None, [], [{"code": "INTELLIGENCE_TEMPORARILY_UNAVAILABLE", "message": "智能回答服务暂时不可用，请稍后重试。", "retryable": True}]
        notices: list[dict[str, Any]] = []
        if result is None:
            return None, [], [
                {
                    "code": "INTELLIGENCE_TEMPORARILY_UNAVAILABLE",
                    "message": "智能回答服务暂时不可用，已先给出本地养护建议。",
                    "retryable": True,
                }
            ]
        # A tool-level search failure can be carried in ``tool_calls`` even
        # when Hermes still produces a useful answer (or normalises the outer
        # error to ``COMPOSER_UNAVAILABLE``). Inspect both layers and expose
        # only the closed, provider-neutral notice vocabulary. In particular,
        # do not relabel search quota exhaustion as an intelligence failure.
        seen_notice_codes: set[str] = set()

        def add_notice(error: Any, *, intelligence: bool) -> None:
            notice = _notice_for_error(error, intelligence=intelligence)
            code = str(notice.get("code") or "").upper()
            if code and code not in seen_notice_codes:
                seen_notice_codes.add(code)
                notices.append(notice)

        result_error = getattr(result, "error_code", None)
        if result_error is not None:
            result_error_text = str(
                getattr(result_error, "value", result_error) or ""
            ).upper()
            add_notice(
                result_error,
                intelligence=not result_error_text.startswith("SEARCH_"),
            )

        observed_status = getattr(result, "status", None)
        observed_status_value = str(
            getattr(observed_status, "value", observed_status or "")
        ).upper()
        if observed_status_value not in {"", RuntimeStatus.OK.value}:
            add_notice(
                SimpleNamespace(
                    kind=(
                        "TIMEOUT"
                        if "TIME" in observed_status_value
                        else "HTTP"
                    )
                ),
                intelligence=True,
            )

        for tool_call in list(getattr(result, "tool_calls", []) or []):
            raw_code = getattr(tool_call, "error_code", None)
            if raw_code is None and isinstance(tool_call, Mapping):
                raw_code = tool_call.get("error_code")
            if raw_code is None:
                continue
            code_text = str(getattr(raw_code, "value", raw_code) or "").upper()
            if not code_text:
                continue
            tool_name = str(
                getattr(tool_call, "tool_name", None)
                or (
                    tool_call.get("tool_name")
                    if isinstance(tool_call, Mapping)
                    else ""
                )
            ).casefold()
            is_search_failure = tool_name == "flower_search" or code_text.startswith(
                "SEARCH_"
            )
            # Unknown tool error strings are intentionally ignored; the
            # runtime has already mapped them to a generic notice, and copying
            # arbitrary text would widen the public boundary.
            if code_text in {
                "SEARCH_QUOTA_EXHAUSTED",
                "SEARCH_AUTH_UNAVAILABLE",
                "SEARCH_TEMPORARILY_UNAVAILABLE",
                "UPSTREAM_TIMEOUT",
                "UPSTREAM_RATE_LIMITED",
                "UPSTREAM_AUTH",
                "COMPOSER_UNAVAILABLE",
                "INVALID_UPSTREAM_DATA",
            }:
                add_notice(raw_code, intelligence=not is_search_failure)
        if (
            getattr(result, "answer_markdown", None)
            and observed_status_value == RuntimeStatus.OK.value
            and any(code.startswith("SEARCH_") for code in seen_notice_codes)
        ):
            # The runtime may normalise a failed search tool call to the broad
            # COMPOSER_UNAVAILABLE code even though the model completed a
            # useful response.  In that case the actionable condition is the
            # search capability, not an intelligence outage.
            notices = [
                notice
                for notice in notices
                if notice.get("code") != "INTELLIGENCE_TEMPORARILY_UNAVAILABLE"
            ]
            seen_notice_codes.discard("INTELLIGENCE_TEMPORARILY_UNAVAILABLE")
        if (
            not getattr(result, "answer_markdown", None)
            and not notices
            and str(getattr(result, "status", "")).upper() == RuntimeStatus.OK.value
        ):
            # A successful transport with no user-facing text is still an
            # unusable model turn.  Do not silently report it as a completed
            # intelligent answer; the caller will keep the deterministic one.
            notices.append(
                {
                    "code": "INTELLIGENCE_TEMPORARILY_UNAVAILABLE",
                    "message": "智能回答服务暂时不可用，已先给出本地养护建议。",
                    "retryable": True,
                }
            )
        observations = list(getattr(result, "observations", []) or [])
        return result, observations, notices

    @staticmethod
    def _clean_model_answer(answer: str | None) -> str | None:
        cleaned = sanitise_agent_answer(answer)
        if not cleaned:
            return None
        # The runtime sanitizer removes known implementation tokens, but keep
        # a final conservative check for obfuscated/Chinese variants.
        if contains_public_implementation_leak(cleaned) or _PRIVATE_RE.search(cleaned):
            return None
        return _URL_RE.sub("", cleaned).strip() or None

    async def _emit(self, sink: Any, event: str, payload: Mapping[str, Any]) -> None:
        if sink is None:
            return
        result = sink.emit(event, payload) if hasattr(sink, "emit") else sink(event, payload)
        if asyncio.iscoroutine(result):
            await result

    async def _replay(self, sink: Any, result: ChatResult) -> None:
        await self._emit(sink, "run.started", {"request_id": result.request_id, "session_id": result.session_id})
        if result.error:
            await self._emit(sink, "run.error", result.to_dict())
        else:
            await self._emit(sink, "message.completed", result.to_dict())

    @staticmethod
    def _garden_context_projection(
        context: GardenContext | None,
    ) -> dict[str, Any] | None:
        """Project only coarse, user-useful garden state to the wire envelope.

        ``GardenContext`` is server-owned and also contains bounded transcript
        fields used for session metadata.  Returning a hand-written allow-list
        here prevents those fields (and a manually injected precise address)
        from reaching HTTP/SSE clients or being copied into UI state.
        """

        if not isinstance(context, GardenContext):
            return None

        def text(value: Any, limit: int) -> str | None:
            if value is None:
                return None
            candidate = " ".join(str(value).replace("\r", " ").replace("\n", " ").split())
            if not candidate or _URL_RE.search(candidate):
                candidate = _URL_RE.sub("", candidate).strip()
            return candidate[:limit] or None

        location = text(context.location, 80)
        if location and re.search(r"\d|(?:路|街|大道|弄|巷|号|栋|单元|室)", location):
            # Keep a known city when one is embedded in a more precise address;
            # otherwise omit the location rather than echoing street details.
            location = next(
                (
                    city
                    for city in (
                        "北京",
                        "上海",
                        "广州",
                        "深圳",
                        "杭州",
                        "南京",
                        "苏州",
                        "成都",
                        "重庆",
                        "武汉",
                        "西安",
                        "天津",
                        "青岛",
                        "厦门",
                        "昆明",
                        "香港",
                        "澳门",
                        "台北",
                    )
                    if city in location
                ),
                None,
            )
        light = context.light.value if hasattr(context.light, "value") else context.light
        container = (
            context.container.type.value
            if hasattr(context.container, "type")
            and hasattr(context.container.type, "value")
            else context.container
        )
        projection: dict[str, Any] = {
            "plant_name": text(context.plant_name, 100),
            "location": location,
            "climate": text(context.climate, 80),
            "light": text(light, 80),
            "container": text(container, 80),
            "season": text(context.season, 40),
        }
        # Keep the shape stable but avoid emitting an empty object for a fresh
        # session, which is less useful to clients than ``null``.
        return projection if any(value is not None for value in projection.values()) else None

    def _result(
        self,
        request_id: UUID,
        session_id: UUID,
        status: str,
        answer: str,
        started: float,
        *,
        context: GardenContext | None = None,
        evidence_state: str = "verified",
        data_origin: str = "local",
        notices: list[dict[str, Any]] | None = None,
        composition: dict[str, Any] | None = None,
        follow_up: str | None = None,
    ) -> ChatResult:
        return ChatResult(
            request_id=request_id,
            session_id=session_id,
            status=status,
            answer_markdown=answer,
            blocks=[AnswerBlock(type=AnswerBlockType.TEXT, content=answer)],
            # Even local/offline answers need a truthful observation timestamp
            # so the UI does not display an empty or stale sports-era value.
            as_of_beijing=format_beijing(_now_utc(self.clock)),
            evidence_state=evidence_state,
            data_origin=data_origin,
            follow_up=follow_up,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            notices=notices or [],
            garden_context=self._garden_context_projection(context),
            composition=composition
            or {"mode": "deterministic", "status": "not_requested", "latency_ms": 0},
        )

    @staticmethod
    def _agent_observation_summary(
        observations: list[dict[str, Any]],
        result: AgentTurnResult | Any | None = None,
    ) -> tuple[str, str]:
        """Collapse model observations into public evidence/origin labels.

        Tool payloads are untrusted and may use either enum or string values.
        Only the small allow-list below influences the response metadata; raw
        provider fields never cross the API boundary.
        """

        states: set[str] = set()
        origins: set[str] = set()
        for item in observations:
            if not isinstance(item, Mapping):
                continue
            state = str(item.get("evidence_state", "none")).lower()
            if state in {"verified", "partial", "unverified", "none"}:
                states.add(state)
            origin = str(item.get("data_origin", "none")).lower()
            if origin in {"local", "search", "public", "cache", "mixed"}:
                origins.add(origin)
            coverage = str(item.get("coverage", "")).lower()
            if coverage == "search_background":
                origins.add("search")
        result_state = str(
            getattr(result, "evidence_state", "none") if result is not None else "none"
        ).lower()
        if result_state in {"verified", "partial", "unverified", "none"}:
            states.add(result_state)
        if "partial" in states or "unverified" in states:
            evidence = "partial"
        elif states == {"verified"}:
            evidence = "verified"
        else:
            evidence = "none"
        if "search" in origins or "public" in origins:
            origin = "mixed" if "local" in origins or result is not None else "search"
        elif "mixed" in origins:
            origin = "mixed"
        elif "cache" in origins:
            origin = "cache"
        elif "local" in origins:
            origin = "local"
        else:
            origin = "none"
        return evidence, origin

    async def handle(
        self,
        request: Any,
        *,
        event_sink: Any | None = None,
        cancel: CancelToken | None = None,
        request_id: UUID | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Run one turn and release an idempotency reservation on cancellation.

        The implementation keeps the long state-machine body in
        ``_handle_impl``.  This thin boundary is intentionally responsible for
        the one cross-cutting invariant that is otherwise easy to miss when a
        provider/model task is cancelled: an in-flight client key must not stay
        reserved forever and make every later retry appear busy.
        """

        session_id: UUID | None = None
        client_id: str | None = None
        wrapper_started = time.monotonic()
        try:
            if hasattr(request, "session_id"):
                session_id = getattr(request, "session_id", None)
                client_id = getattr(request, "client_message_id", None)
            elif isinstance(request, Mapping):
                raw_session = request.get("session_id")
                if raw_session:
                    session_id = raw_session if isinstance(raw_session, UUID) else UUID(str(raw_session))
                raw_client = request.get("client_message_id")
                client_id = str(raw_client).strip() if raw_client else None
        except (TypeError, ValueError, AttributeError):
            session_id = None
            client_id = None
        # Allocate an implicit session at this outer boundary as well.  That
        # gives the cancellation handler the same identifier used by the
        # implementation, including requests that omit ``session_id``.
        if session_id is None:
            generated_session = uuid4()
            try:
                if hasattr(request, "model_copy"):
                    request = request.model_copy(update={"session_id": generated_session})
                    session_id = generated_session
                elif isinstance(request, Mapping):
                    request = {**request, "session_id": generated_session}
                    session_id = generated_session
            except Exception:
                # Invalid/custom request objects are still validated by the
                # implementation; do not let this convenience path mask the
                # proper public payload error.
                pass
        try:
            return await self._handle_impl(
                request,
                event_sink=event_sink,
                cancel=cancel,
                request_id=request_id,
                **kwargs,
            )
        except asyncio.CancelledError:
            if session_id is not None and client_id:
                try:
                    await self.session_store.fail_idempotency(session_id, client_id)
                except Exception:
                    # Cancellation must remain cancellable even if a custom
                    # store is already shutting down.
                    pass
            raise
        except Exception:
            if session_id is not None and client_id:
                try:
                    await self.session_store.fail_idempotency(session_id, client_id)
                except Exception:
                    pass
            result = self._result(
                request_id or uuid4(),
                session_id or uuid4(),
                "failed",
                "服务暂时不可用，请稍后重试。",
                wrapper_started,
                evidence_state="none",
                data_origin="none",
            )
            result.error = {
                "code": "SERVICE_BUSY",
                "retryable": True,
                "message": "服务暂时不可用，请稍后重试。",
            }
            try:
                await self._emit(event_sink, "run.error", result.to_dict())
            except Exception:
                pass
            return result

    async def _handle_impl(
        self,
        request: Any,
        *,
        event_sink: Any | None = None,
        cancel: CancelToken | None = None,
        request_id: UUID | None = None,
        **_: Any,
    ) -> ChatResult:
        started = time.monotonic()
        token = cancel or CancelToken()
        try:
            from apps.api.src.api.schemas import ChatRequest as WireChatRequest

            req = request if hasattr(request, "message") else WireChatRequest.model_validate(request)
            question = str(req.message).strip()
            session_id = req.session_id or uuid4()
        except Exception:
            return self._result(
                request_id or uuid4(), uuid4(), "failed", "请求格式不正确，请重试。", started,
                evidence_state="none",
            )
        request_id = request_id or uuid4()
        client_id = getattr(req, "client_message_id", None)
        if client_id:
            message_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
            owner, record = await self.session_store.reserve_idempotency(
                session_id, client_id, request_id, message_hash=message_hash
            )
            if not owner:
                if record.message_hash and record.message_hash != message_hash:
                    # Reusing an idempotency key for different content is a
                    # client payload conflict, not a transient provider
                    # failure.  Keep the sync and SSE projections identical:
                    # emit the normal start/error sequence and attach the
                    # canonical INVALID_PAYLOAD envelope to the result so the
                    # HTTP route maps it to 400 instead of silently treating a
                    # failed conversational result as a success.
                    conflict = self._result(
                        request_id,
                        session_id,
                        "failed",
                        "该请求标识已用于其他问题，请换一个请求标识。",
                        started,
                        evidence_state="none",
                        data_origin="none",
                    )
                    conflict.error = {
                        "code": "INVALID_PAYLOAD",
                        "retryable": False,
                        "message": "该请求标识已用于其他问题，请换一个请求标识。",
                    }
                    await self._emit(
                        event_sink,
                        "run.started",
                        {"request_id": request_id, "session_id": session_id},
                    )
                    await self._emit(event_sink, "run.error", conflict.to_dict())
                    return conflict
                replay = await self.session_store.replay_or_wait(session_id, client_id, timeout=10)
                if isinstance(replay, ChatResult):
                    await self._replay(event_sink, replay)
                    return replay
                # The original owner may still be running after the bounded
                # wait.  Starting the same payload again would execute model
                # and search calls twice and could overwrite the newer garden
                # context.  Return a retryable busy result until that owner
                # completes (or explicitly fails its idempotency record).
                if getattr(record, "state", "in_flight") != "completed":
                    busy = self._result(
                        request_id,
                        session_id,
                        "failed",
                        "请求仍在处理中，请稍后重试。",
                        started,
                        evidence_state="none",
                        data_origin="none",
                    )
                    busy.error = {
                        "code": "SERVICE_BUSY",
                        "retryable": True,
                        "message": "请求仍在处理中，请稍后重试。",
                    }
                    await self._replay(event_sink, busy)
                    return busy
        await self._emit(event_sink, "run.started", {"request_id": request_id, "session_id": session_id})
        token.raise_if_cancelled()
        lock = await self._lock_for(session_id)
        async with lock:
            context = await self._context(session_id)
            # Resolve the requested/effective mode before any domain parsing.
            # Session metadata questions are application-state queries and must
            # not be sent through the plant parser, search adapter, or Hermes.
            full_requested = (
                str(
                    getattr(
                        getattr(req, "intelligence_mode", None),
                        "value",
                        getattr(req, "intelligence_mode", ""),
                    )
                    or getattr(self.settings, "default_intelligence_mode", "hybrid")
                ).lower()
                == "full"
            )
            full_enabled = bool(getattr(self.settings, "full_intelligence_enabled", False))
            meta_query = classify_flower_session_meta_question(question)
            if meta_query is not None:
                effective_full = (
                    full_requested
                    and full_enabled
                    and self.agent_runtime is not None
                )
                answer = render_flower_session_meta_answer(
                    meta_query,
                    context,
                    requested_full=full_requested,
                    effective_full=effective_full,
                )
                # Metadata is a normal, safe conversational turn.  Commit it
                # to the bounded question/answer projections so a subsequent
                # “上一问/上一条回答” is deterministic and session-local.
                meta_context = context.remember(
                    question=question,
                    answer=answer,
                    now=_now_utc(self.clock),
                )
                result = self._result(
                    request_id,
                    session_id,
                    "completed",
                    answer,
                    started,
                    context=meta_context,
                    evidence_state="none",
                    data_origin="local",
                    composition={
                        "mode": "deterministic",
                        "status": "not_requested",
                        "latency_ms": 0,
                    },
                )
                await self._save_context(meta_context)
                await self._remember_history(session_id, question, answer)
                if client_id:
                    await self.session_store.complete_idempotency(session_id, client_id, result)
                await self._emit(event_sink, "message.completed", result.to_dict())
                return result
            # Safety is checked before search/model invocation by the local core.
            local_turn = self.core.answer(
                question, context=context, session_id=session_id, now=_now_utc(self.clock)
            )
            updated_context = local_turn.context
            if local_turn.safety_notice is not None:
                answer = local_turn.answer_markdown
                result = self._result(
                    request_id, session_id, "blocked", answer, started,
                    # A blocked request must not mutate plant/session memory.
                    # In particular, its question must not become visible to
                    # a later “我问了几个问题” metadata query.
                    context=context, evidence_state="none",
                )
                await self._save_context(context)
                if client_id:
                    await self.session_store.complete_idempotency(session_id, client_id, result)
                await self._emit(event_sink, "safety.blocked", {"message": answer})
                await self._emit(event_sink, "message.completed", result.to_dict())
                return result
            notices: list[dict[str, Any]] = []
            answer = local_turn.answer_markdown
            evidence = "verified" if local_turn.parse.resolved_plant is not None or local_turn.parse.intent_name is FlowerIntentName.PLANT_SELECTION else "none"
            data_origin = "local"
            composition = {"mode": "deterministic", "status": "not_requested", "latency_ms": 0}
            if full_requested and full_enabled and self.agent_runtime is not None:
                await self._emit(event_sink, "run.status", {"stage": "agent_planning", "text": "正在理解你的种植问题"})
                budget = RequestBudget(
                    _now_utc(self.clock) + timedelta(milliseconds=int(getattr(self.settings, "request_deadline_ms", 10_000))),
                    max_provider_operations=int(getattr(self.settings, "max_provider_operations", 4)),
                    max_retries_per_operation=int(getattr(self.settings, "provider_max_retries", 2)),
                    clock=self.clock,
                )
                agent_result, observations, agent_notices = await self._run_agent(
                    request_id=request_id,
                    session_id=session_id,
                    question=question,
                    # Include slots resolved from the current turn.  Passing
                    # the pre-turn context made an explicit “我在上海养绣球”
                    # invisible to Hermes on that same request.
                    context=updated_context,
                    budget=budget,
                    token=token,
                )
                notices.extend(agent_notices)
                candidate = (
                    self._clean_model_answer(getattr(agent_result, "answer_markdown", None))
                    if agent_result
                    else None
                )
                candidate_is_raw_search = self._looks_like_raw_search_answer(
                    candidate, observations
                )
                if (
                    candidate
                    and not candidate_is_raw_search
                    and not (
                        candidate.startswith("请告诉我")
                        and local_turn.parse.resolved_plant is not None
                    )
                ):
                    answer = candidate
                    composition = {"mode": "agent", "status": "used", "latency_ms": int(getattr(agent_result, "latency_ms", 0))}
                    agent_evidence, agent_origin = self._agent_observation_summary(
                        observations, agent_result
                    )
                    if agent_evidence == "partial" or evidence == "partial":
                        evidence = "partial"
                    elif agent_evidence == "verified":
                        evidence = "verified"
                    if agent_origin in {"search", "mixed", "public", "cache"}:
                        data_origin = "mixed" if agent_origin != "cache" else "cache"
                    elif agent_origin == "local":
                        data_origin = "local"
                else:
                    if observations and (candidate_is_raw_search or not candidate):
                        # A weak model may echo the bounded search payload.
                        # Keep the deterministic care answer and expose only
                        # one controlled background sentence instead of a
                        # title/summary dump.
                        answer = self._append_search_evidence(answer, observations)
                        evidence = "partial"
                        data_origin = "mixed"
                    composition = {"mode": "fallback", "status": "fallback", "latency_ms": int(getattr(agent_result, "latency_ms", 0)) if agent_result else 0}
            elif full_requested and not full_enabled:
                # An explicit user choice should never be silently ignored when
                # the deployment has disabled the model path.  Keep the useful
                # local answer and expose a retryable, provider-neutral notice.
                notices.append(
                    {
                        "code": "INTELLIGENCE_TEMPORARILY_UNAVAILABLE",
                        "message": "全智能模式当前不可用，已先给出本地养护建议。",
                        "retryable": True,
                    }
                )
                composition = {"mode": "fallback", "status": "fallback", "latency_ms": 0}
            elif full_requested and full_enabled and self.agent_runtime is None:
                # A requested full-intelligence turn must not look successful
                # merely because the runtime was not wired.  Keep the local
                # answer, but expose a stable, actionable capability notice.
                notices.append(
                    {
                        "code": "INTELLIGENCE_TEMPORARILY_UNAVAILABLE",
                        "message": "智能回答服务暂时不可用，已先给出本地养护建议。",
                        "retryable": True,
                    }
                )
                composition = {"mode": "fallback", "status": "fallback", "latency_ms": 0}
            elif self._needs_search(question, updated_context):
                budget = RequestBudget(
                    _now_utc(self.clock) + timedelta(milliseconds=int(getattr(self.settings, "request_deadline_ms", 10_000))),
                    max_provider_operations=int(getattr(self.settings, "max_provider_operations", 4)),
                    max_retries_per_operation=int(getattr(self.settings, "provider_max_retries", 2)),
                    clock=self.clock,
                )
                _observations, search_notice = await self._search(
                    question, updated_context, budget
                )
                if search_notice:
                    notices.append(search_notice)
                    evidence = "partial"
                if _observations:
                    evidence = "partial"
                    # Non-full mode has no generative synthesis step.  Do not
                    # discard successful fresh observations, but also do not
                    # expose raw result titles/URLs; retain the local answer
                    # and add one bounded, conditional background sentence.
                    answer = self._append_search_evidence(answer, _observations)
                    data_origin = "mixed"
            status = "blocked" if local_turn.safety_notice else (
                "needs_clarification" if local_turn.parse.missing_slots and not answer.strip().startswith("按目前") else "completed"
            )
            follow_up = None
            if local_turn.parse.missing_slots:
                follow_up = local_turn.parse.clarification
            # The parser has already recorded this safe question and advanced
            # the completed-turn counter.  Retain only a bounded answer
            # projection for deterministic session-meta queries; do not count
            # the answer as a second user turn.
            updated_context = self._attach_answer(
                updated_context,
                answer,
                now=_now_utc(self.clock),
            )
            result = self._result(
                request_id, session_id, status, answer, started,
                context=updated_context, evidence_state=evidence,
                data_origin=data_origin,
                notices=notices, composition=composition, follow_up=follow_up,
            )
            await self._save_context(updated_context)
            await self._remember_history(session_id, question, answer)
            if client_id:
                await self.session_store.complete_idempotency(session_id, client_id, result)
            await self._emit(event_sink, "message.completed", result.to_dict())
            return result


__all__ = ["FlowerChatUseCase", "FlowerSearchGateway", "FlowerSearchProvider"]
