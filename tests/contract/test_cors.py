"""Browser-origin smoke checks for the configured local preview ports."""

from __future__ import annotations

import httpx
import pytest

from apps.api.src.main import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:54572",
        "http://127.0.0.1:54572",
    ],
)
async def test_codex_preview_origin_is_allowed_for_chat_post(origin: str) -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test"
    ) as client:
        response = await client.options(
            "/api/v1/chat",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
    assert "POST" in response.headers.get("access-control-allow-methods", "")
