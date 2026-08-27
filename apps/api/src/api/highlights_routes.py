"""HTTP endpoint for the left-rail scoreboard projection."""

from __future__ import annotations

import re
from datetime import date
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from apps.api.src.api.schemas import ErrorResponse, HighlightsResponse
from apps.api.src.application.highlights import (
    FutureHighlightsDateError,
    HighlightsProviderError,
    HighlightsService,
)
from apps.api.src.domain.time_policy import validate_timezone

router = APIRouter()


@router.get(
    "/api/v1/highlights",
    response_model=HighlightsResponse,
    responses={
        400: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def highlights(
    request: Request,
    date_value: str | None = Query(default=None, alias="date"),
    timezone_name: str = Query(default="Asia/Shanghai", alias="timezone"),
):
    usecase = getattr(request.app.state, "chat_use_case", None)
    gateway = getattr(usecase, "gateway", None)
    if gateway is None:
        return _error_response(
            "SERVICE_BUSY",
            "日期赛事服务暂时不可用，请稍后重试。",
            503,
            retryable=True,
        )
    service = HighlightsService(gateway, clock=getattr(usecase, "clock", None))
    try:
        zone = validate_timezone(timezone_name)
        timezone_name = zone.key
        if date_value is None:
            target = service._now().astimezone(zone).date()
        else:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
                raise ValueError("date must use YYYY-MM-DD")
            target = date.fromisoformat(date_value)
    except (ValueError, TypeError):
        return _error_response("INVALID_PAYLOAD", "日期或时区格式不正确。", 400)
    try:
        result = await service.for_date(target, timezone_name=timezone_name)
    except FutureHighlightsDateError:
        return _error_response("INVALID_PAYLOAD", "不能查询未来日期。", 400)
    except HighlightsProviderError as exc:
        return _error_response(exc.code, exc.message, exc.status_code, retryable=exc.retryable)
    return result


def _error_response(
    code: str,
    message: str,
    status_code: int,
    *,
    retryable: bool = False,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "request_id": str(uuid4()),
            "session_id": str(uuid4()),
            "status": "failed",
            "error": {
                "code": code,
                "retryable": retryable,
                "message": message,
            },
        },
    )


__all__ = ["router"]
