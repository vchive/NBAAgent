"""Contract checks for the server-owned flower search domain marker."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from apps.api.src.application.ports import RequestBudget
from apps.api.src.domain.models import EntityKind, EntityRef, NewsQuery
from apps.api.src.providers.aliyun_iqs_adapter import (
    AliyunIQSSearchAdapter,
)
from apps.api.src.providers.aliyun_iqs_adapter import (
    _query_text as aliyun_query_text,
)
from apps.api.src.providers.baidu_adapter import (
    BaiduSearchAdapter,
)
from apps.api.src.providers.baidu_adapter import (
    _query_text as baidu_query_text,
)
from apps.api.src.providers.ddg_adapter import (
    DuckDuckGoAdapter,
)
from apps.api.src.providers.ddg_adapter import (
    _query_text as ddg_query_text,
)
from apps.api.src.providers.qianfan_search_adapter import (
    QianfanSearchAdapter,
)
from apps.api.src.providers.qianfan_search_adapter import (
    _query_text as qianfan_query_text,
)


def _budget() -> RequestBudget:
    return RequestBudget(datetime.now(UTC) + timedelta(seconds=5), max_provider_operations=4)


def _flower_query() -> NewsQuery:
    return NewsQuery(
        domain="flower",
        subject_refs=[
            EntityRef(
                kind=EntityKind.UNKNOWN,
                canonical_id="flower",
                display_name="花卉园艺",
            ),
            EntityRef(
                kind=EntityKind.UNKNOWN,
                canonical_id="plant",
                display_name="月季",
            ),
        ],
        keywords=["上海九月养护"],
        limit=5,
    )


def test_domain_marker_is_server_owned_and_strict() -> None:
    assert NewsQuery().domain == "nba"
    assert NewsQuery(domain="flower").domain == "flower"
    with pytest.raises(ValueError):
        NewsQuery(domain="search")
    with pytest.raises(ValueError):
        NewsQuery(domain="NBA")


def test_flower_queries_never_receive_legacy_nba_prefix() -> None:
    query = _flower_query()
    builders = (baidu_query_text, qianfan_query_text, aliyun_query_text, ddg_query_text)
    for builder in builders:
        text = builder(query)
        assert "nba" not in text.casefold()
        assert "月季" in text


def test_legacy_queries_keep_nba_scope() -> None:
    # Existing callers do not set the marker and must retain their old search
    # scope.  This is important for the explicit NBA compatibility domain.
    assert "nba" in baidu_query_text(NewsQuery()).casefold()
    assert "nba" in qianfan_query_text(NewsQuery()).casefold()
    assert "nba" in aliyun_query_text(NewsQuery()).casefold()
    assert "nba" in ddg_query_text(NewsQuery()).casefold()


def test_flower_empty_queries_use_gardening_fallbacks() -> None:
    query = NewsQuery(domain="flower")
    assert "nba" not in baidu_query_text(query).casefold()
    assert "nba" not in qianfan_query_text(query).casefold()
    assert "nba" not in aliyun_query_text(query).casefold()
    assert "nba" not in ddg_query_text(query).casefold()


def test_flower_search_requests_do_not_inject_nba_scope() -> None:
    """Check actual egress query fields, not only helper output."""

    async def run() -> None:
        captured: list[tuple[str, str]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                query = request.url.params.get("wd") or request.url.params.get("q") or ""
                captured.append((request.url.host or "", query))
                if request.url.host == "api.duckduckgo.com":
                    return httpx.Response(200, json={"RelatedTopics": []})
                return httpx.Response(
                    200,
                    content=(
                        "<html><h3><a href='https://example.test/flower'>"
                        "月季养护</a></h3></html>"
                    ).encode(),
                )
            payload = json.loads(request.content)
            if request.url.host == "qianfan.baidubce.com":
                captured.append((request.url.host or "", payload["messages"][0]["content"]))
                return httpx.Response(200, json={"references": []})
            captured.append((request.url.host or "", payload["query"]))
            return httpx.Response(200, json={"pageItems": []})

        query = _flower_query()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await BaiduSearchAdapter(client=client).search_news(query, _budget())
            await QianfanSearchAdapter(api_key="test", client=client).search_news(query, _budget())
            await AliyunIQSSearchAdapter(api_key="test", client=client).search_news(
                query, _budget()
            )
            await DuckDuckGoAdapter(client=client).search_news(query, _budget())
        finally:
            await client.aclose()

        assert len(captured) == 4
        for _host, text in captured:
            assert "nba" not in text.casefold()

    asyncio.run(run())
