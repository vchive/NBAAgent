"""HTTP routes for health and synchronous chat."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.src.api.schemas import ChatRequest, ChatResponse, ErrorDetail, ErrorResponse
from apps.api.src.application.chat_use_case import ChatResult, ChatUseCase

router = APIRouter()


def _usecase(request: Request) -> ChatUseCase:
    return request.app.state.chat_use_case


def _technical_status(code: str) -> int:
    return {
        "INVALID_PAYLOAD": 400,
        "UPSTREAM_RATE_LIMITED": 429,
        "SERVICE_BUSY": 503,
        "UPSTREAM_AUTH": 502,
        "INVALID_UPSTREAM_DATA": 502,
        "UPSTREAM_TIMEOUT": 504,
        "COMPOSER_UNAVAILABLE": 500,
        "OUTPUT_BLOCKED": 500,
    }.get(code, 500)


def _wire(result: ChatResult) -> ChatResponse | ErrorResponse:
    try:
        if result.status == "failed" or result.error is not None:
            error = result.error or {
                "code": "SERVICE_BUSY",
                "retryable": True,
                "message": "服务暂时不可用，请稍后重试。",
            }
            return ErrorResponse(
                request_id=result.request_id,
                session_id=result.session_id,
                error=ErrorDetail.model_validate(error),
            )
        return ChatResponse.from_domain(
            request_id=result.request_id,
            session_id=result.session_id,
            status=result.status,
            answer={
                "markdown": result.answer_markdown,
                "blocks": result.blocks,
                "evidence_state": result.evidence_state,
                "corrections": result.corrections,
                "follow_up": result.follow_up,
            },
            latency_ms=result.latency_ms,
            as_of_beijing=result.as_of_beijing,
        )
    except Exception:
        # A runtime/composer output is untrusted at this boundary.  Do not let
        # malformed blocks (including nested provider metadata) escape as a
        # framework 500 or reveal validation details; map them to the public
        # technical error vocabulary instead.
        request_id = result.request_id if isinstance(result.request_id, UUID) else uuid4()
        session_id = result.session_id if isinstance(result.session_id, UUID) else uuid4()
        return ErrorResponse(
            request_id=request_id,
            session_id=session_id,
            error=ErrorDetail(
                code="OUTPUT_BLOCKED",
                retryable=False,
                message="回答未通过安全校验，请换一种问法。",
            ),
        )


@router.get("/healthz")
async def healthz(request: Request):
    settings = request.app.state.settings
    usecase = _usecase(request)
    mode = str(getattr(settings, "public_data_mode", "fixture")).lower()
    gateway = getattr(usecase, "gateway", None)
    store = getattr(usecase, "session_store", None)
    cache = getattr(gateway, "cache", None)
    hermes_runtime = getattr(usecase, "hermes_runtime", None)
    hermes_mode = str(getattr(settings, "hermes_lite_mode", "off")).lower()
    session_status = "ok" if store is not None else "degraded"
    cache_status = "ok" if cache is not None else "degraded"
    if hermes_mode == "off":
        hermes_status = "disabled"
    else:
        self_test = getattr(hermes_runtime, "capability_self_test", None)
        hermes_status = "ok" if self_test is None or bool(self_test()) else "degraded"
    dependency_values = (session_status, cache_status, hermes_status)
    status = "degraded" if "degraded" in dependency_values else "ok"
    return {
        "status": status,
        "version": "v1",
        "mode": mode,
        "dependencies": {
            "session_store": session_status,
            "cache": cache_status,
            "hermes": hermes_status,
        },
    }


@router.get("/livez")
async def livez():
    return {"status": "ok", "version": "v1"}


@router.get("/readyz")
async def readyz(request: Request):
    # Readiness covers only local dependencies.  A live public provider is
    # intentionally not probed here, so an upstream outage does not make a
    # healthy API instance disappear from service discovery.
    usecase = _usecase(request)
    settings = request.app.state.settings
    store_ok = getattr(usecase, "session_store", None) is not None
    cache_ok = getattr(getattr(usecase, "gateway", None), "cache", None) is not None
    hermes_mode = str(getattr(settings, "hermes_lite_mode", "off")).lower()
    hermes_runtime = getattr(usecase, "hermes_runtime", None)
    if hermes_mode == "off":
        hermes_status = "disabled"
    else:
        self_test = getattr(hermes_runtime, "capability_self_test", None)
        hermes_status = "ok" if self_test is None or bool(self_test()) else "degraded"
    status = "ok" if store_ok and cache_ok else "not_ready"
    payload = {
        "status": status,
        "version": "v1",
        "mode": str(getattr(settings, "public_data_mode", "fixture")).lower(),
        "dependencies": {
            "session_store": "ok" if store_ok else "not_ready",
            "cache": "ok" if cache_ok else "not_ready",
            "hermes": hermes_status,
        },
    }
    return JSONResponse(status_code=200 if status == "ok" else 503, content=payload)


@router.post("/api/v1/chat")
async def chat(request: Request, body: ChatRequest):
    result = await _usecase(request).handle(body)
    response = _wire(result)
    if isinstance(response, ErrorResponse):
        return JSONResponse(
            status_code=_technical_status(response.error.code.value),
            content=response.model_dump(mode="json"),
            headers={"X-Request-Id": str(response.request_id)},
        )
    return JSONResponse(
        status_code=200,
        content=response.model_dump(mode="json"),
        headers={"X-Request-Id": str(response.request_id)},
    )


__all__ = ["router"]
