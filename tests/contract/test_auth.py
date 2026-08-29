from __future__ import annotations

import httpx
import pytest

from apps.api.src.config import Settings
from apps.api.src.main import create_app


def _settings(**overrides):
    values = {
        "auth_required": True,
        "app_password": "interview-pass-123",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_protected_routes_require_login_but_health_stays_public() -> None:
    app = create_app(settings=_settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        chat = await client.post("/api/v1/chat", json={"message": "NBA 比分"})
        highlights = await client.get("/api/v1/highlights")
        health = await client.get("/healthz")
        ready = await client.get("/readyz")

    assert chat.status_code == 401
    assert highlights.status_code == 401
    assert health.status_code == 200
    assert ready.status_code == 200
    assert health.json()["dependencies"]["auth"] == "ok"


@pytest.mark.asyncio
async def test_login_cookie_protects_chat_and_logout_revokes_it() -> None:
    app = create_app(settings=_settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        bad = await client.post("/api/v1/auth/login", json={"password": "wrong"})
        login = await client.post(
            "/api/v1/auth/login", json={"password": "interview-pass-123"}
        )
        chat = await client.post("/api/v1/chat", json={"message": "NBA 比分"})
        stream = await client.post(
            "/api/v1/chat/stream",
            json={"message": "NBA 比分"},
            headers={"Accept": "text/event-stream"},
        )
        logout = await client.post("/api/v1/auth/logout")
        after = await client.post("/api/v1/chat", json={"message": "NBA 比分"})

    assert bad.status_code == 401
    assert "interview-pass-123" not in bad.text
    assert login.status_code == 200
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "interview-pass-123" not in login.text
    assert chat.status_code == 200
    assert stream.status_code == 200
    assert "event: message.completed" in stream.text
    assert logout.status_code == 200
    assert after.status_code == 401


@pytest.mark.asyncio
async def test_missing_required_password_fails_closed() -> None:
    app = create_app(settings=Settings(auth_required=True))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        status = await client.get("/api/v1/auth/status")
        login = await client.post("/api/v1/auth/login", json={"password": "anything"})
        protected = await client.get("/api/v1/highlights")
        ready = await client.get("/readyz")

    assert status.json() == {"enabled": True, "authenticated": False}
    assert login.status_code == 503
    assert protected.status_code == 503
    assert ready.status_code == 503
    assert ready.json()["dependencies"]["auth"] == "degraded"


@pytest.mark.asyncio
async def test_password_file_is_loaded_without_exposing_contents(tmp_path) -> None:
    secret = tmp_path / "app_password"
    secret.write_text("file-pass-456\n", encoding="utf-8")
    app = create_app(settings=Settings(auth_required=True, app_password_file=str(secret)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/auth/login", json={"password": "file-pass-456"})

    assert response.status_code == 200
    assert "file-pass-456" not in response.text
