"""Typed, user-safe error vocabulary for the domain/application boundary."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import ErrorCode


class ProviderErrorKind(str, Enum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH = "AUTH"
    HTTP = "HTTP"
    INVALID_JSON = "INVALID_JSON"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    NOT_FOUND = "NOT_FOUND"


class RuntimeErrorKind(str, Enum):
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    UNSAFE = "UNSAFE"


class ProviderError(BaseModel):
    """Provider failure details kept behind the adapter boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ProviderErrorKind
    retryable: bool = False
    safe_message: str = Field(min_length=1, max_length=500)
    retry_after_seconds: int | None = Field(default=None, ge=0, le=3600)


class AgentError(Exception):
    """Base exception carrying a canonical internal error code.

    ``message`` is intended for internal logs; ``safe_message`` is suitable for
    a user-facing error envelope and must never include URLs, stack traces, or
    provider fields.
    """

    code: ErrorCode
    retryable: bool
    safe_message: str
    retry_after_seconds: int | None
    details: dict[str, Any]

    def __init__(
        self,
        code: ErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.details = details or {}


class InvalidPayloadError(AgentError):
    def __init__(self, safe_message: str = "请求格式不正确，请缩短问题或补充必要条件。") -> None:
        super().__init__(ErrorCode.INVALID_PAYLOAD, safe_message)


class SafetyBlockedError(AgentError):
    def __init__(
        self,
        safe_message: str = "这个话题不属于赛事助手的讨论范围。您可以问我比赛、球员或球队数据。",
    ) -> None:
        super().__init__(ErrorCode.SAFETY_BLOCKED, safe_message)


class AmbiguousEntityError(AgentError):
    def __init__(
        self, safe_message: str = "我找到了多个可能的对象，请补充球队、球员或日期。"
    ) -> None:
        super().__init__(ErrorCode.AMBIGUOUS_ENTITY, safe_message)


class MissingSlotError(AgentError):
    def __init__(self, safe_message: str = "请补充比赛、球员或时间范围等必要条件。") -> None:
        super().__init__(ErrorCode.MISSING_SLOT, safe_message)


class NoDataError(AgentError):
    def __init__(
        self, safe_message: str = "暂无匹配的公开数据，您可以调整日期或缩小查询范围。"
    ) -> None:
        super().__init__(ErrorCode.NO_DATA, safe_message)


class ServiceBusyError(AgentError):
    def __init__(
        self,
        safe_message: str = "当前请求较多，请稍后重试。",
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(
            ErrorCode.SERVICE_BUSY,
            safe_message,
            retryable=True,
            retry_after_seconds=retry_after_seconds,
        )


class UpstreamTimeoutError(AgentError):
    def __init__(self, safe_message: str = "数据暂时不可用，请稍后重试。") -> None:
        super().__init__(ErrorCode.UPSTREAM_TIMEOUT, safe_message, retryable=True)


class UpstreamRateLimitedError(AgentError):
    def __init__(
        self,
        safe_message: str = "数据服务暂时繁忙，请稍后重试。",
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(
            ErrorCode.UPSTREAM_RATE_LIMITED,
            safe_message,
            retryable=True,
            retry_after_seconds=retry_after_seconds,
        )


class UpstreamAuthError(AgentError):
    def __init__(self, safe_message: str = "数据服务暂时不可用，请稍后再试。") -> None:
        super().__init__(ErrorCode.UPSTREAM_AUTH, safe_message)


class InvalidUpstreamDataError(AgentError):
    def __init__(self, safe_message: str = "公开数据格式异常，暂时无法核验。") -> None:
        super().__init__(ErrorCode.INVALID_UPSTREAM_DATA, safe_message)


class ComposerUnavailableError(AgentError):
    def __init__(self, safe_message: str = "回答生成服务暂时不可用，请稍后重试。") -> None:
        super().__init__(ErrorCode.COMPOSER_UNAVAILABLE, safe_message, retryable=True)


class OutputBlockedError(AgentError):
    def __init__(self, safe_message: str = "回答未通过安全校验，请换一种问法。") -> None:
        super().__init__(ErrorCode.OUTPUT_BLOCKED, safe_message)


class RequestCancelledError(AgentError):
    """Internal cancellation marker; normally not serialized to the client."""

    def __init__(self, safe_message: str = "请求已取消。") -> None:
        super().__init__(ErrorCode.SERVICE_BUSY, safe_message, retryable=True)


class SessionConflictError(AgentError):
    """Optimistic session-version conflict; safe to retry once."""

    def __init__(self, safe_message: str = "会话正在更新，请稍后重试。") -> None:
        super().__init__(ErrorCode.SERVICE_BUSY, safe_message, retryable=True)


__all__ = [
    "AgentError",
    "AmbiguousEntityError",
    "ComposerUnavailableError",
    "ErrorCode",
    "InvalidPayloadError",
    "InvalidUpstreamDataError",
    "MissingSlotError",
    "NoDataError",
    "OutputBlockedError",
    "ProviderError",
    "ProviderErrorKind",
    "RequestCancelledError",
    "RuntimeErrorKind",
    "SafetyBlockedError",
    "ServiceBusyError",
    "SessionConflictError",
    "UpstreamAuthError",
    "UpstreamRateLimitedError",
    "UpstreamTimeoutError",
]
