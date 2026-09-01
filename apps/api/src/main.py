"""FastAPI application factory and local entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.api.src.api.auth_routes import router as auth_router
from apps.api.src.api.highlights_routes import router as highlights_router
from apps.api.src.api.http_routes import router as http_router
from apps.api.src.api.sse_routes import SSEConnectionLimiter
from apps.api.src.api.sse_routes import router as sse_router
from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.config import Settings
from apps.api.src.infrastructure.auth import AuthManager
from apps.api.src.infrastructure.cache import InMemoryTTLCache
from apps.api.src.infrastructure.highlights_cache import SQLiteHighlightsCache
from apps.api.src.providers.ddg_adapter import DuckDuckGoAdapter
from apps.api.src.providers.espn_adapter import ESPNAdapter
from apps.api.src.providers.fixture_provider import FixtureProvider
from apps.api.src.providers.gateway import ProviderGateway
from apps.api.src.providers.search_augmented_provider import SearchAugmentedProvider


def _provider_stack(config: Settings) -> tuple[Any, Any | None]:
    """Build the configured provider and an optional hybrid fallback.

    Fixture mode is the deterministic default.  Live mode talks only to the
    allow-listed public adapter; hybrid mode tries that adapter first and
    falls back to the local snapshot after bounded gateway retries.  Keeping
    this selection at the composition root means domain/application code does
    not branch on environment variables.
    """

    fixture = FixtureProvider()
    mode = str(config.public_data_mode).lower()
    if mode == "fixture":
        return fixture, None
    live = ESPNAdapter(
        base_url=config.espn_base_url,
        timeout_seconds=config.provider_timeout_seconds,
        max_response_bytes=config.provider_max_response_bytes,
        allowed_hosts=config.espn_allowed_hosts,
    )
    # DuckDuckGo is an optional, news/background-only candidate source. Never
    # enable it for fixture mode: the default local profile must remain fully
    # offline and deterministic.
    if bool(getattr(config, "ddg_search_enabled", False)):
        ddg = DuckDuckGoAdapter(
            timeout_seconds=getattr(config, "ddg_timeout_seconds", 3.0),
            max_results=getattr(config, "ddg_max_results", 5),
            max_response_bytes=getattr(config, "ddg_max_response_bytes", 512_000),
        )
        live = SearchAugmentedProvider(live, ddg)
    if mode == "hybrid":
        # Keep the fixture fallback as a separate ProviderPort. Search
        # augmentation belongs only to the live primary path.
        return live, fixture
    return live, None


def create_app(*, settings: Settings | None = None, usecase: ChatUseCase | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    # ``Settings.from_env`` validates itself, while callers injecting a
    # dataclass in tests/deploy code may bypass that constructor path.
    config.validate()
    _validate_capacity_limits(config)
    # API documentation is useful during local development, but exposing
    # OpenAPI on the public interview endpoint would reveal internal routes
    # and wire fields that are intentionally hidden from end users. Keep the
    # public/production profiles closed while retaining the local developer
    # experience.
    public_profile = str(getattr(config, "app_env", "local")).lower() in {
        "public_demo",
        "production",
        "prod",
    }
    app = FastAPI(
        title="NBA Chat Agent",
        version="v1",
        docs_url=None if public_profile else "/docs",
        redoc_url=None,
        openapi_url=None if public_profile else "/openapi.json",
    )
    app.state.settings = config
    app.state.highlights_cache = None
    if bool(getattr(config, "highlights_cache_enabled", False)):
        cache_path = Path(str(getattr(config, "highlights_cache_db", "")))
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # The cache implementation is fail-open. Let it record a degraded
            # state while the existing Provider/in-memory path remains usable.
            pass
        persistent_cache = SQLiteHighlightsCache(
            cache_path,
            max_entries=int(getattr(config, "highlights_cache_max_entries", 5_000)),
            max_payload_bytes=int(
                getattr(config, "highlights_cache_max_payload_bytes", 2_097_152)
            ),
            lease_seconds=int(getattr(config, "highlights_cache_lease_seconds", 30)),
            busy_timeout_ms=int(
                getattr(config, "highlights_cache_busy_timeout_ms", 1_500)
            ),
        )
        app.state.highlights_cache = persistent_cache
        # FastAPI 0.116+ exposes lifecycle registration on the underlying
        # Starlette router.  Register the synchronous close callback there so
        # cache-enabled production profiles start cleanly across supported
        # FastAPI versions.
        app.router.add_event_handler("shutdown", persistent_cache.close)
    # Server-owned map of games exposed by the highlights projection. Chat
    # requests may refer to a selected card by ID; resolving that ID here
    # prevents the browser from supplying untrusted team/score metadata.
    app.state.game_registry = {}
    app.state.game_origin_registry = {}
    app.state.auth_manager = AuthManager(
        password=getattr(config, "app_password", ""),
        password_file=getattr(config, "app_password_file", ""),
        required=bool(getattr(config, "auth_required", False)),
        session_ttl_seconds=int(getattr(config, "auth_session_ttl_seconds", 86_400)),
        max_failed_attempts=int(getattr(config, "auth_max_failed_attempts", 8)),
        lockout_seconds=int(getattr(config, "auth_lockout_seconds", 60)),
    )
    # The limiter is application-scoped so concurrent workers do not each
    # allocate an unbounded number of SSE streams.  The route performs a
    # non-blocking admission check and releases the slot when its generator
    # exits (including disconnect and cancellation paths).
    app.state.sse_connection_limiter = SSEConnectionLimiter(config.max_sse_connections)
    if usecase is None:
        provider, fallback = _provider_stack(config)
        gateway = ProviderGateway(
            provider,
            fallback=fallback,
            cache=InMemoryTTLCache(max_entries=config.cache_max_entries),
            max_retries=config.provider_max_retries,
            news_ttl_seconds=getattr(config, "ddg_cache_ttl_seconds", 300),
        )
        usecase = ChatUseCase(
            provider,
            settings=config,
            gateway=gateway,
            game_registry=app.state.game_registry,
            game_origin_registry=app.state.game_origin_registry,
        )
        app.state.provider = provider
        app.state.fallback_provider = fallback
    else:
        app.state.provider = getattr(usecase, "provider", None)
        app.state.fallback_provider = None
        # Keep injected use cases (tests/embedding) on the same registry used
        # by the highlights routes so a selected card can still scope chat.
        try:
            existing_registry = getattr(usecase, "game_registry", None)
            if isinstance(existing_registry, dict) and existing_registry:
                app.state.game_registry.update(existing_registry)
            usecase.game_registry = app.state.game_registry
            existing_origins = getattr(usecase, "game_origin_registry", None)
            if isinstance(existing_origins, dict) and existing_origins:
                app.state.game_origin_registry.update(existing_origins)
            usecase.game_origin_registry = app.state.game_origin_registry
        except (AttributeError, TypeError):
            pass
    app.state.chat_use_case = usecase
    app.include_router(http_router)
    app.include_router(sse_router)
    app.include_router(highlights_router)
    app.include_router(auth_router)
    origins = list(getattr(config, "allowed_origins", ()) or ())
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            # Login uses an HttpOnly cookie.  Credentials are constrained by
            # the explicit origin allow-list above; wildcard origins are
            # rejected by Settings.validate in production.
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Accept", "X-Requested-With"],
        )

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        """Require the shared-password session on data and chat endpoints."""

        path = request.url.path
        protected = (
            path == "/api/v1/chat"
            or path == "/api/v1/chat/stream"
            or path == "/api/v1/highlights"
            or path == "/api/v1/highlights/availability"
            or path.startswith("/api/v1/highlights/")
        )
        manager: AuthManager = request.app.state.auth_manager
        if protected and manager.enabled and request.method != "OPTIONS":
            if manager.required and not manager.configured:
                response = JSONResponse(
                    status_code=503,
                    content={
                        "status": "failed",
                        "error": {
                            "code": "AUTH_NOT_CONFIGURED",
                            "retryable": False,
                            "message": "服务尚未配置访问密码。",
                        },
                    },
                    headers={"Cache-Control": "no-store"},
                )
                _set_security_headers(response)
                return response
            token = request.cookies.get(config.auth_cookie_name)
            if not manager.is_authenticated(token):
                response = JSONResponse(
                    status_code=401,
                    content={
                        "status": "failed",
                        "error": {
                            "code": "AUTH_REQUIRED",
                            "retryable": False,
                            "message": "请先登录后再访问该服务。",
                        },
                    },
                    headers={
                        "Cache-Control": "no-store",
                        "WWW-Authenticate": "Cookie",
                    },
                )
                _set_security_headers(response)
                return response
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        content_length = request.headers.get("content-length")
        try:
            too_large = (
                content_length is not None and int(content_length) > config.max_request_bytes
            )
        except ValueError:
            too_large = True
        if too_large and request.url.path.startswith("/api/"):
            request_id, session_id = uuid4(), uuid4()
            response = JSONResponse(
                status_code=400,
                content={
                    "request_id": str(request_id),
                    "session_id": str(session_id),
                    "status": "failed",
                    "error": {
                        "code": "INVALID_PAYLOAD",
                        "retryable": False,
                        "message": "请求内容过大，请缩短问题。",
                    },
                },
            )
            _set_security_headers(response)
            return response
        response = await call_next(request)
        # JSONResponse exposes Content-Length; enforce the configured response
        # ceiling before the body is sent.  SSE is streamed and is accounted
        # for by sse_routes.py's cumulative frame budget.
        if request.url.path.startswith("/api/"):
            raw_length = response.headers.get("content-length")
            try:
                response_length = int(raw_length) if raw_length is not None else None
            except (TypeError, ValueError):
                response_length = None
            content_type = response.headers.get("content-type", "").lower()
            if (
                response_length is not None
                and response_length > config.max_response_bytes
                and "text/event-stream" not in content_type
            ):
                response = JSONResponse(
                    status_code=500,
                    content={
                        "request_id": str(uuid4()),
                        "session_id": str(uuid4()),
                        "status": "failed",
                        "error": {
                            "code": "OUTPUT_BLOCKED",
                            "retryable": False,
                            "message": "回答超过安全长度限制，请缩短问题后重试。",
                        },
                    },
                )
        # The demo is served from the same process as the API.  During a
        # rolling refresh, a browser may otherwise keep an older app.js in
        # its HTTP cache and continue to display pre-fix conversation
        # behavior even though the API has already been updated.  Revalidate
        # the HTML/assets on every navigation; ETag/Last-Modified still make
        # unchanged assets inexpensive.
        if request.url.path == "/" or request.url.path.endswith((".js", ".css")):
            response.headers.setdefault("Cache-Control", "no-cache")
        _set_security_headers(response)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError):
        request_id, session_id = uuid4(), uuid4()
        response = JSONResponse(
            status_code=400,
            content={
                "request_id": str(request_id),
                "session_id": str(session_id),
                "status": "failed",
                "error": {
                    "code": "INVALID_PAYLOAD",
                    "retryable": False,
                    "message": "请求格式不正确，请缩短问题或补充必要条件。",
                },
            },
        )
        _set_security_headers(response)
        return response

    # Serve the zero-build web demo from the same origin when the repository
    # contains it.  This gives a deployment a single ``IP:port`` entry point:
    # API routes are registered above and therefore keep precedence, while
    # ``StaticFiles(html=True)`` handles ``/`` and the demo's CSS/JS assets.
    # The conditional keeps the API package usable when installed without the
    # optional web-demo directory (for example, as a slim API-only wheel).
    web_demo_dir = Path(__file__).resolve().parents[2] / "web-demo"
    if web_demo_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_demo_dir, html=True), name="web-demo")

    return app


def _set_security_headers(response: Any) -> None:
    """Apply baseline browser headers to every HTTP response path."""

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")


def _validate_capacity_limits(config: Settings) -> None:
    """Reject unsafe memory/stream limits before constructing the app.

    A lower bound leaves enough room for the mandatory run.started and
    run.error envelopes. Upper bounds prevent a typo in an environment
    variable from allocating an effectively unbounded queue, response, or
    connection table.
    """

    min_event_bytes = 512
    min_response_bytes = 1024
    max_event_bytes = 1_048_576
    max_response_bytes = 16 * 1024 * 1024
    max_queue_depth = 4096
    max_sse_connections = 10_000
    if config.max_event_bytes < min_event_bytes or config.max_event_bytes > max_event_bytes:
        raise ValueError(f"max_event_bytes must be between {min_event_bytes} and {max_event_bytes}")
    if (
        config.max_response_bytes < min_response_bytes
        or config.max_response_bytes > max_response_bytes
    ):
        raise ValueError(
            f"max_response_bytes must be between {min_response_bytes} and {max_response_bytes}"
        )
    # A response must be able to carry at least one bounded event plus the
    # mandatory start envelope and a safe terminal fallback.
    if config.max_response_bytes < config.max_event_bytes + min_event_bytes:
        raise ValueError("max_response_bytes must leave room for a terminal SSE event")
    if config.max_sse_connections > max_sse_connections:
        raise ValueError(f"max_sse_connections must be <= {max_sse_connections}")
    if config.queue_max_depth > max_queue_depth:
        raise ValueError(f"queue_max_depth must be <= {max_queue_depth}")


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("apps.api.src.main:app", host="0.0.0.0", port=8000, reload=False)


__all__ = ["app", "create_app", "run", "_provider_stack"]
