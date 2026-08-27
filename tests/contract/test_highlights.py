from __future__ import annotations

import httpx
import pytest

from apps.api.src.main import create_app


@pytest.mark.asyncio
async def test_highlights_contract_valid_empty_and_future_dates() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        good = await client.get("/api/v1/highlights?date=2026-06-12&timezone=Asia/Shanghai")
        empty = await client.get("/api/v1/highlights?date=2026-06-13&timezone=Asia/Shanghai")
        future = await client.get("/api/v1/highlights?date=2999-01-01&timezone=Asia/Shanghai")
    assert good.status_code == 200 and good.json()["games"]
    assert good.json()["date"] == "2026-06-12"
    assert empty.status_code == 200 and empty.json()["games"] == []
    assert future.status_code == 400 and future.json()["error"]["code"] == "INVALID_PAYLOAD"
