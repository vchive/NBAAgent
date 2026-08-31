"""HTTP endpoint for the left-rail scoreboard projection."""

from __future__ import annotations

import re
from datetime import date, timedelta
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from apps.api.src.api.schemas import (
    ErrorResponse,
    HighlightDetailResponse,
    HighlightsAvailabilityResponse,
    HighlightsRangeResponse,
    HighlightsResponse,
)
from apps.api.src.application.highlights import (
    FutureHighlightsDateError,
    HighlightsAvailabilityRangeError,
    HighlightsProviderError,
    HighlightsService,
)
from apps.api.src.domain.time_policy import validate_timezone

router = APIRouter()


@router.get(
    "/api/v1/highlights/availability",
    response_model=HighlightsAvailabilityResponse,
    response_model_by_alias=True,
    responses={
        400: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def highlights_availability(
    request: Request,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    timezone_name: str = Query(default="Asia/Shanghai", alias="timezone"),
):
    """Return a bounded calendar projection for the history date picker.

    The range is optional for convenience: when omitted, the caller's current
    local month is returned.  Supplying one endpoint of the range without the
    other is a malformed request.  Future days are represented as ``unknown``
    in the successful response so the browser can disable them locally while
    preserving the distinction from a verified no-game day.
    """

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
        if (from_date is None) != (to_date is None):
            raise ValueError("from and to must be supplied together")
        if from_date is None and to_date is None:
            configured_demo = _fixture_demo_date(request)
            today = configured_demo or service._now().astimezone(zone).date()
            start = today.replace(day=1)
            next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = next_month - timedelta(days=1)
        else:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", from_date or ""):
                raise ValueError("from must use YYYY-MM-DD")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", to_date or ""):
                raise ValueError("to must use YYYY-MM-DD")
            start = date.fromisoformat(from_date or "")
            end = date.fromisoformat(to_date or "")
    except (ValueError, TypeError):
        return _error_response("INVALID_PAYLOAD", "日期或时区格式不正确。", 400)
    try:
        result = await service.availability(start, end, timezone_name=timezone_name)
    except HighlightsAvailabilityRangeError:
        return _error_response(
            "INVALID_PAYLOAD",
            "日期范围必须按顺序填写，且不能超过 31 天。",
            400,
        )
    except HighlightsProviderError as exc:
        return _error_response(exc.code, exc.message, exc.status_code, retryable=exc.retryable)
    return result


@router.get(
    "/api/v1/highlights/recent",
    response_model=HighlightsRangeResponse,
    response_model_by_alias=True,
    responses={
        400: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def highlights_recent(
    request: Request,
    limit: int = Query(default=5, ge=1, le=20),
    timezone_name: str = Query(default="Asia/Shanghai", alias="timezone"),
):
    """Return the latest completed games for the default review view."""

    usecase = getattr(request.app.state, "chat_use_case", None)
    gateway = getattr(usecase, "gateway", None)
    if gateway is None:
        return _error_response(
            "SERVICE_BUSY",
            "历史赛事服务暂时不可用，请稍后重试。",
            503,
            retryable=True,
        )
    service = HighlightsService(gateway, clock=getattr(usecase, "clock", None))
    try:
        zone = validate_timezone(timezone_name)
        configured_demo = _fixture_demo_date(request)
        result = await service.recent(
            limit=limit,
            timezone_name=zone.key,
            reference_day=configured_demo,
        )
    except HighlightsAvailabilityRangeError:
        return _error_response("INVALID_PAYLOAD", "最近比赛数量必须在 1–20 场之间。", 400)
    except FutureHighlightsDateError:
        return _error_response("INVALID_PAYLOAD", "不能查询未来日期。", 400)
    except HighlightsProviderError as exc:
        return _error_response(exc.code, exc.message, exc.status_code, retryable=exc.retryable)
    return result


@router.get(
    "/api/v1/highlights/range",
    response_model=HighlightsRangeResponse,
    response_model_by_alias=True,
    responses={
        400: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def highlights_range(
    request: Request,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    timezone_name: str = Query(default="Asia/Shanghai", alias="timezone"),
):
    """Return all games in a caller-selected local-date interval."""

    usecase = getattr(request.app.state, "chat_use_case", None)
    gateway = getattr(usecase, "gateway", None)
    if gateway is None:
        return _error_response(
            "SERVICE_BUSY",
            "历史赛事服务暂时不可用，请稍后重试。",
            503,
            retryable=True,
        )
    try:
        zone = validate_timezone(timezone_name)
        if not from_date or not to_date:
            raise ValueError("from and to are required")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", from_date):
            raise ValueError("from must use YYYY-MM-DD")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", to_date):
            raise ValueError("to must use YYYY-MM-DD")
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
    except (ValueError, TypeError):
        return _error_response("INVALID_PAYLOAD", "日期或时区格式不正确。", 400)
    service = HighlightsService(gateway, clock=getattr(usecase, "clock", None))
    try:
        result = await service.for_range(start, end, timezone_name=zone.key)
    except FutureHighlightsDateError:
        return _error_response("INVALID_PAYLOAD", "不能查询未来日期。", 400)
    except HighlightsAvailabilityRangeError:
        return _error_response(
            "INVALID_PAYLOAD",
            "日期范围必须按顺序填写，且不能超过 93 天。",
            400,
        )
    except HighlightsProviderError as exc:
        return _error_response(exc.code, exc.message, exc.status_code, retryable=exc.retryable)
    return result


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
            # A fixture deployment can opt into a fixed, populated day so an
            # interview/demo instance remains useful even when the real
            # calendar has no game. Live and hybrid modes deliberately ignore
            # this setting and use the caller's actual local day.
            target = _fixture_demo_date(request) or service._now().astimezone(zone).date()
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


@router.get(
    "/api/v1/highlights/{game_id}/detail",
    response_model=HighlightDetailResponse,
    response_model_by_alias=True,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def highlight_detail(
    request: Request,
    game_id: str,
    timezone_name: str = Query(default="Asia/Shanghai", alias="timezone"),
):
    """Return safe summary/PBP data for the selected scoreboard card."""

    usecase = getattr(request.app.state, "chat_use_case", None)
    gateway = getattr(usecase, "gateway", None)
    if gateway is None:
        return _error_response(
            "SERVICE_BUSY",
            "比赛详情服务暂时不可用，请稍后重试。",
            503,
            retryable=True,
        )
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", game_id):
        return _error_response("INVALID_PAYLOAD", "比赛标识格式不正确。", 400)
    service = HighlightsService(gateway, clock=getattr(usecase, "clock", None))
    try:
        timezone_name = validate_timezone(timezone_name).key
        return await service.detail(game_id, timezone_name=timezone_name)
    except HighlightsProviderError as exc:
        return _error_response(exc.code, exc.message, exc.status_code, retryable=exc.retryable)


def _fixture_demo_date(request: Request) -> date | None:
    """Return the configured fixture day, if this is a fixture deployment."""

    settings = getattr(request.app.state, "settings", None)
    if str(getattr(settings, "public_data_mode", "fixture")).lower() != "fixture":
        return None
    raw = str(getattr(settings, "highlights_demo_date", "") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        # Settings validation normally catches this. Keep the route fail-safe
        # for injected test settings rather than turning health endpoints into
        # a traceback.
        return None


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
