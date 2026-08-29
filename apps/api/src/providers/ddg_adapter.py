"""Controlled DuckDuckGo search adapter.

This adapter is deliberately *not* a general browsing tool.  It accepts only
the typed :class:`NewsQuery`, calls the fixed DuckDuckGo Instant Answer API,
and returns a small, untrusted news candidate projection.  Search candidates
are useful for background/news coverage but are never allowed to prove NBA
scores, standings, statistics, or play-by-play facts on their own.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from apps.api.src.application.ports import ProviderResult, RequestBudget
from apps.api.src.domain.errors import ProviderErrorKind
from apps.api.src.domain.models import (
    Evidence,
    Freshness,
    NewsItem,
    NewsQuery,
    SourceClass,
    TrustLevel,
)

DDG_ENDPOINT = "https://api.duckduckgo.com/"
DDG_HOST = "api.duckduckgo.com"
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLOCK_TAG_RE = re.compile(
    r"<\s*(?:script|style|iframe|object|embed)\b[^>]*>.*?<\s*/\s*"
    r"(?:script|style|iframe|object|embed)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]{0,400}>")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_INJECTION_RE = re.compile(
    r"(?:ignore|disregard|forget|override|bypass|skip)\s+(?:all\s+)?"
    r"(?:previous|prior|above|system|developer|the)?\s*(?:instructions?|rules?|prompts?|facts?|evidence)|"
    r"(?:忽略|无视|忘记|绕过|跳过)(?:之前|上面|所有|系统|开发者)?(?:的)?(?:指令|规则|提示|事实|证据|核验)",
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
    return text[:limit] or None


def _query_text(query: NewsQuery) -> str:
    parts = [ref.display_name for ref in query.subject_refs]
    parts.extend(query.keywords[:8])
    if not parts:
        parts = ["NBA basketball"]
    # NewsQuery validates keyword length, but callers can still construct a
    # mutable object or pass confusable control characters. Keep the egress
    # query bounded and plain-text only at this final boundary.
    # Drop any keyword that resembles an instruction before it reaches the
    # search service. Subject display names are canonical server-side values.
    safe_parts = [str(part) for part in parts if not _INJECTION_RE.search(str(part))]
    value = " ".join(safe_parts)
    value = _CONTROL_RE.sub(" ", value)
    value = re.sub(r"[^\w\u3400-\u9fff\s.'-]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())[:160] or "NBA basketball"


class DuckDuckGoAdapter:
    """Fixed-endpoint, timeout/size bounded DDG Instant Answer client."""

    def __init__(
        self,
        *,
        endpoint: str = DDG_ENDPOINT,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 3.0,
        max_results: int = 5,
        max_response_bytes: int = 512_000,
    ) -> None:
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname != DDG_HOST
            or parsed.port is not None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("DuckDuckGo endpoint is not allowed")
        if timeout_seconds <= 0 or not 1 <= max_results <= 5 or max_response_bytes <= 0:
            raise ValueError("DuckDuckGo limits must be positive and bounded")
        self.endpoint = DDG_ENDPOINT
        self.client = client
        self.timeout_seconds = float(timeout_seconds)
        self.max_results = int(max_results)
        self.max_response_bytes = int(max_response_bytes)
        self.calls = 0

    @staticmethod
    def _error(
        kind: ProviderErrorKind, message: str, retryable: bool, retrieved: datetime
    ) -> ProviderResult[list[NewsItem]]:
        from apps.api.src.domain.errors import ProviderError

        return ProviderResult(
            data=None,
            evidence=[],
            partial=False,
            error=ProviderError(kind=kind, safe_message=message, retryable=retryable),
            retrieved_at_utc=retrieved,
        )

    @staticmethod
    def _topics(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        values: list[Mapping[str, Any]] = []
        for item in payload.get("RelatedTopics", []) or []:
            if not isinstance(item, Mapping):
                continue
            nested = item.get("Topics")
            if isinstance(nested, list):
                values.extend(topic for topic in nested if isinstance(topic, Mapping))
            else:
                values.append(item)
        return values

    async def search_news(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        retrieved = datetime.now(UTC)
        if not budget.reserve_operation():
            return self._error(
                ProviderErrorKind.TIMEOUT, "request deadline exceeded", True, retrieved
            )
        params = {
            "q": _query_text(query),
            "format": "json",
            "no_html": "1",
            "no_redirect": "1",
            "skip_disambig": "1",
        }
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            follow_redirects=False,
            headers={"User-Agent": "NBAAgent/0.1 (+https://github.com/vchive/NBAAgent)"},
        )
        self.calls += 1
        try:
            try:
                response = await client.get(
                    self.endpoint,
                    params=params,
                    timeout=min(self.timeout_seconds, max(budget.remaining_ms(), 1) / 1000),
                )
            except TypeError as exc:
                if "timeout" not in str(exc):
                    raise
                response = await client.get(self.endpoint, params=params)
            retrieved = datetime.now(UTC)
            if getattr(response, "is_redirect", False):
                return self._error(
                    ProviderErrorKind.AUTH, "search redirect rejected", False, retrieved
                )
            if response.status_code == 429:
                return self._error(
                    ProviderErrorKind.RATE_LIMITED, "search rate limited", True, retrieved
                )
            if response.status_code >= 500:
                return self._error(ProviderErrorKind.HTTP, "search unavailable", True, retrieved)
            if response.status_code >= 400:
                return self._error(
                    ProviderErrorKind.HTTP, "search request failed", False, retrieved
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
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return self._error(
                    ProviderErrorKind.INVALID_JSON, "search payload invalid", False, retrieved
                )
            if not isinstance(payload, Mapping):
                return self._error(
                    ProviderErrorKind.SCHEMA_MISMATCH, "search payload invalid", False, retrieved
                )

            candidates: list[tuple[str, str | None, str | None]] = []
            abstract_title = _clean_text(payload.get("Heading"), limit=500)
            abstract = _clean_text(payload.get("AbstractText"), limit=4000)
            abstract_url = payload.get("AbstractURL")
            if abstract_title and abstract:
                candidates.append((abstract_title, abstract, str(abstract_url or "")))
            for item in self._topics(payload):
                text = _clean_text(item.get("Text"), limit=4000)
                if not text:
                    continue
                title = text.split(" - ", 1)[0][:500] or "NBA 相关新闻"
                candidates.append((title, text, str(item.get("FirstURL") or "")))
                if len(candidates) >= self.max_results:
                    break
            evidence: list[Evidence] = []
            result: list[NewsItem] = []
            for title, summary, source_url in candidates[: self.max_results]:
                fingerprint = hashlib.sha256(
                    f"{title}\n{summary}\n{source_url}".encode()
                ).hexdigest()[:20]
                evidence_id = f"ddg:search:{fingerprint}"
                evidence.append(
                    Evidence(
                        evidence_id=evidence_id,
                        source_class=SourceClass.SEARCH,
                        source_ref="duckduckgo.instant_answer",
                        url=DDG_ENDPOINT,
                        fetched_at_utc=retrieved,
                        data_as_of_utc=None,
                        trust=TrustLevel.MEDIUM,
                        freshness=Freshness.UNKNOWN,
                    )
                )
                result.append(
                    NewsItem(
                        news_id=evidence_id,
                        title=title,
                        summary=summary,
                        published_utc=None,
                        subject_refs=list(query.subject_refs),
                        evidence_id=evidence_id,
                    )
                )
            # DDG candidates intentionally remain partial/unknown. They can
            # enrich a news answer but cannot upgrade numeric NBA facts.
            return ProviderResult(
                data=result,
                evidence=evidence,
                partial=True,
                retrieved_at_utc=retrieved,
            )
        except (httpx.TimeoutException, asyncio.TimeoutError):
            return self._error(ProviderErrorKind.TIMEOUT, "search timed out", True, retrieved)
        except httpx.HTTPError:
            return self._error(ProviderErrorKind.HTTP, "search unavailable", True, retrieved)
        finally:
            if own_client:
                await client.aclose()


__all__ = ["DDG_ENDPOINT", "DuckDuckGoAdapter"]
