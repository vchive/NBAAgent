"""Bounded Alibaba Cloud IQS UnifiedSearch adapter.

The adapter exposes the existing typed news-search port. Search pages are
untrusted, supplementary evidence: they can add current background but cannot
verify scores, standings, statistics or play-by-play by themselves.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from apps.api.src.application.ports import (
    ProviderResult,
    RequestBudget,
    merge_capability_issues,
)
from apps.api.src.domain.errors import ProviderError, ProviderErrorKind
from apps.api.src.domain.models import (
    Evidence,
    Freshness,
    NewsItem,
    NewsQuery,
    SourceClass,
    TrustLevel,
)
from apps.api.src.domain.safety import neutralize_external_internal_names

ALIYUN_IQS_ENDPOINT = "https://cloud-iqs.aliyuncs.com/search/unified"
ALIYUN_IQS_HOST = "cloud-iqs.aliyuncs.com"
_BEIJING = ZoneInfo("Asia/Shanghai")
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
_AUTH_CODES = {
    "InvalidAccessKeyId.NotFound",
    "Retrieval.NotActivate",
    "Retrieval.NotAuthorised",
}
_NONRETRYABLE_QUOTA_CODES = {
    "Retrieval.Arrears",
    "Retrieval.TestUserPeriodExpired",
    "Retrieval.TestUserQueryExceeded",
}


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
    text = neutralize_external_internal_names(text)
    return text[:limit] or None


def _query_text(query: NewsQuery) -> str:
    is_flower = getattr(query, "domain", "nba") == "flower"
    fallback = "花卉 园艺" if is_flower else "NBA"
    parts = [ref.display_name for ref in query.subject_refs]
    parts.extend(query.keywords[:8])
    safe = [str(part) for part in parts if not _INJECTION_RE.search(str(part))]
    value = _CONTROL_RE.sub(" ", " ".join(safe))
    value = re.sub(r"[^\w\u3400-\u9fff\s.'-]", " ", value, flags=re.UNICODE)
    value = " ".join(value.split())
    if not is_flower and not re.search(
        r"(?<![A-Za-z])NBA(?![A-Za-z])", value, re.IGNORECASE
    ):
        value = f"NBA {value}".strip()
    return value[:500] or fallback


def _published_time(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _safe_article_url(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port is not None
    ):
        return None
    return raw


class AliyunIQSSearchAdapter:
    """Fixed-endpoint, key-authenticated IQS UnifiedSearch client."""

    def __init__(
        self,
        *,
        endpoint: str = ALIYUN_IQS_ENDPOINT,
        api_key: str = "",
        api_key_file: str = "",
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 5.5,
        max_results: int = 5,
        max_response_bytes: int = 800_000,
        fallback_provider: object | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("IQS search endpoint is not allowed") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname != ALIYUN_IQS_HOST
            or port is not None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/search/unified"
        ):
            raise ValueError("IQS search endpoint is not allowed")
        if timeout_seconds <= 0 or not 1 <= max_results <= 5 or max_response_bytes <= 0:
            raise ValueError("IQS search limits must be positive and bounded")
        self.endpoint = ALIYUN_IQS_ENDPOINT
        self.api_key = str(api_key or "")
        self.api_key_file = str(api_key_file or "")
        self.client = client
        self.timeout_seconds = float(timeout_seconds)
        self.max_results = int(max_results)
        self.max_response_bytes = int(max_response_bytes)
        self.fallback_provider = fallback_provider
        self.calls = 0
        self._last_status = "unknown"
        self._last_error_kind: str | None = None
        self._last_checked_at_utc: datetime | None = None

    def _load_key(self) -> str:
        if self.api_key:
            return self.api_key.strip()
        if not self.api_key_file:
            return ""
        try:
            return Path(self.api_key_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return ""

    def availability(self) -> dict[str, Any]:
        checked = self._last_checked_at_utc
        return {
            "status": self._last_status,
            "error_kind": self._last_error_kind,
            "checked_at_utc": checked.isoformat() if checked is not None else None,
        }

    def _mark_ok(self, retrieved: datetime) -> None:
        self._last_status = "ok"
        self._last_error_kind = None
        self._last_checked_at_utc = retrieved

    def _error(
        self,
        kind: ProviderErrorKind,
        message: str,
        retryable: bool,
        retrieved: datetime,
    ) -> ProviderResult[list[NewsItem]]:
        self._last_status = "degraded"
        self._last_error_kind = kind.value
        self._last_checked_at_utc = retrieved
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

    async def _fallback_on_empty(
        self,
        query: NewsQuery,
        budget: RequestBudget,
        result: ProviderResult[list[NewsItem]],
    ) -> ProviderResult[list[NewsItem]]:
        if self.fallback_provider is None or budget.remaining_ms() <= 0:
            return result
        method = getattr(self.fallback_provider, "search_news", None)
        if not callable(method):
            return result
        try:
            fallback = await method(query, budget)
        except Exception:
            return result
        if (
            isinstance(fallback, ProviderResult)
            and fallback.error is None
            and fallback.data
        ):
            return fallback
        return result

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        raw = bytes(response.content)
        if not raw:
            return ""
        try:
            document = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ""
        if not isinstance(document, Mapping):
            return ""
        return str(document.get("code") or document.get("Code") or "")

    def _http_error(
        self, response: httpx.Response, retrieved: datetime
    ) -> ProviderResult[list[NewsItem]]:
        code = self._error_code(response)
        if response.status_code == 401 or code in _AUTH_CODES:
            return self._error(
                ProviderErrorKind.AUTH,
                "search authentication or activation failed",
                False,
                retrieved,
            )
        if code in _NONRETRYABLE_QUOTA_CODES:
            return self._error(
                ProviderErrorKind.QUOTA_EXHAUSTED,
                "search quota or billing is unavailable",
                False,
                retrieved,
            )
        if response.status_code == 429:
            return self._error(
                ProviderErrorKind.RATE_LIMITED,
                "search rate limited",
                True,
                retrieved,
            )
        return self._error(
            ProviderErrorKind.HTTP,
            (
                "search service unavailable"
                if response.status_code >= 500
                else "search request failed"
            ),
            response.status_code >= 500,
            retrieved,
        )

    @staticmethod
    def _request_payload(query: NewsQuery, query_text: str, max_results: int) -> dict[str, Any]:
        advanced: dict[str, str] = {"numResults": str(max_results)}
        if query.date_range is not None:
            start = query.date_range.start_inclusive.astimezone(_BEIJING)
            end = (query.date_range.end_exclusive - timedelta(microseconds=1)).astimezone(
                _BEIJING
            )
            advanced["startPublishedDate"] = start.date().isoformat()
            advanced["endPublishedDate"] = end.date().isoformat()
        return {
            "query": query_text,
            "engineType": "LiteAdvanced",
            "timeRange": "NoLimit",
            "contents": {
                "mainText": False,
                "markdownText": False,
                "richMainBody": False,
                "summary": False,
                "rerankScore": True,
            },
            "advancedParams": advanced,
        }

    async def search_news(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        retrieved = datetime.now(UTC)
        if not budget.reserve_operation():
            error = self._error(
                ProviderErrorKind.TIMEOUT,
                "search deadline exceeded",
                True,
                retrieved,
            )
            return await self._fallback_or_error(query, budget, error)
        key = self._load_key()
        if not key or len(key) > 512 or any(char.isspace() for char in key):
            error = self._error(
                ProviderErrorKind.AUTH,
                "search API key is not configured",
                False,
                retrieved,
            )
            return await self._fallback_or_error(query, budget, error)

        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            follow_redirects=False,
            headers={"User-Agent": "COURTSIDE/0.1", "Accept": "application/json"},
        )
        self.calls += 1
        try:
            timeout = min(self.timeout_seconds, max(budget.remaining_ms(), 1) / 1000)
            payload = self._request_payload(query, _query_text(query), self.max_results)
            try:
                response = await client.post(
                    self.endpoint,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    timeout=timeout,
                )
            except TypeError as exc:
                if "timeout" not in str(exc):
                    raise
                response = await client.post(
                    self.endpoint,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                )
            retrieved = datetime.now(UTC)
            if response.is_redirect:
                error = self._error(
                    ProviderErrorKind.SCHEMA_MISMATCH,
                    "search redirect rejected",
                    False,
                    retrieved,
                )
                return await self._fallback_or_error(query, budget, error)
            content_length = response.headers.get("Content-Length")
            try:
                if content_length is not None and int(content_length) > self.max_response_bytes:
                    error = self._error(
                        ProviderErrorKind.SCHEMA_MISMATCH,
                        "search response too large",
                        False,
                        retrieved,
                    )
                    return await self._fallback_or_error(query, budget, error)
            except ValueError:
                pass
            raw = bytes(response.content)
            if len(raw) > self.max_response_bytes:
                error = self._error(
                    ProviderErrorKind.SCHEMA_MISMATCH,
                    "search response too large",
                    False,
                    retrieved,
                )
                return await self._fallback_or_error(query, budget, error)
            if response.status_code >= 400:
                error = self._http_error(response, retrieved)
                return await self._fallback_or_error(query, budget, error)
            try:
                document = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                error = self._error(
                    ProviderErrorKind.INVALID_JSON,
                    "search payload invalid",
                    False,
                    retrieved,
                )
                return await self._fallback_or_error(query, budget, error)
            if not isinstance(document, Mapping) or not isinstance(
                document.get("pageItems"), list
            ):
                error = self._error(
                    ProviderErrorKind.SCHEMA_MISMATCH,
                    "search payload invalid",
                    False,
                    retrieved,
                )
                return await self._fallback_or_error(query, budget, error)

            items: list[NewsItem] = []
            evidence: list[Evidence] = []
            seen: set[str] = set()
            for row in document["pageItems"][: self.max_results]:
                if not isinstance(row, Mapping) or row.get("correlationTag") in {0, "0"}:
                    continue
                title = _clean_text(row.get("title"), limit=500)
                summary = _clean_text(
                    row.get("snippet") or row.get("summary"),
                    limit=1200,
                )
                article_url = _safe_article_url(row.get("link"))
                if not title or not summary or not article_url:
                    continue
                fingerprint = hashlib.sha256(
                    f"{title.casefold()}\n{summary.casefold()}".encode()
                ).hexdigest()[:20]
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                evidence_id = f"iqs:search:{fingerprint}"
                published = _published_time(row.get("publishedTime"))
                evidence.append(
                    Evidence(
                        evidence_id=evidence_id,
                        source_class=SourceClass.SEARCH,
                        source_ref="aliyun.iqs",
                        url=article_url,
                        fetched_at_utc=retrieved,
                        data_as_of_utc=published,
                        trust=TrustLevel.MEDIUM,
                        freshness=Freshness.UNKNOWN,
                    )
                )
                items.append(
                    NewsItem(
                        news_id=evidence_id,
                        title=title,
                        summary=summary,
                        published_utc=published,
                        subject_refs=list(query.subject_refs),
                        evidence_id=evidence_id,
                    )
                )
            result = ProviderResult(
                data=items,
                evidence=evidence,
                partial=True,
                retrieved_at_utc=retrieved,
            )
            self._mark_ok(retrieved)
            if not items:
                return await self._fallback_on_empty(query, budget, result)
            return result
        except (httpx.TimeoutException, asyncio.TimeoutError):
            error = self._error(
                ProviderErrorKind.TIMEOUT,
                "search timed out",
                True,
                retrieved,
            )
            return await self._fallback_or_error(query, budget, error)
        except httpx.HTTPError:
            error = self._error(
                ProviderErrorKind.HTTP,
                "search service unavailable",
                True,
                retrieved,
            )
            return await self._fallback_or_error(query, budget, error)
        finally:
            if own_client:
                await client.aclose()

    async def search_web(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        return await self.search_news(query, budget)


__all__ = [
    "ALIYUN_IQS_ENDPOINT",
    "AliyunIQSSearchAdapter",
    "_clean_text",
    "_query_text",
]
