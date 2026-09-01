"""HTTP endpoint for the left-rail scoreboard projection."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
from apps.api.src.infrastructure.highlights_cache import (
    SQLiteHighlightsCache,
    stable_cache_key,
)

router = APIRouter()


def _cache_now(service: HighlightsService):
    return service._now()


def _persistent_cache(request: Request) -> SQLiteHighlightsCache | None:
    value = getattr(request.app.state, "highlights_cache", None)
    return value if isinstance(value, SQLiteHighlightsCache) and value.available else None


def _cache_ttl(request: Request, name: str, default: int) -> int:
    settings = getattr(request.app.state, "settings", None)
    return int(getattr(settings, name, default))


def _remember_cached_projection(
    service: HighlightsService,
    value: BaseModel,
    *,
    timezone_name: str,
) -> None:
    if isinstance(value, HighlightDetailResponse):
        games = [value.game]
    elif isinstance(value, (HighlightsResponse, HighlightsRangeResponse)):
        games = value.games
    else:
        games = []
    service.remember_public_games(games, timezone_name=timezone_name)


def _refresh_tasks(request: Request) -> set[asyncio.Task[None]]:
    tasks = getattr(request.app.state, "highlights_refresh_tasks", None)
    if not isinstance(tasks, set):
        tasks = set()
        request.app.state.highlights_refresh_tasks = tasks
    return tasks


def _cache_locks(request: Request) -> dict[str, asyncio.Lock]:
    locks = getattr(request.app.state, "highlights_cache_locks", None)
    if not isinstance(locks, dict):
        locks = {}
        request.app.state.highlights_cache_locks = locks
    return locks


def _schedule_refresh[ProjectionT: BaseModel](
    request: Request,
    *,
    cache: SQLiteHighlightsCache,
    key: str,
    kind: str,
    ttl_seconds: int | Callable[[ProjectionT], int],
    now,
    loader: Callable[[], Awaitable[ProjectionT]],
) -> None:
    owner = cache.acquire_refresh(key, now=now)
    if owner is None:
        return

    async def refresh() -> None:
        try:
            value = await loader()
            ttl = ttl_seconds(value) if callable(ttl_seconds) else ttl_seconds
            cache.set(key, kind, value, ttl_seconds=ttl, now=now)
        except Exception:
            # A stale historical projection remains valid when a low-priority
            # refresh fails. The foreground response has already completed.
            pass
        finally:
            cache.release_refresh(key, owner)

    task = asyncio.create_task(refresh())
    tasks = _refresh_tasks(request)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _load_cached_projection[ProjectionT: BaseModel](
    request: Request,
    service: HighlightsService,
    *,
    key: str,
    kind: str,
    model: type[ProjectionT],
    ttl_seconds: int | Callable[[ProjectionT], int],
    timezone_name: str,
    allow_stale: bool,
    stale_value_allowed: Callable[[ProjectionT], bool] | None = None,
    loader: Callable[[], Awaitable[ProjectionT]],
) -> ProjectionT:
    cache = _persistent_cache(request)
    if cache is None:
        return await loader()
    now = _cache_now(service)
    hit = cache.get(key, model, now=now, allow_stale=allow_stale)
    if hit is not None and (
        not hit.stale
        or stale_value_allowed is None
        or stale_value_allowed(hit.value)
    ):
        _remember_cached_projection(
            service,
            hit.value,
            timezone_name=timezone_name,
        )
        if hit.stale:
            _schedule_refresh(
                request,
                cache=cache,
                key=key,
                kind=kind,
                ttl_seconds=ttl_seconds,
                now=now,
                loader=loader,
            )
        return hit.value

    # Coalesce simultaneous misses inside the single-process demo. Recheck
    # after acquiring the lock because another request may have populated the
    # SQLite row while this one waited.
    lock = _cache_locks(request).setdefault(key, asyncio.Lock())
    async with lock:
        now = _cache_now(service)
        hit = cache.get(key, model, now=now, allow_stale=False)
        if hit is not None:
            _remember_cached_projection(
                service,
                hit.value,
                timezone_name=timezone_name,
            )
            return hit.value
        value = await loader()
        ttl = ttl_seconds(value) if callable(ttl_seconds) else ttl_seconds
        cache.set(key, kind, value, ttl_seconds=ttl, now=now)
        return value


def _schedule_detail_prefetch(
    request: Request,
    service: HighlightsService,
    games,
    *,
    timezone_name: str,
) -> None:
    """Warm at most five completed details without delaying the list response."""

    cache = _persistent_cache(request)
    if cache is None:
        return
    final_games = [game for game in list(games or []) if game.status == "final"][:5]
    if not final_games:
        return
    settings = getattr(request.app.state, "settings", None)
    detail_ttl = int(getattr(settings, "highlights_cache_detail_ttl_seconds", 604_800))
    live_ttl = int(getattr(settings, "highlights_cache_live_ttl_seconds", 45))

    async def prefetch_one(game) -> None:
        key = stable_cache_key("detail", timezone_name, game.game_id)
        now = _cache_now(service)
        if cache.get(
            key,
            HighlightDetailResponse,
            now=now,
            allow_stale=True,
            count_metrics=False,
        ) is not None:
            return
        owner = cache.acquire_refresh(key, now=now)
        if owner is None:
            return
        try:
            value = await service.detail(game.game_id, timezone_name=timezone_name)
            ttl = detail_ttl if value.game.status == "final" else live_ttl
            cache.set(key, "detail", value, ttl_seconds=ttl, now=now)
        except Exception:
            pass
        finally:
            cache.release_refresh(key, owner)

    async def prefetch() -> None:
        semaphore = asyncio.Semaphore(2)

        async def bounded(game) -> None:
            async with semaphore:
                await prefetch_one(game)

        await asyncio.gather(*(bounded(game) for game in final_games))

    task = asyncio.create_task(prefetch())
    tasks = _refresh_tasks(request)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


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
    service = HighlightsService(
        gateway,
        clock=getattr(usecase, "clock", None),
        game_registry=getattr(request.app.state, "game_registry", None),
        game_origin_registry=getattr(request.app.state, "game_origin_registry", None),
    )
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
    service = HighlightsService(
        gateway,
        clock=getattr(usecase, "clock", None),
        game_registry=getattr(request.app.state, "game_registry", None),
        game_origin_registry=getattr(request.app.state, "game_origin_registry", None),
    )
    try:
        zone = validate_timezone(timezone_name)
        configured_demo = _fixture_demo_date(request)
        reference_day = configured_demo or service._now().astimezone(zone).date()
        key = stable_cache_key("recent", zone.key, reference_day.isoformat(), limit)

        async def load_recent() -> HighlightsRangeResponse:
            return await service.recent(
                limit=limit,
                timezone_name=zone.key,
                reference_day=configured_demo,
            )

        result = await _load_cached_projection(
            request,
            service,
            key=key,
            kind="recent",
            model=HighlightsRangeResponse,
            ttl_seconds=_cache_ttl(
                request,
                "highlights_cache_recent_ttl_seconds",
                900,
            ),
            timezone_name=zone.key,
            allow_stale=True,
            loader=load_recent,
        )
        _schedule_detail_prefetch(
            request,
            service,
            result.games,
            timezone_name=zone.key,
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
    service = HighlightsService(
        gateway,
        clock=getattr(usecase, "clock", None),
        game_registry=getattr(request.app.state, "game_registry", None),
        game_origin_registry=getattr(request.app.state, "game_origin_registry", None),
    )
    try:
        local_today = service._now().astimezone(zone).date()
        historical = end < local_today
        key = stable_cache_key(
            "range",
            zone.key,
            start.isoformat(),
            end.isoformat(),
        )

        async def load_range() -> HighlightsRangeResponse:
            return await service.for_range(start, end, timezone_name=zone.key)

        result = await _load_cached_projection(
            request,
            service,
            key=key,
            kind="range",
            model=HighlightsRangeResponse,
            ttl_seconds=_cache_ttl(
                request,
                (
                    "highlights_cache_history_ttl_seconds"
                    if historical
                    else "highlights_cache_live_ttl_seconds"
                ),
                86_400 if historical else 45,
            ),
            timezone_name=zone.key,
            allow_stale=historical,
            loader=load_range,
        )
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
    service = HighlightsService(
        gateway,
        clock=getattr(usecase, "clock", None),
        game_registry=getattr(request.app.state, "game_registry", None),
        game_origin_registry=getattr(request.app.state, "game_origin_registry", None),
    )
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
        local_today = service._now().astimezone(zone).date()
        historical = target < local_today
        key = stable_cache_key("date", timezone_name, target.isoformat())

        async def load_date() -> HighlightsResponse:
            return await service.for_date(target, timezone_name=timezone_name)

        result = await _load_cached_projection(
            request,
            service,
            key=key,
            kind="date",
            model=HighlightsResponse,
            ttl_seconds=_cache_ttl(
                request,
                (
                    "highlights_cache_history_ttl_seconds"
                    if historical
                    else "highlights_cache_live_ttl_seconds"
                ),
                86_400 if historical else 45,
            ),
            timezone_name=timezone_name,
            allow_stale=historical,
            loader=load_date,
        )
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
    service = HighlightsService(
        gateway,
        clock=getattr(usecase, "clock", None),
        game_registry=getattr(request.app.state, "game_registry", None),
        game_origin_registry=getattr(request.app.state, "game_origin_registry", None),
    )
    try:
        timezone_name = validate_timezone(timezone_name).key

        async def load_detail() -> HighlightDetailResponse:
            return await service.detail(game_id, timezone_name=timezone_name)

        key = stable_cache_key("detail", timezone_name, game_id)
        # Final details are immutable enough for stale-while-revalidate. Live
        # or scheduled details must be fresh and use the short TTL.
        final_ttl = _cache_ttl(
            request,
            "highlights_cache_detail_ttl_seconds",
            604_800,
        )
        live_ttl = _cache_ttl(
            request,
            "highlights_cache_live_ttl_seconds",
            45,
        )

        def detail_ttl_for(value: HighlightDetailResponse) -> int:
            return final_ttl if value.game.status == "final" else live_ttl

        cached = await _load_cached_projection(
            request,
            service,
            key=key,
            kind="detail",
            model=HighlightDetailResponse,
            ttl_seconds=detail_ttl_for,
            timezone_name=timezone_name,
            allow_stale=True,
            stale_value_allowed=lambda value: value.game.status == "final",
            loader=load_detail,
        )
        return cached
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
