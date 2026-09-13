"""Bounded Baidu web-search adapter used by the full-intelligence path.

The adapter deliberately returns *search candidates*, not arbitrary web pages.
It uses a fixed Baidu HTTPS endpoint and an HTML parser from the Python
standard library, so the model never receives a shell/curl capability and the
server never follows user supplied URLs.  Search text is treated as untrusted
background evidence and is always marked partial.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
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

BAIDU_ENDPOINT = "https://www.baidu.com/s"
BAIDU_HOST = "www.baidu.com"
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLOCK_RE = re.compile(
    r"<\s*(?:script|style|iframe|object|embed)\b[^>]*>.*?<\s*/\s*"
    r"(?:script|style|iframe|object|embed)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]{0,400}>")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_INJECTION_RE = re.compile(
    r"(?:ignore|disregard|forget|override|bypass|skip)\s+(?:all\s+)?"
    r"(?:previous|prior|above|system|developer|the)?\s*(?:instructions?|rules?|prompts?|facts?|evidence)|"
    r"(?:忽略|无视|忘记|绕过|跳过)(?:之前|上面|所有|系统|开发者)?(?:的)?"
    r"(?:指令|规则|提示|事实|证据|核验)",
    re.IGNORECASE,
)


def _clean_text(value: object, *, limit: int) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = _BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _CONTROL_RE.sub(" ", text)
    text = " ".join(text.split())
    if not text or _INJECTION_RE.search(text):
        return None
    text = neutralize_external_internal_names(text)
    return text[:limit] or None


def _query_text(query: NewsQuery) -> str:
    # ``getattr`` keeps this adapter compatible with older typed-query shims
    # used by downstream callers while treating an absent marker as legacy NBA.
    is_flower = getattr(query, "domain", "nba") == "flower"
    fallback = "花卉 园艺" if is_flower else "NBA"
    parts = [ref.display_name for ref in query.subject_refs]
    parts.extend(query.keywords[:8])
    if not parts:
        parts = [fallback]
    safe = [part for part in parts if not _INJECTION_RE.search(str(part))]
    value = _CONTROL_RE.sub(" ", " ".join(map(str, safe)))
    value = re.sub(r"[^\w\u3400-\u9fff\s.'-]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())[:160] or fallback


class _BaiduResultParser(HTMLParser):
    """Extract only external result anchors from Baidu's changing HTML."""

    def __init__(self, max_results: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_results = max_results
        self._anchor: dict[str, str] | None = None
        self._buffer: list[str] = []
        self.results: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._anchor is not None:
            return
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        href = values.get("href", "").strip()
        parsed = urlparse(href if not href.startswith("//") else f"https:{href}")
        # Keep external result links only; navigation/search links are not
        # useful evidence and could contain untrusted query directives.
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return
        if parsed.hostname.casefold().endswith("baidu.com"):
            return
        self._anchor = {"href": href}
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._anchor is None:
            return
        title = _clean_text(" ".join(self._buffer), limit=500)
        href = self._anchor.get("href", "")
        if title and len(title) >= 4 and len(self.results) < self.max_results:
            self.results.append((title, href))
        self._anchor = None
        self._buffer = []


class BaiduSearchAdapter:
    """Fixed-endpoint, timeout/size bounded Baidu candidate search."""

    def __init__(
        self,
        *,
        endpoint: str = BAIDU_ENDPOINT,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 4.0,
        max_results: int = 5,
        max_response_bytes: int = 800_000,
        fallback_provider: object | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname != BAIDU_HOST
            or parsed.port is not None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/s"
        ):
            raise ValueError("Baidu endpoint is not allowed")
        if timeout_seconds <= 0 or not 1 <= max_results <= 5 or max_response_bytes <= 0:
            raise ValueError("Baidu limits must be positive and bounded")
        self.endpoint = BAIDU_ENDPOINT
        self.client = client
        self.timeout_seconds = float(timeout_seconds)
        self.max_results = int(max_results)
        self.max_response_bytes = int(max_response_bytes)
        self.fallback_provider = fallback_provider
        self.calls = 0

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

    async def search_news(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        retrieved = datetime.now(UTC)
        if not budget.reserve_operation():
            return self._error(
                ProviderErrorKind.TIMEOUT, "search deadline exceeded", True, retrieved
            )
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            follow_redirects=False,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "Chrome/124.0 Safari/537.36 COURTSIDE/0.1"
                )
            },
        )
        self.calls += 1
        try:
            try:
                response = await client.get(
                    self.endpoint,
                    params={"wd": _query_text(query), "tn": "news"},
                    timeout=min(self.timeout_seconds, max(budget.remaining_ms(), 1) / 1000),
                )
            except TypeError as exc:
                if "timeout" not in str(exc):
                    raise
                response = await client.get(
                    self.endpoint, params={"wd": _query_text(query), "tn": "news"}
                )
            retrieved = datetime.now(UTC)
            if getattr(response, "is_redirect", False):
                return await self._fallback_or_error(
                    query,
                    budget,
                    self._error(
                        ProviderErrorKind.AUTH,
                        "search verification required",
                        True,
                        retrieved,
                    ),
                )
            if response.status_code == 429:
                return await self._fallback_or_error(
                    query,
                    budget,
                    self._error(
                        ProviderErrorKind.RATE_LIMITED,
                        "search rate limited",
                        True,
                        retrieved,
                    ),
                )
            if response.status_code >= 500:
                return await self._fallback_or_error(
                    query,
                    budget,
                    self._error(ProviderErrorKind.HTTP, "search unavailable", True, retrieved),
                )
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
                    ProviderErrorKind.SCHEMA_MISMATCH,
                    "search response too large",
                    False,
                    retrieved,
                )
            text = raw.decode(response.encoding or "utf-8", errors="replace")
            if "安全验证" in text or "wappass.baidu.com" in text:
                return await self._fallback_or_error(
                    query,
                    budget,
                    self._error(
                        ProviderErrorKind.AUTH,
                        "search verification required",
                        True,
                        retrieved,
                    ),
                )
            parser = _BaiduResultParser(self.max_results)
            parser.feed(text)
            if not parser.results and self.fallback_provider is not None:
                return await self._fallback_or_error(
                    query,
                    budget,
                    self._error(
                        ProviderErrorKind.NOT_FOUND,
                        "search returned no results",
                        True,
                        retrieved,
                    ),
                )
            evidence: list[Evidence] = []
            items: list[NewsItem] = []
            for title, source_url in parser.results:
                fingerprint = hashlib.sha256(f"{title}\n{source_url}".encode()).hexdigest()[:20]
                evidence_id = f"baidu:search:{fingerprint}"
                evidence.append(
                    Evidence(
                        evidence_id=evidence_id,
                        source_class=SourceClass.SEARCH,
                        source_ref="baidu.search",
                        url=BAIDU_ENDPOINT,
                        fetched_at_utc=retrieved,
                        data_as_of_utc=None,
                        trust=TrustLevel.MEDIUM,
                        freshness=Freshness.UNKNOWN,
                    )
                )
                items.append(
                    NewsItem(
                        news_id=evidence_id,
                        title=title,
                        summary=None,
                        published_utc=None,
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
                query, budget,
                self._error(ProviderErrorKind.TIMEOUT, "search timed out", True, retrieved),
            )
        except httpx.HTTPError:
            return await self._fallback_or_error(
                query, budget,
                self._error(ProviderErrorKind.HTTP, "search unavailable", True, retrieved),
            )
        finally:
            if own_client:
                await client.aclose()

    async def search_web(
        self, query: NewsQuery, budget: RequestBudget
    ) -> ProviderResult[list[NewsItem]]:
        return await self.search_news(query, budget)


__all__ = ["BAIDU_ENDPOINT", "BaiduSearchAdapter"]
