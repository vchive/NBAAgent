"""Small, dependency-free application configuration.

The service intentionally reads environment variables through one typed object so tests can
inject a deterministic fixture profile without importing provider or model implementations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = "local"
    public_data_mode: str = "fixture"
    provider_timeout_seconds: float = 8.0
    provider_max_retries: int = 2
    provider_max_response_bytes: int = 2_000_000
    espn_base_url: str = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
    espn_allowed_hosts: tuple[str, ...] = (
        "site.api.espn.com",
        "site.web.api.espn.com",
    )
    request_deadline_ms: int = 10_000
    queue_wait_deadline_ms: int = 1_000
    max_provider_operations: int = 4
    cache_ttl_live_seconds: int = 45
    cache_ttl_boxscore_seconds: int = 300
    cache_ttl_history_seconds: int = 86_400
    session_ttl_seconds: int = 86_400
    max_session_turns: int = 8
    max_session_bytes: int = 16_384
    cache_max_entries: int = 10_000
    llm_mode: str = "mock"
    llm_timeout_seconds: float = 8.0
    # Permit the documented local static demo origins by default.  Production
    # deployments should provide an explicit allow-list; wildcard origins are
    # rejected below.
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: (
            "http://127.0.0.1:4173",
            "http://localhost:4173",
            # The Codex in-app preview serves the same static demo on 54572.
            # Keeping both loopback host spellings makes local browser testing
            # work without weakening production's explicit allow-list policy.
            "http://127.0.0.1:54572",
            "http://localhost:54572",
        )
    )
    log_level: str = "INFO"
    runtime_profile: str = "template"
    hermes_lite_mode: str = "off"
    hermes_lite_endpoint: str = ""
    hermes_lite_max_tokens: int = 800
    hermes_lite_timeout_ms: int = 2_500
    max_request_bytes: int = 32_768
    max_event_bytes: int = 16_384
    max_response_bytes: int = 262_144
    max_sse_connections: int = 100
    max_inflight_requests: int = 32
    queue_max_depth: int = 64
    shutdown_drain_ms: int = 10_000
    egress_allowlist: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls) -> Settings:
        settings = cls(
            app_env=os.getenv("APP_ENV", "local"),
            public_data_mode=os.getenv("PUBLIC_DATA_MODE", "fixture").lower(),
            provider_timeout_seconds=_float("PROVIDER_TIMEOUT_SECONDS", 8.0),
            provider_max_retries=_int("PROVIDER_MAX_RETRIES", 2),
            provider_max_response_bytes=_int("PROVIDER_MAX_RESPONSE_BYTES", 2_000_000),
            espn_base_url=os.getenv(
                "ESPN_BASE_URL",
                "https://site.api.espn.com/apis/site/v2/sports/basketball/nba",
            ).strip(),
            espn_allowed_hosts=_csv(
                "ESPN_ALLOWED_HOSTS",
                ("site.api.espn.com", "site.web.api.espn.com"),
            ),
            request_deadline_ms=_int("REQUEST_DEADLINE_MS", 10_000),
            queue_wait_deadline_ms=_int("QUEUE_WAIT_DEADLINE_MS", 1_000),
            max_provider_operations=_int("MAX_PROVIDER_OPERATIONS", 4),
            cache_ttl_live_seconds=_int("CACHE_TTL_LIVE_SECONDS", 45),
            cache_ttl_boxscore_seconds=_int("CACHE_TTL_BOXSCORE_SECONDS", 300),
            cache_ttl_history_seconds=_int("CACHE_TTL_HISTORY_SECONDS", 86_400),
            session_ttl_seconds=_int("SESSION_TTL_SECONDS", 86_400),
            max_session_turns=_int("MAX_SESSION_TURNS", 8),
            max_session_bytes=_int("MAX_SESSION_BYTES", 16_384),
            cache_max_entries=_int("CACHE_MAX_ENTRIES", 10_000),
            llm_mode=os.getenv("LLM_MODE", "mock").lower(),
            llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 8.0),
            allowed_origins=_csv(
                "ALLOWED_ORIGINS",
                (
                    "http://127.0.0.1:4173",
                    "http://localhost:4173",
                    "http://127.0.0.1:54572",
                    "http://localhost:54572",
                ),
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            runtime_profile=os.getenv("RUNTIME_PROFILE", "template").lower(),
            hermes_lite_mode=os.getenv("HERMES_LITE_MODE", "off").lower(),
            hermes_lite_endpoint=os.getenv("HERMES_LITE_ENDPOINT", ""),
            hermes_lite_max_tokens=_int("HERMES_LITE_MAX_TOKENS", 800),
            hermes_lite_timeout_ms=_int("HERMES_LITE_TIMEOUT_MS", 2_500),
            max_request_bytes=_int("MAX_REQUEST_BYTES", 32_768),
            max_event_bytes=_int("MAX_EVENT_BYTES", 16_384),
            max_response_bytes=_int("MAX_RESPONSE_BYTES", 262_144),
            max_sse_connections=_int("MAX_SSE_CONNECTIONS", 100),
            max_inflight_requests=_int("MAX_INFLIGHT_REQUESTS", 32),
            queue_max_depth=_int("QUEUE_MAX_DEPTH", 64),
            shutdown_drain_ms=_int("SHUTDOWN_DRAIN_MS", 10_000),
            egress_allowlist=_csv("EGRESS_ALLOWLIST"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.public_data_mode not in {"fixture", "live", "hybrid"}:
            raise ValueError("PUBLIC_DATA_MODE must be fixture, live, or hybrid")
        if self.llm_mode not in {"mock", "live"}:
            raise ValueError("LLM_MODE must be mock or live")
        if self.runtime_profile not in {"template", "hermes", "hybrid"}:
            raise ValueError("RUNTIME_PROFILE must be template, hermes, or hybrid")
        if self.hermes_lite_mode not in {"off", "embedded_spike", "sidecar"}:
            raise ValueError("HERMES_LITE_MODE must be off, embedded_spike, or sidecar")
        if self.app_env == "production" and "*" in self.allowed_origins:
            raise ValueError("wildcard CORS origin is forbidden in production")
        for origin in self.allowed_origins:
            if origin == "*":
                continue
            if not origin.startswith(("http://", "https://")):
                raise ValueError("ALLOWED_ORIGINS must contain absolute http(s) origins")
        if self.provider_max_retries < 0:
            raise ValueError("provider_max_retries must be non-negative")
        if self.provider_timeout_seconds <= 0 or self.llm_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if (
            self.cache_ttl_live_seconds < 0
            or self.cache_ttl_boxscore_seconds < 0
            or self.cache_ttl_history_seconds < 0
        ):
            raise ValueError("cache TTL values must be non-negative")
        if self.app_env == "production" and self.hermes_lite_mode == "embedded_spike":
            raise ValueError("embedded Hermes spike is forbidden in production")
        positive = {
            "provider_timeout_seconds": self.provider_timeout_seconds,
            "provider_max_response_bytes": self.provider_max_response_bytes,
            "request_deadline_ms": self.request_deadline_ms,
            "max_provider_operations": self.max_provider_operations,
            "session_ttl_seconds": self.session_ttl_seconds,
            "max_session_turns": self.max_session_turns,
            "max_session_bytes": self.max_session_bytes,
            "cache_max_entries": self.cache_max_entries,
            "max_request_bytes": self.max_request_bytes,
            "max_event_bytes": self.max_event_bytes,
            "max_response_bytes": self.max_response_bytes,
            "max_sse_connections": self.max_sse_connections,
            "max_inflight_requests": self.max_inflight_requests,
            "queue_max_depth": self.queue_max_depth,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"configuration values must be positive: {', '.join(invalid)}")
        if not self.espn_base_url:
            raise ValueError("ESPN_BASE_URL must not be empty")
        if not self.espn_allowed_hosts:
            raise ValueError("ESPN_ALLOWED_HOSTS must not be empty")


settings = Settings.from_env()
