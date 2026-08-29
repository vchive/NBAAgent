"""Password login/logout endpoints for the single-user demo deployment."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from apps.api.src.infrastructure.auth import AuthManager

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Keep the bound generous enough for a passphrase while preventing a large
    # body from becoming an authentication oracle or memory sink.
    password: str = Field(min_length=1, max_length=512)


def _manager(request: Request) -> AuthManager:
    return request.app.state.auth_manager


def _error(code: str, message: str, status_code: int, *, retryable: bool = False):
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "failed",
            "error": {"code": code, "retryable": retryable, "message": message},
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/status")
async def auth_status(request: Request):
    manager = _manager(request)
    token = request.cookies.get(request.app.state.settings.auth_cookie_name)
    return JSONResponse(
        content=manager.status(token),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/login")
async def auth_login(request: Request, body: LoginRequest):
    manager = _manager(request)
    if manager.required and not manager.configured:
        # Fail closed: an accidentally missing mounted secret must never turn a
        # production deployment into an anonymous service.
        return _error("AUTH_NOT_CONFIGURED", "服务尚未配置访问密码。", 503, retryable=False)
    client_ip = request.client.host if request.client else "unknown"
    result = manager.login(body.password, client_ip)
    if result.rate_limited:
        return _error("AUTH_RATE_LIMITED", "尝试次数过多，请稍后再试。", 429, retryable=True)
    if not result.success or not result.token:
        return _error("AUTH_INVALID", "密码不正确。", 401, retryable=False)

    response = JSONResponse(
        content={"authenticated": True},
        headers={"Cache-Control": "no-store"},
    )
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=result.token,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=bool(settings.auth_cookie_secure),
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
async def auth_logout(request: Request):
    settings = request.app.state.settings
    manager = _manager(request)
    manager.logout(request.cookies.get(settings.auth_cookie_name))
    response = JSONResponse(
        content={"authenticated": False},
        headers={"Cache-Control": "no-store"},
    )
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        secure=bool(settings.auth_cookie_secure),
        samesite="lax",
    )
    return response


__all__ = ["router", "LoginRequest"]
