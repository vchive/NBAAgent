"""Small, dependency-free application configuration.

The service intentionally reads environment variables through one typed object so tests can
inject a deterministic fixture profile without importing provider or model implementations.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse


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


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = "local"
    public_data_mode: str = "fixture"
    # Optional fixed day for a reproducible fixture demo. Live/hybrid data
    # modes always use the service's actual local date.
    highlights_demo_date: str = ""
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
    # Optional, bounded web-search augmentation.  Fixture mode remains
    # offline even if these flags are accidentally present.
    ddg_search_enabled: bool = False
    ddg_timeout_seconds: float = 3.0
    ddg_max_results: int = 5
    ddg_max_response_bytes: int = 512_000
    ddg_cache_ttl_seconds: int = 300
    full_intelligence_enabled: bool = False
    default_intelligence_mode: str = "hybrid"
    llm_mode: str = "mock"
    llm_timeout_seconds: float = 8.0
    siliconflow_api_key: str = field(default="", repr=False)
    siliconflow_api_key_file: str = field(default="", repr=False)
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_model: str = "deepseek-ai/DeepSeek-V4-Flash"
    siliconflow_max_tokens: int = 800
    siliconflow_max_response_bytes: int = 262_144
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
    agent_max_iterations: int = 4
    agent_max_tool_calls: int = 4
    agent_tool_timeout_ms: int = 8_000
    agent_max_tool_result_bytes: int = 16_384
    agent_max_output_bytes: int = 20_000
    agent_package_version: str = "0.19.0"
    # This bounded three-tool workflow does not need a hidden reasoning pass
    # by default. Make the policy explicit so a provider default cannot spend
    # most of the request deadline before returning a tool decision.
    agent_reasoning_effort: str = "none"
    max_request_bytes: int = 32_768
    max_event_bytes: int = 16_384
    max_response_bytes: int = 262_144
    max_sse_connections: int = 100
    max_inflight_requests: int = 32
    queue_max_depth: int = 64
    shutdown_drain_ms: int = 10_000
    egress_allowlist: tuple[str, ...] = field(default_factory=tuple)
    # A single shared password is enough for the interview/demo deployment.
    # Prefer the secret file in Docker; the environment value is retained for
    # local development and tests only.  Passwords are hidden from repr/logs.
    auth_required: bool = False
    app_password: str = field(default="", repr=False)
    app_password_file: str = field(default="", repr=False)
    auth_cookie_name: str = "nba_session"
    auth_cookie_secure: bool = False
    auth_session_ttl_seconds: int = 86_400
    auth_max_failed_attempts: int = 8
    auth_lockout_seconds: int = 60

    @classmethod
    def from_env(cls) -> Settings:
        settings = cls(
            app_env=os.getenv("APP_ENV", "local").lower(),
            public_data_mode=os.getenv("PUBLIC_DATA_MODE", "fixture").lower(),
            highlights_demo_date=os.getenv("HIGHLIGHTS_DEMO_DATE", "").strip(),
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
            ddg_search_enabled=_bool("DDG_SEARCH_ENABLED", False),
            ddg_timeout_seconds=_float("DDG_TIMEOUT_SECONDS", 3.0),
            ddg_max_results=_int("DDG_MAX_RESULTS", 5),
            ddg_max_response_bytes=_int("DDG_MAX_RESPONSE_BYTES", 512_000),
            ddg_cache_ttl_seconds=_int("DDG_CACHE_TTL_SECONDS", 300),
            full_intelligence_enabled=_bool("FULL_INTELLIGENCE_ENABLED", False),
            default_intelligence_mode=os.getenv("DEFAULT_INTELLIGENCE_MODE", "hybrid").lower(),
            llm_mode=os.getenv("LLM_MODE", "mock").lower(),
            llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 8.0),
            siliconflow_api_key=os.getenv("SILICONFLOW_API_KEY", "").strip(),
            siliconflow_api_key_file=os.getenv("SILICONFLOW_API_KEY_FILE", "").strip(),
            siliconflow_base_url=os.getenv(
                "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
            ).strip(),
            siliconflow_model=os.getenv(
                "SILICONFLOW_MODEL", "deepseek-ai/DeepSeek-V4-Flash"
            ).strip(),
            siliconflow_max_tokens=_int("SILICONFLOW_MAX_TOKENS", 800),
            siliconflow_max_response_bytes=_int(
                "SILICONFLOW_MAX_RESPONSE_BYTES", 262_144
            ),
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
            agent_max_iterations=_int("AGENT_MAX_ITERATIONS", 4),
            agent_max_tool_calls=_int("AGENT_MAX_TOOL_CALLS", 4),
            agent_tool_timeout_ms=_int("AGENT_TOOL_TIMEOUT_MS", 8_000),
            agent_max_tool_result_bytes=_int("AGENT_MAX_TOOL_RESULT_BYTES", 16_384),
            agent_max_output_bytes=_int("AGENT_MAX_OUTPUT_BYTES", 20_000),
            agent_package_version=os.getenv("AGENT_PACKAGE_VERSION", "0.19.0").strip(),
            agent_reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "none").lower(),
            max_request_bytes=_int("MAX_REQUEST_BYTES", 32_768),
            max_event_bytes=_int("MAX_EVENT_BYTES", 16_384),
            max_response_bytes=_int("MAX_RESPONSE_BYTES", 262_144),
            max_sse_connections=_int("MAX_SSE_CONNECTIONS", 100),
            max_inflight_requests=_int("MAX_INFLIGHT_REQUESTS", 32),
            queue_max_depth=_int("QUEUE_MAX_DEPTH", 64),
            shutdown_drain_ms=_int("SHUTDOWN_DRAIN_MS", 10_000),
            egress_allowlist=_csv("EGRESS_ALLOWLIST"),
            auth_required=_bool("AUTH_REQUIRED", False),
            app_password=os.getenv("APP_PASSWORD", ""),
            app_password_file=os.getenv("APP_PASSWORD_FILE", "").strip(),
            auth_cookie_name=os.getenv("AUTH_COOKIE_NAME", "nba_session").strip(),
            auth_cookie_secure=_bool("AUTH_COOKIE_SECURE", False),
            auth_session_ttl_seconds=_int("AUTH_SESSION_TTL_SECONDS", 86_400),
            auth_max_failed_attempts=_int("AUTH_MAX_FAILED_ATTEMPTS", 8),
            auth_lockout_seconds=_int("AUTH_LOCKOUT_SECONDS", 60),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        app_env = str(self.app_env).lower()
        public_data_mode = str(self.public_data_mode).lower()
        llm_mode = str(self.llm_mode).lower()
        runtime_profile = str(self.runtime_profile).lower()
        hermes_lite_mode = str(self.hermes_lite_mode).lower()
        default_intelligence_mode = str(self.default_intelligence_mode).lower()
        if public_data_mode not in {"fixture", "live", "hybrid"}:
            raise ValueError("PUBLIC_DATA_MODE must be fixture, live, or hybrid")
        if self.highlights_demo_date:
            try:
                date.fromisoformat(self.highlights_demo_date)
            except (TypeError, ValueError) as exc:
                raise ValueError("HIGHLIGHTS_DEMO_DATE must use YYYY-MM-DD") from exc
        if llm_mode not in {"mock", "live"}:
            raise ValueError("LLM_MODE must be mock or live")
        if runtime_profile not in {"template", "hermes", "hybrid"}:
            raise ValueError("RUNTIME_PROFILE must be template, hermes, or hybrid")
        if hermes_lite_mode not in {"off", "embedded_spike", "embedded_agent", "sidecar"}:
            raise ValueError(
                "HERMES_LITE_MODE must be off, embedded_spike, embedded_agent, or sidecar"
            )
        if default_intelligence_mode not in {"hybrid", "full"}:
            raise ValueError("DEFAULT_INTELLIGENCE_MODE must be hybrid or full")
        if default_intelligence_mode == "full" and not self.full_intelligence_enabled:
            raise ValueError("full default intelligence requires FULL_INTELLIGENCE_ENABLED=true")
        if llm_mode == "live" and hermes_lite_mode == "off":
            raise ValueError("live LLM calls require HERMES_LITE_MODE to be enabled")
        if llm_mode == "live" and runtime_profile not in {"hermes", "hybrid"}:
            raise ValueError("live LLM calls require RUNTIME_PROFILE=hermes or hybrid")
        if app_env == "production" and "*" in self.allowed_origins:
            raise ValueError("wildcard CORS origin is forbidden in production")
        for origin in self.allowed_origins:
            if origin == "*":
                continue
            if not origin.startswith(("http://", "https://")):
                raise ValueError("ALLOWED_ORIGINS must contain absolute http(s) origins")
        if self.provider_max_retries < 0:
            raise ValueError("provider_max_retries must be non-negative")
        if (
            not math.isfinite(self.provider_timeout_seconds)
            or not math.isfinite(self.llm_timeout_seconds)
            or self.provider_timeout_seconds <= 0
            or self.llm_timeout_seconds <= 0
        ):
            raise ValueError("timeouts must be positive")
        if (
            self.cache_ttl_live_seconds < 0
            or self.cache_ttl_boxscore_seconds < 0
            or self.cache_ttl_history_seconds < 0
        ):
            raise ValueError("cache TTL values must be non-negative")
        if app_env == "production" and hermes_lite_mode in {"embedded_spike", "embedded_agent"}:
            raise ValueError("embedded Hermes runtimes are forbidden in production")
        positive = {
            "provider_timeout_seconds": self.provider_timeout_seconds,
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "provider_max_response_bytes": self.provider_max_response_bytes,
            "request_deadline_ms": self.request_deadline_ms,
            "max_provider_operations": self.max_provider_operations,
            "session_ttl_seconds": self.session_ttl_seconds,
            "max_session_turns": self.max_session_turns,
            "max_session_bytes": self.max_session_bytes,
            "cache_max_entries": self.cache_max_entries,
            "ddg_timeout_seconds": self.ddg_timeout_seconds,
            "ddg_max_results": self.ddg_max_results,
            "ddg_max_response_bytes": self.ddg_max_response_bytes,
            "ddg_cache_ttl_seconds": self.ddg_cache_ttl_seconds,
            "max_request_bytes": self.max_request_bytes,
            "max_event_bytes": self.max_event_bytes,
            "max_response_bytes": self.max_response_bytes,
            "max_sse_connections": self.max_sse_connections,
            "max_inflight_requests": self.max_inflight_requests,
            "queue_max_depth": self.queue_max_depth,
            "siliconflow_max_tokens": self.siliconflow_max_tokens,
            "siliconflow_max_response_bytes": self.siliconflow_max_response_bytes,
            "hermes_lite_max_tokens": self.hermes_lite_max_tokens,
            "hermes_lite_timeout_ms": self.hermes_lite_timeout_ms,
            "agent_tool_timeout_ms": self.agent_tool_timeout_ms,
            "agent_max_tool_result_bytes": self.agent_max_tool_result_bytes,
            "agent_max_output_bytes": self.agent_max_output_bytes,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"configuration values must be positive: {', '.join(invalid)}")
        if not 1 <= self.agent_max_iterations <= 4:
            raise ValueError("AGENT_MAX_ITERATIONS must be between 1 and 4")
        if not 1 <= self.agent_max_tool_calls <= 4:
            raise ValueError("AGENT_MAX_TOOL_CALLS must be between 1 and 4")
        if self.agent_max_tool_result_bytes > 65_536:
            raise ValueError("AGENT_MAX_TOOL_RESULT_BYTES must be <= 65536")
        if self.agent_max_output_bytes > 65_536:
            raise ValueError("AGENT_MAX_OUTPUT_BYTES must be <= 65536")
        if self.agent_package_version != "0.19.0":
            raise ValueError("AGENT_PACKAGE_VERSION must match the locked Hermes version 0.19.0")
        if self.agent_reasoning_effort not in {"none", "minimal", "low", "medium", "high"}:
            raise ValueError(
                "AGENT_REASONING_EFFORT must be none, minimal, low, medium, or high"
            )
        if not self.espn_base_url:
            raise ValueError("ESPN_BASE_URL must not be empty")
        if not self.espn_allowed_hosts:
            raise ValueError("ESPN_ALLOWED_HOSTS must not be empty")
        if not isinstance(self.siliconflow_base_url, str):
            raise ValueError("SILICONFLOW_BASE_URL must be a string")
        if not isinstance(self.siliconflow_model, str):
            raise ValueError("SILICONFLOW_MODEL must be a string")
        if not isinstance(self.siliconflow_api_key, str):
            raise ValueError("SILICONFLOW_API_KEY must be a string")
        if not isinstance(self.siliconflow_api_key_file, str):
            raise ValueError("SILICONFLOW_API_KEY_FILE must be a string")
        if not isinstance(self.app_password, str) or not isinstance(self.app_password_file, str):
            raise ValueError("APP_PASSWORD and APP_PASSWORD_FILE must be strings")
        if self.app_password and (
            len(self.app_password) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in self.app_password)
        ):
            raise ValueError("APP_PASSWORD must be a text value of <= 512 characters")
        if self.app_password_file and any(
            ord(char) < 32 or ord(char) == 127 for char in self.app_password_file
        ):
            raise ValueError("APP_PASSWORD_FILE contains control characters")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.auth_cookie_name or ""):
            raise ValueError("AUTH_COOKIE_NAME must contain only letters, numbers, '_' or '-'")
        if self.auth_session_ttl_seconds <= 0:
            raise ValueError("AUTH_SESSION_TTL_SECONDS must be positive")
        if self.auth_max_failed_attempts <= 0 or self.auth_lockout_seconds <= 0:
            raise ValueError("authentication rate-limit values must be positive")
        direct_model_enabled = llm_mode == "live" and hermes_lite_mode in {
            "embedded_spike",
            "embedded_agent",
        }
        # The fixed SiliconFlow allow-list applies only to the in-process
        # embedded spike.  A future isolated sidecar owns its own endpoint;
        # keeping that setting opaque here avoids coupling sidecar startup to
        # direct-provider configuration.
        if direct_model_enabled:
            parsed_siliconflow = urlparse(self.siliconflow_base_url)
            try:
                siliconflow_port = parsed_siliconflow.port
            except ValueError:
                siliconflow_port = -1
            if (
                parsed_siliconflow.scheme != "https"
                or (parsed_siliconflow.hostname or "").lower() != "api.siliconflow.cn"
                or siliconflow_port is not None
                or parsed_siliconflow.username
                or parsed_siliconflow.password
                or parsed_siliconflow.query
                or parsed_siliconflow.fragment
                or parsed_siliconflow.path.rstrip("/") != "/v1"
            ):
                raise ValueError("SILICONFLOW_BASE_URL must be https://api.siliconflow.cn/v1")
        if not 1 <= len(self.siliconflow_model) <= 200:
            raise ValueError("SILICONFLOW_MODEL must contain 1..200 characters")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.siliconflow_model):
            raise ValueError("SILICONFLOW_MODEL contains control characters")
        if self.siliconflow_api_key and (
            len(self.siliconflow_api_key) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in self.siliconflow_api_key)
            or any(char.isspace() for char in self.siliconflow_api_key)
        ):
            raise ValueError("SILICONFLOW_API_KEY must be a single token")
        if self.siliconflow_api_key_file and any(
            ord(char) < 32 or ord(char) == 127 for char in self.siliconflow_api_key_file
        ):
            raise ValueError("SILICONFLOW_API_KEY_FILE contains control characters")
        if self.siliconflow_max_tokens > 4096:
            raise ValueError("SILICONFLOW_MAX_TOKENS must be <= 4096")
        if self.siliconflow_max_response_bytes > 1_048_576:
            raise ValueError("SILICONFLOW_MAX_RESPONSE_BYTES must be <= 1048576")
        if self.ddg_max_results > 5:
            raise ValueError("DDG_MAX_RESULTS must be <= 5")
        if self.ddg_max_response_bytes > 1_048_576:
            raise ValueError("DDG_MAX_RESPONSE_BYTES must be <= 1048576")


settings = Settings.from_env()
