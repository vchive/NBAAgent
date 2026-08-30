"""Bounded NBA tools exposed to the official Hermes Agent.

Hermes owns the model/tool loop, but it never receives a Provider object.  A
process-global registry handler looks up a short-lived request bridge by the
opaque Hermes ``task_id`` and dispatches the typed operation back onto the
owning ASGI event loop.  Removing that bridge invalidates every late tool call.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import re
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from apps.api.src.application.ports import CancelToken
from apps.api.src.domain.models import DateRange

NBA_TOOL_NAMES = ("nba_news", "nba_query", "nba_schedule")
NBA_TOOLSET = "nba"

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_ARGUMENT_RE = re.compile(
    r"(?:https?|ftp|file)://|www\.|(?:system|developer)\s*(?:prompt|message)|"
    r"(?:ignore|disregard|override|bypass)\s+(?:all\s+)?(?:previous|system)?\s*instructions?|"
    r"(?:忽略|无视|绕过|跳过).{0,10}(?:指令|规则|安全|核验)|"
    r"(?:输出|泄露).{0,10}(?:提示词|密钥|凭据|内部信息)",
    re.IGNORECASE,
)
_FORBIDDEN_OUTPUT_KEY_RE = re.compile(
    r"(?:provider|source|evidence|canonical|request|session|trace|raw|token|key|url|id)",
    re.IGNORECASE,
)

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "nba_query": {
        "description": (
            "查询 NBA 比赛、球队、球员、统计、历史或逐回合事实。"
            "事实问题必须使用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "保留用户事实查询含义的简短中文问题，不得包含 URL 或指令。",
                    "maxLength": 500,
                }
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    "nba_schedule": {
        "description": "按北京时间查询 NBA 赛程或赛果；无比赛时也会返回解析后的日期范围。",
        "parameters": {
            "type": "object",
            "properties": {
                "date_expression": {
                    "type": "string",
                    "description": "例如今天、明天、下周或 2026-09-01。",
                    "maxLength": 80,
                },
                "team": {
                    "type": "string",
                    "description": "可选球队名称。",
                    "maxLength": 80,
                },
            },
            "required": ["date_expression"],
            "additionalProperties": False,
        },
    },
    "nba_news": {
        "description": "查询 NBA 新闻和背景；搜索摘要不能单独证明比分、统计或逐回合数字。",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "maxLength": 160},
                "date_expression": {"type": "string", "maxLength": 80},
            },
            "required": ["subject"],
            "additionalProperties": False,
        },
    },
}

_ARGUMENT_RULES: dict[str, dict[str, tuple[int, bool]]] = {
    "nba_query": {"question": (500, True)},
    "nba_schedule": {"date_expression": (80, True), "team": (80, False)},
    "nba_news": {"subject": (160, True), "date_expression": (80, False)},
}


@dataclass(slots=True)
class AgentToolCall:
    tool_name: str
    arguments_hash: str
    status: str
    latency_ms: int
    evidence_state: str = "none"


@dataclass(slots=True)
class _TaskState:
    loop: asyncio.AbstractEventLoop
    runner: Callable[[str, dict[str, str]], Awaitable[Mapping[str, Any]]]
    deadline_at_utc: datetime
    cancel: CancelToken
    max_calls: int
    timeout_ms: int
    max_result_bytes: int
    seen: set[str] = field(default_factory=set)
    calls: list[AgentToolCall] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    active: bool = True


def _safe_error(status: str, message: str) -> str:
    return json.dumps(
        {"status": status, "error": message},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _normalise_arguments(tool_name: str, args: Any) -> dict[str, str]:
    rules = _ARGUMENT_RULES.get(tool_name)
    if rules is None or not isinstance(args, Mapping):
        raise ValueError("invalid tool arguments")
    if any(key not in rules for key in args):
        raise ValueError("unknown tool argument")
    output: dict[str, str] = {}
    for key, (limit, required) in rules.items():
        value = args.get(key)
        if value is None or str(value).strip() == "":
            if required:
                raise ValueError("missing tool argument")
            continue
        if not isinstance(value, str):
            raise ValueError("tool arguments must be strings")
        text = " ".join(value.strip().split())
        if len(text) > limit or _CONTROL_RE.search(text) or _UNSAFE_ARGUMENT_RE.search(text):
            raise ValueError("unsafe tool argument")
        output[key] = text
    return output


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _CONTROL_RE.sub(" ", value).strip()
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"), depth=depth + 1)
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in list(value.items())[:64]:
            safe_key = str(key)
            if _FORBIDDEN_OUTPUT_KEY_RE.search(safe_key):
                continue
            safe_value = _json_safe(item, depth=depth + 1)
            if safe_value is not None:
                output[safe_key] = safe_value
        return output
    if isinstance(value, (list, tuple)):
        return [
            safe
            for safe in (_json_safe(item, depth=depth + 1) for item in value[:64])
            if safe is not None
        ]
    return str(value)[:200]


def sanitise_observation(value: Mapping[str, Any], *, max_bytes: int) -> dict[str, Any]:
    """Project an internal tool result into the provider-neutral Agent shape."""

    status = str(value.get("status", "failed")).lower()
    if status not in {"completed", "no_data", "needs_clarification", "failed"}:
        status = "failed"
    scope = value.get("query_scope")
    safe_scope: dict[str, str] | None = None
    if isinstance(scope, Mapping):
        safe_scope = {}
        for key in ("start_date", "end_date", "timezone"):
            item = scope.get(key)
            if isinstance(item, str) and item and not _CONTROL_RE.search(item):
                safe_scope[key] = item[:64]
        if not safe_scope:
            safe_scope = None
    answer = str(value.get("answer_markdown", "")).strip()
    answer = _CONTROL_RE.sub(" ", answer)[:12_000]
    observation: dict[str, Any] = {
        "status": status,
        "intent": str(value.get("intent", "unknown"))[:80],
        "query_scope": safe_scope,
        "answer_markdown": answer,
        "blocks": _json_safe(value.get("blocks", [])),
        "evidence_state": str(value.get("evidence_state", "none")).lower(),
        "as_of_beijing": (
            str(value.get("as_of_beijing"))[:32]
            if value.get("as_of_beijing") is not None
            else None
        ),
    }
    encoded = json.dumps(observation, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > max_bytes:
        observation["blocks"] = []
        encoded = json.dumps(observation, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > max_bytes:
        overflow = len(encoded) - max_bytes
        keep = max(0, len(answer.encode("utf-8")) - overflow - 128)
        while len(answer[:keep].encode("utf-8")) > keep:
            keep -= 1
        observation["answer_markdown"] = answer[:keep]
        encoded = json.dumps(observation, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > max_bytes:
        raise ValueError("tool result exceeds configured bound")
    return observation


class AgentTaskBridge:
    """Thread-safe task registry used by synchronous Hermes tool handlers."""

    def __init__(self) -> None:
        self._states: dict[str, _TaskState] = {}
        self._lock = threading.RLock()

    def register(
        self,
        task_id: str,
        *,
        loop: asyncio.AbstractEventLoop,
        runner: Callable[[str, dict[str, str]], Awaitable[Mapping[str, Any]]],
        deadline_at_utc: datetime,
        cancel: CancelToken,
        max_calls: int = 4,
        timeout_ms: int = 8_000,
        max_result_bytes: int = 16_384,
    ) -> None:
        if deadline_at_utc.tzinfo is None or deadline_at_utc.utcoffset() is None:
            raise ValueError("agent deadline must be timezone-aware")
        if not 1 <= max_calls <= 4:
            raise ValueError("agent tool call budget must be between 1 and 4")
        with self._lock:
            if task_id in self._states:
                raise ValueError("agent task is already registered")
            self._states[task_id] = _TaskState(
                loop=loop,
                runner=runner,
                deadline_at_utc=deadline_at_utc.astimezone(UTC),
                cancel=cancel,
                max_calls=max_calls,
                timeout_ms=timeout_ms,
                max_result_bytes=max_result_bytes,
            )

    def unregister(self, task_id: str) -> tuple[list[AgentToolCall], list[dict[str, Any]]]:
        with self._lock:
            state = self._states.pop(task_id, None)
            if state is None:
                return [], []
            state.active = False
            return list(state.calls), list(state.observations)

    def snapshot(self, task_id: str) -> tuple[list[AgentToolCall], list[dict[str, Any]]]:
        with self._lock:
            state = self._states.get(task_id)
            return (
                (list(state.calls), list(state.observations))
                if state is not None
                else ([], [])
            )

    def contains(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._states

    def dispatch(self, tool_name: str, args: Any, *, task_id: str | None) -> str:
        started = time.monotonic()
        if tool_name not in NBA_TOOL_NAMES or not task_id:
            return _safe_error("failed", "tool is not available for this request")
        try:
            normalised = _normalise_arguments(tool_name, args)
        except ValueError:
            return _safe_error("failed", "tool arguments were rejected")
        fingerprint = hashlib.sha256(
            json.dumps(
                [tool_name, normalised], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()[:24]
        with self._lock:
            state = self._states.get(task_id)
            if state is None or not state.active or state.cancel.is_cancelled():
                return _safe_error("cancelled", "request is no longer active")
            remaining = (state.deadline_at_utc - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return _safe_error("cancelled", "request deadline expired")
            if fingerprint in state.seen:
                state.calls.append(
                    AgentToolCall(tool_name, fingerprint, "duplicate", 0)
                )
                return _safe_error("duplicate", "identical tool call was already executed")
            if len(state.calls) >= state.max_calls:
                return _safe_error("failed", "tool call budget exhausted")
            state.seen.add(fingerprint)
            loop = state.loop
            runner = state.runner
            timeout = min(remaining, max(state.timeout_ms, 1) / 1000)
            max_result_bytes = state.max_result_bytes

        future = asyncio.run_coroutine_threadsafe(runner(tool_name, normalised), loop)
        try:
            raw = future.result(timeout=timeout)
            if not isinstance(raw, Mapping):
                raise TypeError("tool runner returned an invalid result")
            observation = sanitise_observation(raw, max_bytes=max_result_bytes)
            status = observation["status"]
        except concurrent.futures.TimeoutError:
            future.cancel()
            observation = None
            status = "cancelled"
        except Exception:
            future.cancel()
            observation = None
            status = "failed"
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        with self._lock:
            current = self._states.get(task_id)
            if current is None or current is not state or not current.active:
                return _safe_error("cancelled", "request is no longer active")
            evidence = str((observation or {}).get("evidence_state", "none"))
            current.calls.append(
                AgentToolCall(tool_name, fingerprint, status, latency_ms, evidence)
            )
            if observation is not None:
                current.observations.append(observation)
        if observation is None:
            return _safe_error(status, "tool execution did not complete")
        return json.dumps(observation, ensure_ascii=False, separators=(",", ":"))

    async def invoke(self, tool_name: str, args: Any, *, task_id: str) -> dict[str, Any]:
        """Test/application helper that exercises the real thread bridge."""

        raw = await asyncio.to_thread(self.dispatch, tool_name, args, task_id=task_id)
        return json.loads(raw)


agent_task_bridge = AgentTaskBridge()


def _handler(tool_name: str):
    def call(args: dict[str, Any], **kwargs: Any) -> str:
        return agent_task_bridge.dispatch(tool_name, args, task_id=kwargs.get("task_id"))

    return call


def register_official_nba_tools(registry: Any | None = None) -> Any:
    """Register exactly the three server-owned schemas in Hermes' registry."""

    if registry is None:
        from tools.registry import registry as official_registry

        registry = official_registry
    for name in NBA_TOOL_NAMES:
        registry.register(
            name=name,
            toolset=NBA_TOOLSET,
            schema=TOOL_SCHEMAS[name],
            handler=_handler(name),
            description=TOOL_SCHEMAS[name]["description"],
            max_result_size_chars=16_384,
        )
    return registry


def resolve_date_expression(
    expression: str,
    *,
    now_utc: datetime,
    timezone_name: str = "Asia/Shanghai",
) -> tuple[DateRange, dict[str, str]]:
    """Resolve the small schedule-tool date language into an exact local range."""

    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must include a timezone")
    zone = ZoneInfo(timezone_name)
    today = now_utc.astimezone(zone).date()
    text = " ".join(str(expression or "").strip().split()).lower()
    if text in {"", "今天", "今日", "today"}:
        start_day, days = today, 1
    elif text in {"明天", "明日", "tomorrow"}:
        start_day, days = today + timedelta(days=1), 1
    elif text in {"后天"}:
        start_day, days = today + timedelta(days=2), 1
    elif text in {"昨天", "昨日", "yesterday"}:
        start_day, days = today - timedelta(days=1), 1
    elif text in {"本周", "这周", "this week"}:
        start_day = today - timedelta(days=today.weekday())
        days = 7
    elif text in {"下周", "下个星期", "next week"}:
        start_day = today + timedelta(days=7 - today.weekday())
        days = 7
    else:
        next_days = re.fullmatch(r"(?:未来|接下来)\s*(\d{1,2})\s*天", text)
        iso = re.fullmatch(r"(20\d{2})[-/]([01]?\d)[-/]([0-3]?\d)", text)
        chinese = re.fullmatch(r"(20\d{2})年([01]?\d)月([0-3]?\d)日?", text)
        if next_days:
            days = int(next_days.group(1))
            if not 1 <= days <= 31:
                raise ValueError("date range must contain 1..31 days")
            start_day = today
        elif iso or chinese:
            match = iso or chinese
            start_day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            days = 1
        else:
            raise ValueError("unsupported date expression")
    end_day = start_day + timedelta(days=days)
    start_local = datetime.combine(start_day, datetime_time.min, tzinfo=zone)
    end_local = datetime.combine(end_day, datetime_time.min, tzinfo=zone)
    return (
        DateRange(
            start_inclusive=start_local.astimezone(UTC),
            end_exclusive=end_local.astimezone(UTC),
        ),
        {
            "start_date": start_day.isoformat(),
            "end_date": (end_day - timedelta(days=1)).isoformat(),
            "timezone": timezone_name,
        },
    )


def new_agent_task_id() -> str:
    return f"nba-{uuid4().hex}"


__all__ = [
    "AgentTaskBridge",
    "AgentToolCall",
    "NBA_TOOL_NAMES",
    "NBA_TOOLSET",
    "TOOL_SCHEMAS",
    "agent_task_bridge",
    "new_agent_task_id",
    "register_official_nba_tools",
    "resolve_date_expression",
    "sanitise_observation",
]
