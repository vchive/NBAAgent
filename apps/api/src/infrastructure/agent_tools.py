"""Bounded domain tools exposed to the official model runtime.

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
import math
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
from apps.api.src.domain.safety import neutralize_external_internal_names

# Keep the legacy NBA names stable for compatibility with the original product,
# but make the registry domain-aware.  The flower application uses its own
# closed toolset; no generic shell/browser/file capability is ever registered.
NBA_TOOL_NAMES = ("nba_news", "nba_query", "nba_schedule", "nba_search")
NBA_TOOLSET = "nba"
FLOWER_TOOL_NAMES = ("care_plan", "flower_lookup", "flower_search")
FLOWER_TOOLSET = "flower"
TOOLSET_NAMES: dict[str, tuple[str, ...]] = {
    NBA_TOOLSET: NBA_TOOL_NAMES,
    FLOWER_TOOLSET: FLOWER_TOOL_NAMES,
}


def normalise_toolset(value: str | None) -> str:
    """Return a canonical closed toolset name.

    ``domain`` is intentionally normalised at this boundary so a deployment
    cannot accidentally enable an arbitrary Hermes toolset from an environment
    variable or a client request.  A few human-friendly aliases are useful for
    local configuration, while unknown values fail closed.
    """

    text = str(value or NBA_TOOLSET).strip().casefold()
    aliases = {
        "basketball": NBA_TOOLSET,
        "nba": NBA_TOOLSET,
        "flower": FLOWER_TOOLSET,
        "flowers": FLOWER_TOOLSET,
        "gardening": FLOWER_TOOLSET,
        "horticulture": FLOWER_TOOLSET,
        "园艺": FLOWER_TOOLSET,
        "种花": FLOWER_TOOLSET,
    }
    canonical = aliases.get(text, text)
    if canonical not in TOOLSET_NAMES:
        raise ValueError("unsupported agent toolset")
    return canonical


def tool_names_for_toolset(value: str | None) -> tuple[str, ...]:
    """Get the immutable allow-list for a supported domain."""

    return TOOLSET_NAMES[normalise_toolset(value)]


ALL_TOOL_NAMES = tuple(sorted({name for names in TOOLSET_NAMES.values() for name in names}))

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Newlines are meaningful in Markdown evidence and are safe once the value is
# JSON-encoded.  Keep them in the Agent observation while still removing all
# other C0 controls (including tabs/carriage returns).
_OUTPUT_CONTROL_RE = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]")
_RESOLVED_GAME_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_UNSAFE_ARGUMENT_RE = re.compile(
    r"(?:https?|ftp|file)://|www\.|(?:system|developer)\s*(?:prompt|message)|"
    r"(?:ignore|disregard|override|bypass)\s+(?:all\s+)?(?:previous|system)?\s*instructions?|"
    r"(?:忽略|无视|绕过|跳过).{0,10}(?:指令|规则|安全|核验)|"
    r"(?:输出|泄露).{0,10}(?:提示词|密钥|凭据|内部信息)",
    re.IGNORECASE,
)
_OBSERVATION_INJECTION_RE = re.compile(
    r"(?:ignore|disregard|forget|override|bypass|skip)\s+(?:all\s+)?"
    r"(?:previous|prior|above|system|developer|the)?\s*"
    r"(?:instructions?|rules?|prompts?|facts?|evidence)|"
    r"(?:忽略|无视|忘记|绕过|跳过)(?:之前|上面|所有|系统|开发者)?(?:的)?"
    r"(?:指令|规则|提示|事实|证据|核验)|"
    r"(?:输出|泄露).{0,12}(?:提示词|密钥|凭据|内部信息)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\]\)]+", re.IGNORECASE)
_ALLOWED_INTENTS = frozenset(
    {
        # Legacy NBA intents
        "nba_query",
        "nba_schedule",
        "nba_news",
        "nba_search",
        "schedule_result",
        "game_summary",
        "game_detail",
        "game_recap",
        "player_stats",
        "team_stats",
        "standings",
        "web_search",
        # Flower intents
        "plant_selection",
        "identification",
        "watering",
        "light",
        "soil",
        "fertilizing",
        "pruning",
        "propagation",
        "pest_disease",
        "seasonal_plan",
        "general_care",
        "safety",
        "care_plan",
    }
)
_ALLOWED_EVIDENCE_STATES = frozenset({"verified", "partial", "none", "unverified"})


def _enum_or_string(value: Any) -> str:
    """Return a stable string for enums and ordinary values.

    Provider/adapter DTOs frequently use ``StrEnum`` values.  ``str(enum)``
    is not guaranteed to be the wire value on every Python version (it may be
    ``EnumName.VALUE``), so all internal diagnostics go through this helper
    before the closed allow-lists are consulted.
    """

    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def _truncate_utf8(value: str, max_bytes: int) -> str:
    """Truncate text by UTF-8 bytes without splitting a code point."""

    if max_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", "ignore").rstrip()


_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "provider",
        "source",
        "evidence",
        "canonical",
        "request",
        "session",
        "trace",
        "raw",
        "token",
        "key",
        "url",
        "id",
    }
)


def _is_private_output_key(value: str) -> bool:
    """Match metadata key components without false positives like ``humidity``."""

    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    parts = [part for part in re.split(r"[^a-zA-Z0-9]+", snake.casefold()) if part]
    return any(part in _FORBIDDEN_KEY_PARTS for part in parts)

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "nba_query": {
        "description": (
            "查询 NBA 比赛、球队、球员、统计、历史或逐回合的结构化记录。"
            "比分、胜负、具体统计、场馆、教练和逐回合等硬事实优先使用本工具。"
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
    "nba_search": {
        "description": (
            "在受控的公开网页索引中检索 NBA 长尾问题、战术背景、比赛复盘材料和新闻线索。"
            "query 完全由你根据用户原问题构造，工具不会自动改写或追加搜索。"
            "用户询问明确比赛的过程或胜因，而结构化事实没有覆盖过程时，必须在最终回答前调用本工具。"
            "结果是不完全的搜索候选，不能单独证明比分、排名、统计或逐回合数字。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "简短的 NBA 搜索语句，不得包含 URL、指令或凭据。",
                    "maxLength": 80,
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "flower_lookup": {
        "description": (
            "查询服务器维护的常见花卉知识，适用于植物名称、别名、光照、浇水、"
            "土壤、施肥、修剪、繁殖、常见问题和宠物安全等基础信息。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "保留用户园艺问题含义的简短中文问题，不得包含 URL 或指令。",
                    "maxLength": 500,
                },
                "plant": {
                    "type": "string",
                    "description": "可选的植物名称或俗名。",
                    "maxLength": 100,
                },
            },
            "anyOf": [
                {"required": ["question"]},
                {"required": ["plant"]},
            ],
            "additionalProperties": False,
        },
    },
    "flower_search": {
        "description": (
            "在受控公开网页索引中检索花卉、家庭园艺、当地季节、病虫害或法规相关资料。"
            "检索材料只用于补充背景，不可单独确诊病害、确定有毒性或给出农药混用方案。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "与用户问题一致的简短园艺搜索语句，不得包含 URL、指令或凭据。",
                    "maxLength": 160,
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "care_plan": {
        "description": (
            "根据服务器已确认的植物与种植环境生成保守、按条件触发的养护计划。"
            "适用于选花、浇水、光照、土壤、施肥、修剪、繁殖和季节安排。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用户希望解决的园艺问题。",
                    "maxLength": 500,
                },
                "plant": {"type": "string", "maxLength": 100},
                "location": {"type": "string", "maxLength": 100},
                "light": {"type": "string", "maxLength": 100},
                "container": {"type": "string", "maxLength": 100},
                "observation": {"type": "string", "maxLength": 300},
            },
            "anyOf": [{"required": ["question"]}, {"required": ["plant"]}],
            "additionalProperties": False,
        },
    },
}

_ARGUMENT_RULES: dict[str, dict[str, tuple[int, bool]]] = {
    "nba_query": {"question": (500, True)},
    "nba_schedule": {"date_expression": (80, True), "team": (80, False)},
    "nba_news": {"subject": (160, True), "date_expression": (80, False)},
    "nba_search": {"query": (80, True)},
    # At least one of ``question``/``plant`` is required.  ``name``/``query``
    # remain accepted only as a migration shim for older in-process callers;
    # they are deliberately absent from the model-facing JSON schema.
    "flower_lookup": {
        "question": (500, False),
        "plant": (100, False),
        "name": (100, False),
        "query": (500, False),
    },
    "flower_search": {"query": (160, True)},
    "care_plan": {
        "question": (500, False),
        "plant": (100, False),
        "location": (100, False),
        "light": (100, False),
        "container": (100, False),
        "observation": (300, False),
    },
}

_CAPABILITY_ISSUE_CODES = {
    "QUOTA_EXHAUSTED": "SEARCH_QUOTA_EXHAUSTED",
    "RATE_LIMITED": "UPSTREAM_RATE_LIMITED",
    "AUTH": "UPSTREAM_AUTH",
    "TIMEOUT": "UPSTREAM_TIMEOUT",
    "INVALID_JSON": "INVALID_UPSTREAM_DATA",
    "SCHEMA_MISMATCH": "INVALID_UPSTREAM_DATA",
    "HTTP": "COMPOSER_UNAVAILABLE",
}
_CAPABILITY_ISSUE_PRIORITY = {
    "QUOTA_EXHAUSTED": 7,
    "AUTH": 6,
    "RATE_LIMITED": 5,
    "TIMEOUT": 4,
    "INVALID_JSON": 3,
    "SCHEMA_MISMATCH": 3,
    "HTTP": 2,
}
# A nested, deterministic ``nba_query`` can complete with usable facts while
# its provider reports a request-local capability notice (for example, an
# optional online lookup exhausted its quota before a local index answered).
# The notice must remain invisible to the model, but the outer application
# still needs its stable code after the Agent turn finishes.  Keep this list
# deliberately closed: arbitrary nested response fields must never become
# Agent error codes or public diagnostics.
_PUBLIC_NOTICE_PRIORITY = {
    "SEARCH_QUOTA_EXHAUSTED": 7,
    "SEARCH_AUTH_UNAVAILABLE": 6,
    "SEARCH_TEMPORARILY_UNAVAILABLE": 5,
    "INTELLIGENCE_QUOTA_EXHAUSTED": 4,
    "INTELLIGENCE_AUTH_UNAVAILABLE": 3,
    "INTELLIGENCE_TEMPORARILY_UNAVAILABLE": 2,
}
_KNOWN_INTERNAL_ERROR_CODES = frozenset(
    {
        *_CAPABILITY_ISSUE_CODES.values(),
        *_PUBLIC_NOTICE_PRIORITY,
        "UPSTREAM_RATE_LIMITED",
        "UPSTREAM_AUTH",
        "UPSTREAM_TIMEOUT",
        "COMPOSER_UNAVAILABLE",
        "INVALID_UPSTREAM_DATA",
        "SEARCH_QUOTA_EXHAUSTED",
    }
)


@dataclass(slots=True)
class AgentToolCall:
    tool_name: str
    arguments_hash: str
    status: str
    latency_ms: int
    evidence_state: str = "none"
    error_code: str | None = None
    retryable: bool = False


@dataclass(slots=True)
class _TaskState:
    loop: asyncio.AbstractEventLoop
    runner: Callable[[str, dict[str, str]], Awaitable[Mapping[str, Any]]]
    deadline_at_utc: datetime
    cancel: CancelToken
    max_calls: int
    timeout_ms: int
    max_result_bytes: int
    allowed_tools: tuple[str, ...] = NBA_TOOL_NAMES
    seen: set[str] = field(default_factory=set)
    # Count accepted calls at reservation time, rather than relying on the
    # length of ``calls`` (which is updated only after the async runner
    # returns).  Hermes may dispatch several synchronous handlers concurrently
    # from worker threads; checking completed calls alone would let those
    # in-flight requests bypass the per-turn budget.
    accepted_calls: int = 0
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
    # Human/tool implementations commonly use ``name`` or ``prompt`` for a
    # plant lookup.  Canonicalise those aliases without widening the schema
    # exposed to the model.  This also makes direct contract tests resilient to
    # older flower adapters.
    if tool_name == "flower_lookup":
        if "plant" not in output and "name" in output:
            output["plant"] = output.pop("name")
        elif "name" in output:
            # Do not pass a duplicate migration alias into the application
            # runner or let it influence the dedup fingerprint differently.
            output.pop("name", None)
        if "question" not in output and "query" in output:
            output["question"] = output.pop("query")
        elif "query" in output:
            output.pop("query", None)
        if "question" not in output and "plant" in output:
            output["question"] = output["plant"]
        if not any(key in output for key in ("question", "plant")):
            raise ValueError("missing tool argument")
    elif tool_name == "care_plan" and not any(
        key in output for key in ("question", "plant")
    ):
        raise ValueError("missing tool argument")
    return output


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        # ``json.dumps`` otherwise emits non-standard NaN/Infinity literals,
        # which strict model clients reject and which can bypass numeric
        # validation in downstream adapters.
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        # Values inside blocks are untrusted source/model text as well as the
        # top-level answer.  Keep useful prose while removing URLs and private
        # implementation names before it reaches the model.
        cleaned = _CONTROL_RE.sub(" ", value).strip()
        cleaned = _URL_RE.sub("", cleaned)
        if _OBSERVATION_INJECTION_RE.search(cleaned):
            return None
        return neutralize_external_internal_names(cleaned).strip()
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"), depth=depth + 1)
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in list(value.items())[:64]:
            if not isinstance(key, str):
                continue
            safe_key = _truncate_utf8(key, 120)
            if not safe_key or _CONTROL_RE.search(safe_key):
                continue
            if _is_private_output_key(safe_key):
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
    # Do not stringify arbitrary objects: their repr may contain credentials,
    # filesystem paths or provider response fragments.  Typed Pydantic models
    # have already been handled above; everything else is dropped fail-closed.
    return None


def sanitise_observation(value: Mapping[str, Any], *, max_bytes: int) -> dict[str, Any]:
    """Project an internal tool result into the provider-neutral Agent shape."""

    if not isinstance(value, Mapping):
        raise ValueError("tool result must be a mapping")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")

    status = _enum_or_string(value.get("status", "failed")).lower()
    if status not in {"completed", "no_data", "needs_clarification", "failed"}:
        status = "failed"
    scope = value.get("query_scope")
    safe_scope: dict[str, str] | None = None
    if isinstance(scope, Mapping):
        safe_scope = {}
        # These are deliberately broad, non-identifying fields that can ground
        # either the legacy NBA domain or the flower session.  IDs, raw query
        # objects and exact addresses are never copied into model context.
        for key in (
            "start_date",
            "end_date",
            "timezone",
            "plant",
            "plant_name",
            "location",
            "climate",
            "light",
            "container",
            "season",
        ):
            item = scope.get(key)
            if isinstance(item, str) and item and not _CONTROL_RE.search(item):
                item = _URL_RE.sub("", item).strip()
                item = neutralize_external_internal_names(item)
                if _OBSERVATION_INJECTION_RE.search(item):
                    continue
                # Location context must remain coarse; do not pass a street,
                # unit or phone-like number into the model transcript.
                if key == "location" and re.search(
                    r"\d{2,}|(?:路|街|号|栋|单元|室|弄|巷)", item
                ):
                    continue
                if item:
                    safe_scope[key] = item[:160]
        # The current recommendation is server-owned series state and is
        # useful to the model when answering a premise challenge such as
        # “为什么不是 G2”.  Keep only the bounded NBA playoff ordinal; IDs
        # and the rest of the raw query scope remain outside model context.
        active_game_number = scope.get("active_game_number")
        if (
            isinstance(active_game_number, int)
            and not isinstance(active_game_number, bool)
            and 1 <= active_game_number <= 7
        ):
            safe_scope["active_game_number"] = active_game_number
        if not safe_scope:
            safe_scope = None
    raw_answer = value.get("answer_markdown", "")
    answer = raw_answer.strip() if isinstance(raw_answer, str) else ""
    answer = answer.replace("\r\n", "\n").replace("\r", "\n")
    # Search/provider text is untrusted.  Drop an entire instruction-like
    # line instead of allowing it to become a prompt injection in the next
    # model turn; keep unrelated safe lines for offline recovery.
    answer = "\n".join(
        line for line in answer.split("\n") if not _OBSERVATION_INJECTION_RE.search(line)
    ).strip()
    answer = _URL_RE.sub(
        "",
        neutralize_external_internal_names(_OUTPUT_CONTROL_RE.sub(" ", answer)),
    )[:12_000]
    data_origin = _enum_or_string(value.get("data_origin", "none")).lower()
    if data_origin not in {
        "public",
        "demo_snapshot",
        "mixed",
        "none",
        "local",
        "search",
        "cache",
    }:
        data_origin = "none"
    intent_value = _enum_or_string(value.get("intent", "unknown")).strip().casefold()
    if intent_value not in _ALLOWED_INTENTS:
        intent_value = "unknown"
    evidence_value = _enum_or_string(value.get("evidence_state", "none")).strip().lower()
    if evidence_value not in _ALLOWED_EVIDENCE_STATES:
        evidence_value = "none"
    as_of_value = value.get("as_of_beijing")
    if isinstance(as_of_value, str) and re.fullmatch(
        r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?",
        as_of_value.strip(),
    ):
        candidate_as_of = as_of_value.strip().replace("/", "-")
        # A regex alone accepts impossible dates/times.  Keep malformed
        # freshness metadata out of the model context rather than making a
        # failed observation look authoritative.
        try:
            if " " in candidate_as_of or "T" in candidate_as_of:
                date_part, time_part = re.split(r"[ T]", candidate_as_of, maxsplit=1)
                datetime.strptime(
                    f"{date_part} {time_part[:8]}",
                    "%Y-%m-%d %H:%M:%S" if len(time_part) >= 8 else "%Y-%m-%d %H:%M",
                )
            else:
                datetime.strptime(candidate_as_of, "%Y-%m-%d")
        except ValueError:
            safe_as_of = None
        else:
            safe_as_of = _truncate_utf8(candidate_as_of, 32)
    else:
        safe_as_of = None
    observation: dict[str, Any] = {
        "status": status,
        "intent": intent_value,
        "query_scope": safe_scope,
        "answer_markdown": answer,
        "blocks": _json_safe(value.get("blocks", [])),
        "evidence_state": evidence_value,
        "as_of_beijing": safe_as_of,
        "data_origin": data_origin,
    }
    coverage = str(value.get("coverage", "complete")).lower()
    if coverage in {
        "complete",
        "requested_detail_missing",
        "series_candidates_ready",
        "server_typed_game_grounding",
        "server_typed_pbp_grounding",
        "plant_profile",
        "care_plan",
        "search_background",
        "safety_short_circuit",
    }:
        observation["coverage"] = coverage
    # Measure with the ordinary JSON representation (including insignificant
    # whitespace) rather than only the compact transport form.  Callers and
    # tests may apply either encoder to the bounded observation; the
    # conservative measurement keeps the limit true in both cases.
    encoded = json.dumps(observation, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > max_bytes:
        observation["blocks"] = []
        encoded = json.dumps(observation, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > max_bytes:
        # Reserve a little room for the fixed envelope and truncate by actual
        # UTF-8 byte count.  The previous character/byte mix could erase every
        # Chinese character (3 bytes each) even when ample space remained.
        low, high = 0, len(answer)
        best = ""
        while low <= high:
            middle = (low + high) // 2
            candidate = _truncate_utf8(answer, middle)
            observation["answer_markdown"] = candidate
            candidate_bytes = len(
                json.dumps(
                    observation,
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode()
            )
            if candidate_bytes <= max_bytes:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        observation["answer_markdown"] = best
        encoded = json.dumps(observation, ensure_ascii=False, allow_nan=False).encode()
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
        allowed_tools: tuple[str, ...] | list[str] | None = None,
        toolset: str | None = None,
    ) -> None:
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 128:
            raise ValueError("agent task id is invalid")
        if _CONTROL_RE.search(task_id):
            raise ValueError("agent task id contains control characters")
        if deadline_at_utc.tzinfo is None or deadline_at_utc.utcoffset() is None:
            raise ValueError("agent deadline must be timezone-aware")
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or not 1 <= max_calls <= 4:
            raise ValueError("agent tool call budget must be between 1 and 4")
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, (int, float))
            or timeout_ms <= 0
            or isinstance(max_result_bytes, bool)
            or not isinstance(max_result_bytes, int)
            or max_result_bytes <= 0
        ):
            raise ValueError("agent tool limits must be positive")
        if allowed_tools is not None and toolset is not None:
            raise ValueError("specify either allowed_tools or toolset")
        if toolset is not None:
            allowed = tool_names_for_toolset(toolset)
        elif allowed_tools is None:
            # Preserve the original NBA bridge contract for callers that do
            # not yet declare a domain.
            allowed = NBA_TOOL_NAMES
        else:
            try:
                allowed = tuple(dict.fromkeys(str(item) for item in allowed_tools))
            except Exception as exc:
                raise ValueError("invalid agent tool allow-list") from exc
            if not allowed or any(item not in ALL_TOOL_NAMES for item in allowed):
                raise ValueError("invalid agent tool allow-list")
            # A bridge is always tied to one domain.  Mixing NBA and flower
            # names would make a compromised model able to pivot between
            # otherwise isolated application runners.
            domains = {
                candidate
                for candidate, names in TOOLSET_NAMES.items()
                if any(item in names for item in allowed)
            }
            if len(domains) > 1:
                raise ValueError("agent tool allow-list mixes domains")
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
                allowed_tools=allowed,
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
        if (
            not isinstance(tool_name, str)
            or tool_name not in ALL_TOOL_NAMES
            or not isinstance(task_id, str)
            or not task_id
        ):
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
            if tool_name not in state.allowed_tools:
                return _safe_error("failed", "tool is not available for this request")
            remaining = (state.deadline_at_utc - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return _safe_error("cancelled", "request deadline expired")
            if fingerprint in state.seen:
                state.calls.append(
                    AgentToolCall(tool_name, fingerprint, "duplicate", 0)
                )
                return _safe_error("duplicate", "identical tool call was already executed")
            if state.accepted_calls >= state.max_calls:
                return _safe_error("failed", "tool call budget exhausted")
            state.seen.add(fingerprint)
            state.accepted_calls += 1
            loop = state.loop
            runner = state.runner
            timeout = min(remaining, max(state.timeout_ms, 1) / 1000)
            max_result_bytes = state.max_result_bytes

        future = asyncio.run_coroutine_threadsafe(runner(tool_name, normalised), loop)
        try:
            raw = future.result(timeout=timeout)
            if not isinstance(raw, Mapping):
                raise TypeError("tool runner returned an invalid result")
            internal_error_code = raw.get("_error_code")
            if internal_error_code is not None:
                candidate_error_code = _enum_or_string(internal_error_code).upper()
                internal_error_code = (
                    candidate_error_code
                    if candidate_error_code in _KNOWN_INTERNAL_ERROR_CODES
                    else None
                )
            internal_retryable = bool(raw.get("_retryable", False))
            if internal_error_code is None:
                raw_issues = raw.get("_capability_issues")
                if isinstance(raw_issues, list):
                    selected_issue: Mapping[str, Any] | None = None
                    selected_priority = -1
                    for issue in raw_issues[:8]:
                        if not isinstance(issue, Mapping):
                            continue
                        kind = _enum_or_string(issue.get("kind")).upper()
                        priority = _CAPABILITY_ISSUE_PRIORITY.get(kind, -1)
                        if priority > selected_priority:
                            selected_issue = issue
                            selected_priority = priority
                    if selected_issue is not None:
                        kind = _enum_or_string(selected_issue.get("kind")).upper()
                        internal_error_code = _CAPABILITY_ISSUE_CODES.get(kind)
                        internal_retryable = bool(
                            selected_issue.get("retryable", False)
                        )
            if internal_error_code is None:
                raw_notices = raw.get("_public_notices")
                if isinstance(raw_notices, list):
                    selected_notice: Mapping[str, Any] | None = None
                    selected_priority = -1
                    for notice in raw_notices[:8]:
                        if not isinstance(notice, Mapping):
                            continue
                        code = str(notice.get("code") or "").upper()
                        priority = _PUBLIC_NOTICE_PRIORITY.get(code, -1)
                        if priority > selected_priority:
                            selected_notice = notice
                            selected_priority = priority
                    if selected_notice is not None:
                        candidate_notice_code = _enum_or_string(
                            selected_notice.get("code")
                        ).upper()
                        # Never carry arbitrary adapter strings into the
                        # internal call record; only the documented public
                        # notice vocabulary is meaningful here.
                        if candidate_notice_code in _PUBLIC_NOTICE_PRIORITY:
                            internal_error_code = candidate_notice_code
                            internal_retryable = bool(
                                selected_notice.get("retryable", False)
                            )
            # A canonical game id is useful to the application after the
            # turn, but it is not model input.  Capture it separately before
            # sanitising the visible observation, validate the narrow ID
            # alphabet, and attach it only to the server-side audit copy.
            resolved_game_id: str | None = None
            raw_scope = raw.get("query_scope")
            if isinstance(raw_scope, Mapping):
                candidate_game_id = raw_scope.get("game_id")
                if isinstance(candidate_game_id, str) and _RESOLVED_GAME_ID_RE.fullmatch(
                    candidate_game_id
                ):
                    resolved_game_id = candidate_game_id
            observation = sanitise_observation(raw, max_bytes=max_result_bytes)
            status = observation["status"]
        except concurrent.futures.TimeoutError:
            future.cancel()
            observation = None
            status = "cancelled"
            internal_error_code = "UPSTREAM_TIMEOUT"
            internal_retryable = True
        except Exception:
            future.cancel()
            observation = None
            status = "failed"
            internal_error_code = "COMPOSER_UNAVAILABLE"
            internal_retryable = True
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        with self._lock:
            current = self._states.get(task_id)
            if current is None or current is not state or not current.active:
                return _safe_error("cancelled", "request is no longer active")
            evidence = str((observation or {}).get("evidence_state", "none"))
            current.calls.append(
                AgentToolCall(
                    tool_name,
                    fingerprint,
                    status,
                    latency_ms,
                    evidence,
                    internal_error_code,
                    internal_retryable,
                )
            )
            if observation is not None:
                stored_observation = observation
                if resolved_game_id is not None:
                    stored_observation = {
                        **observation,
                        "_resolved_game_id": resolved_game_id,
                    }
                current.observations.append(stored_observation)
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


def register_official_tools(
    toolset: str = NBA_TOOLSET,
    registry: Any | None = None,
) -> Any:
    """Register one closed, server-owned domain toolset.

    The official registry may contain many tools for other applications.  This
    function only adds the selected allow-list and never enables a wildcard or
    a built-in shell/browser toolset.  Registration is idempotent for the
    registry implementations used by Hermes and tests.
    """

    canonical = normalise_toolset(toolset)
    if registry is None:
        from tools.registry import registry as official_registry

        registry = official_registry
    for name in tool_names_for_toolset(canonical):
        registry.register(
            name=name,
            toolset=canonical,
            schema=TOOL_SCHEMAS[name],
            handler=_handler(name),
            description=TOOL_SCHEMAS[name]["description"],
            max_result_size_chars=16_384,
        )
    return registry


def register_official_nba_tools(registry: Any | None = None) -> Any:
    """Backward-compatible NBA registration helper."""

    return register_official_tools(NBA_TOOLSET, registry)


def register_official_flower_tools(registry: Any | None = None) -> Any:
    """Register the bounded flower-growing tools."""

    return register_official_tools(FLOWER_TOOLSET, registry)


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
    # The task id is an opaque bridge key.  Avoid embedding a product/domain
    # name so logs and diagnostics cannot accidentally advertise a legacy
    # domain when the flower runtime is active.
    return f"agent-{uuid4().hex}"


__all__ = [
    "ALL_TOOL_NAMES",
    "AgentTaskBridge",
    "AgentToolCall",
    "FLOWER_TOOL_NAMES",
    "FLOWER_TOOLSET",
    "NBA_TOOL_NAMES",
    "NBA_TOOLSET",
    "TOOLSET_NAMES",
    "TOOL_SCHEMAS",
    "agent_task_bridge",
    "new_agent_task_id",
    "normalise_toolset",
    "register_official_flower_tools",
    "register_official_tools",
    "register_official_nba_tools",
    "resolve_date_expression",
    "sanitise_observation",
    "tool_names_for_toolset",
]
