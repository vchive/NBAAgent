"""百度千帆 AI 搜索适配器。

千帆搜索只作为 Hermes 的联网背景检索工具，不是 NBA 结构化事实源。
适配器固定访问官方 HTTPS endpoint，密钥只在服务端读取，返回给 Agent
的仅是经过清洗、限长的标题和摘要。搜索结果始终标记为 ``partial``，
因此不能单独证明比分、球员统计、排名或逐回合事实。
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from apps.api.src.application.ports import (
    ProviderResult,
    RequestBudget,
    merge_capability_issues,
)
from apps.api.src.domain.errors import ProviderErrorKind
from apps.api.src.domain.models import (
    Evidence,
    Freshness,
    NewsItem,
    NewsQuery,
    SourceClass,
    TrustLevel,
)
from apps.api.src.domain.safety import neutralize_external_internal_names

QIANFAN_SEARCH_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"
QIANFAN_SEARCH_HOST = "qianfan.baidubce.com"
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLOCK_TAG_RE = re.compile(
    r"<\s*(?:script|style|iframe|object|embed|form)\b[^>]*>.*?<\s*/\s*"
    r"(?:script|style|iframe|object|embed|form)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]{0,400}>")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_INJECTION_RE = re.compile(
    r"(?:ignore|disregard|forget|override|bypass|skip)\s+(?:all\s+)?"
    r"(?:previous|prior|above|system|developer|the)?\s*"
    r"(?:instructions?|rules?|prompts?|facts?|evidence)|"
    r"(?:忽略|无视|忘记|绕过|跳过)(?:之前|上面|所有|系统|开发者)?(?:的)?"
    r"(?:指令|规则|提示|事实|证据|核验)",
    re.IGNORECASE,
)


def _clean_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = _BLOCK_TAG_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _CONTROL_RE.sub(" ", text)
    text = " ".join(text.split())
    if not text or _INJECTION_RE.search(text):
        return None
    # Keep article context useful while removing implementation/provider names
    # that are forbidden at the public response boundary.
    text = neutralize_external_internal_names(text)
    return text[:limit] or None


def _query_text(query: NewsQuery) -> str:
    parts = [ref.display_name for ref in query.subject_refs]
    parts.extend(query.keywords[:8])
    if not parts:
        parts = ["NBA basketball"]
    safe_parts = [str(part) for part in parts if not _INJECTION_RE.search(str(part))]
    value = _CONTROL_RE.sub(" ", " ".join(safe_parts))
    value = re.sub(r"[^\w\u3400-\u9fff\s.'-]", " ", value, flags=re.UNICODE)
    value = " ".join(value.split())
    # 千帆文档规定 query 最长 72 个字符（汉字按 2 个字符计）。
    result: list[str] = []
    units = 0
    for char in value:
        units += 2 if ord(char) > 127 else 1
        if units > 72:
            break
        result.append(char)
    return "".join(result) or "NBA basketball"


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


class QianfanSearchAdapter:
    """Fixed-endpoint, key-authenticated Qianfan web search client."""

    def __init__(
        self,
        *,
        endpoint: str = QIANFAN_SEARCH_ENDPOINT,
        api_key: str = "",
        api_key_file: str = "",
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 8.0,
        max_results: int = 5,
        max_response_bytes: int = 800_000,
        fallback_provider: object | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname != QIANFAN_SEARCH_HOST
            or parsed.port is not None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/v2/ai_search/web_search"
        ):
            raise ValueError("Qianfan search endpoint is not allowed")
        if timeout_seconds <= 0 or not 1 <= max_results <= 5 or max_response_bytes <= 0:
            raise ValueError("Qianfan search limits must be positive and bounded")
        self.endpoint = QIANFAN_SEARCH_ENDPOINT
        self.api_key = str(api_key or "")
        self.api_key_file = str(api_key_file or "")
        self.client = client
        self.timeout_seconds = float(timeout_seconds)
        self.max_results = int(max_results)
        self.max_response_bytes = int(max_response_bytes)
        self.fallback_provider = fallback_provider
        self.calls = 0

    def _load_key(self) -> str:
        if self.api_key:
            return self.api_key.strip()
        if not self.api_key_file:
            return ""
        try:
            return Path(self.api_key_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return ""

    @staticmethod
    def _error(
        kind: ProviderErrorKind,
        message: str,
        retryable: bool,
        retrieved: datetime,
    ) -> ProviderResult[list[NewsItem]]:
        from apps.api.src.domain.errors import ProviderError

        return ProviderResult(
            data=None,
            evidence=[],
            partial=False,
            error=ProviderError(kind=kind, safe_message=message, retryable=retryable),
            retrieved_at_utc=retrieved,
        )

    async def _fallback_or_error(
        self,
        query: NewsQuery,
        budget: RequestBudget,
        error: ProviderResult[list[NewsItem]],
    ) -> ProviderResult[list[NewsItem]]:
        if self.fallback_provider is not None and budget.remaining_ms() > 0:
            method = getattr(self.fallback_provider, "search_news", None)
            if callable(method):
                try:
                    result = await method(query, budget)
                    if (
                        isinstance(result, ProviderResult)
                        and result.error is None
                        and bool(result.data)
                    ):
                        return result.model_copy(
                            update={
                                "capability_issues": merge_capability_issues(
                                    result.capability_issues,
                                    [error.error],
                                )
                            }
                        )
                except Exception:
                    pass
        return error

    @staticmethod
    def _references(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        raw = document.get("references")
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, Mapping)]
        if isinstance(raw, Mapping):
            # Be tolerant of an object keyed by reference id, while never
            # passing the provider object itself across the application port.
            if "title" in raw or "web_anchor" in raw or "content" in raw:
                return [raw]
            values = raw.get("items") or raw.get("data") or raw.values()
            if isinstance(values, list):
                return [item for item in values if isinstance(item, Mapping)]
            return [item for item in values if isinstance(item, Mapping)]
        return []

    async def search_news(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        retrieved = datetime.now(UTC)
        if not budget.reserve_operation():
            return self._error(
                ProviderErrorKind.TIMEOUT, "search deadline exceeded", True, retrieved
            )
        key = self._load_key()
        if not key or len(key) > 512 or any(char.isspace() for char in key):
            return await self._fallback_or_error(
                query,
                budget,
                self._error(
                    ProviderErrorKind.AUTH, "search API key is not configured", False, retrieved
                ),
            )
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            follow_redirects=False,
            headers={"User-Agent": "COURTSIDE/0.1", "Accept": "application/json"},
        )
        self.calls += 1
        try:
            payload = {
                "messages": [{"role": "user", "content": _query_text(query)}],
                "search_source": "baidu_search_v2",
                "resource_type_filter": [{"type": "web", "top_k": min(20, self.max_results * 4)}],
                "safe_search": True,
            }
            headers = {
                "Content-Type": "application/json",
                # This is the header shown by the official Qianfan AI Search
                # curl example. Authorization is also sent for compatibility
                # with the API reference's generic bearer description.
                "X-Appbuilder-Authorization": f"Bearer {key}",
                "Authorization": f"Bearer {key}",
            }
            timeout = min(self.timeout_seconds, max(budget.remaining_ms(), 1) / 1000)
            try:
                response = await client.post(
                    self.endpoint, json=payload, headers=headers, timeout=timeout
                )
            except TypeError as exc:
                if "timeout" not in str(exc):
                    raise
                response = await client.post(self.endpoint, json=payload, headers=headers)
            retrieved = datetime.now(UTC)
            if getattr(response, "is_redirect", False):
                return await self._fallback_or_error(
                    query,
                    budget,
                    self._error(
                        ProviderErrorKind.AUTH, "search redirect rejected", False, retrieved
                    ),
                )
            if response.status_code == 402:
                return await self._fallback_or_error(
                    query,
                    budget,
                    self._error(
                        ProviderErrorKind.QUOTA_EXHAUSTED,
                        "search quota is exhausted",
                        False,
                        retrieved,
                    ),
                )
            if response.status_code in {401, 403}:
                return await self._fallback_or_error(
                    query,
                    budget,
                    self._error(
                        ProviderErrorKind.AUTH, "search API authentication failed", False, retrieved
                    ),
                )
            if response.status_code == 429:
                return await self._fallback_or_error(
                    query,
                    budget,
                    self._error(
                        ProviderErrorKind.RATE_LIMITED, "search API rate limited", True, retrieved
                    ),
                )
            if response.status_code >= 500:
                return await self._fallback_or_error(
                    query,
                    budget,
                    self._error(ProviderErrorKind.HTTP, "search API unavailable", True, retrieved),
                )
            if response.status_code >= 400:
                return self._error(
                    ProviderErrorKind.HTTP, "search API request failed", False, retrieved
                )
            content_length = response.headers.get("Content-Length")
            try:
                if content_length is not None and int(content_length) > self.max_response_bytes:
                    return self._error(
                        ProviderErrorKind.SCHEMA_MISMATCH,
                        "search response too large",
                        False,
                        retrieved,
                    )
            except ValueError:
                pass
            raw = bytes(response.content)
            if len(raw) > self.max_response_bytes:
                return self._error(
                    ProviderErrorKind.SCHEMA_MISMATCH, "search response too large", False, retrieved
                )
            try:
                document = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return self._error(
                    ProviderErrorKind.INVALID_JSON, "search payload invalid", False, retrieved
                )
            if not isinstance(document, Mapping):
                return self._error(
                    ProviderErrorKind.SCHEMA_MISMATCH, "search payload invalid", False, retrieved
                )
            if document.get("code") not in (None, 0, "0", ""):
                # Provider error messages are deliberately not copied into
                # the model context; they may contain credentials or prompts.
                code = str(document.get("code") or "").casefold()
                message = str(document.get("message") or "").casefold()[:1_000]
                diagnostic = f"{code} {message}"
                permanent_quota = code in {"17", "19"} or any(
                    marker in diagnostic
                    for marker in (
                        "billing",
                        "arrears",
                        "balance",
                        "credit",
                        "insufficient",
                        "periodexpired",
                        "quota_exhausted",
                        "quota exhausted",
                        "quota depleted",
                        "out of quota",
                        "daily quota",
                        "total request limit",
                        "额度耗尽",
                        "余额",
                        "欠费",
                        "试用结束",
                        "试用到期",
                    )
                )
                limited = code in {"17", "18", "19", "429"} or any(
                    marker in diagnostic
                    for marker in (
                        "rate",
                        "limit",
                        "quota",
                        "throttl",
                        "arrears",
                        "balance",
                        "credit",
                        "queryexceeded",
                        "periodexpired",
                        "resource_exhausted",
                        "额度",
                        "余额",
                        "欠费",
                        "限流",
                    )
                )
                if permanent_quota:
                    error = self._error(
                        ProviderErrorKind.QUOTA_EXHAUSTED,
                        "search quota is exhausted",
                        False,
                        retrieved,
                    )
                elif limited:
                    error = self._error(
                        ProviderErrorKind.RATE_LIMITED,
                        "search rate limit reached",
                        True,
                        retrieved,
                    )
                elif any(
                    marker in diagnostic
                    for marker in (
                        "auth",
                        "accesskey",
                        "api key",
                        "permission",
                        "unauthorized",
                        "notactivate",
                    )
                ):
                    error = self._error(
                        ProviderErrorKind.AUTH,
                        "search API authentication failed",
                        False,
                        retrieved,
                    )
                else:
                    error = self._error(
                        ProviderErrorKind.HTTP,
                        "search API returned an error",
                        False,
                        retrieved,
                    )
                return await self._fallback_or_error(
                    query,
                    budget,
                    error,
                )
            items: list[NewsItem] = []
            evidence: list[Evidence] = []
            for index, row in enumerate(self._references(document)[: self.max_results]):
                if str(row.get("type") or "web").lower() != "web":
                    continue
                title = _clean_text(row.get("title") or row.get("web_anchor"), limit=500)
                # Search snippets are background candidates, not authoritative
                # facts.  Bound each one so five references always fit inside
                # the Agent observation budget with room for metadata.
                summary = _clean_text(row.get("content") or row.get("snippet"), limit=1200)
                if not title or not summary:
                    continue
                fingerprint = hashlib.sha256(f"{title}\n{summary}".encode()).hexdigest()[:20]
                evidence_id = f"qianfan:search:{fingerprint}"
                evidence.append(
                    Evidence(
                        evidence_id=evidence_id,
                        source_class=SourceClass.SEARCH,
                        source_ref="qianfan.ai_search",
                        url=QIANFAN_SEARCH_ENDPOINT,
                        fetched_at_utc=retrieved,
                        data_as_of_utc=_parse_date(row.get("date")),
                        trust=TrustLevel.MEDIUM,
                        freshness=Freshness.UNKNOWN,
                    )
                )
                items.append(
                    NewsItem(
                        news_id=evidence_id,
                        title=title,
                        summary=summary,
                        published_utc=_parse_date(row.get("date")),
                        subject_refs=list(query.subject_refs),
                        evidence_id=evidence_id,
                    )
                )
            return ProviderResult(
                data=items,
                evidence=evidence,
                partial=True,
                retrieved_at_utc=retrieved,
            )
        except (httpx.TimeoutException, asyncio.TimeoutError):
            return await self._fallback_or_error(
                query,
                budget,
                self._error(ProviderErrorKind.TIMEOUT, "search timed out", True, retrieved),
            )
        except httpx.HTTPError:
            return await self._fallback_or_error(
                query,
                budget,
                self._error(ProviderErrorKind.HTTP, "search unavailable", True, retrieved),
            )
        finally:
            if own_client:
                await client.aclose()

    async def search_web(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        return await self.search_news(query, budget)


__all__ = ["QIANFAN_SEARCH_ENDPOINT", "QianfanSearchAdapter", "_clean_text", "_query_text"]
