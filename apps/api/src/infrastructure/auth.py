"""Small shared-password authentication boundary for the demo deployment.

The first release intentionally uses one operator-provided password rather than a
user database.  Successful logins receive an opaque, short-lived HttpOnly cookie;
the password itself is never retained in a response, log message, or browser
storage.  The in-memory session table is suitable for the single-process demo and
fails closed when authentication is required but no password has been configured.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LoginResult:
    success: bool
    rate_limited: bool = False
    token: str | None = None


class AuthManager:
    """Authenticate one configured password and keep opaque browser sessions."""

    def __init__(
        self,
        *,
        password: str = "",
        password_file: str = "",
        required: bool = False,
        session_ttl_seconds: int = 86_400,
        max_failed_attempts: int = 8,
        lockout_seconds: int = 60,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.required = bool(required)
        self.session_ttl_seconds = max(60, int(session_ttl_seconds))
        self.max_failed_attempts = max(1, int(max_failed_attempts))
        self.lockout_seconds = max(1, int(lockout_seconds))
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._sessions: dict[str, float] = {}
        self._failures: dict[str, tuple[int, float]] = {}
        self._password = self._resolve_password(password, password_file)

    @staticmethod
    def _resolve_password(password: str, password_file: str) -> str:
        value = str(password or "")
        if not value and password_file:
            try:
                # Only remove the line ending introduced by a secret file. Do
                # not strip spaces that may intentionally be part of a password.
                value = open(password_file, encoding="utf-8").read().rstrip("\r\n")
            except (OSError, UnicodeError):
                value = ""
        if len(value) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            return ""
        return value

    @property
    def enabled(self) -> bool:
        return self.required or bool(self._password)

    @property
    def configured(self) -> bool:
        return bool(self._password)

    def status(self, token: str | None = None) -> dict[str, bool]:
        return {
            "enabled": self.enabled,
            "authenticated": True if not self.enabled else self.is_authenticated(token),
        }

    def is_authenticated(self, token: str | None) -> bool:
        if not self.enabled or not token:
            return not self.enabled
        now = float(self._clock())
        token_hash = self._hash_token(token)
        with self._lock:
            expires_at = self._sessions.get(token_hash)
            if expires_at is None:
                return False
            if expires_at <= now:
                self._sessions.pop(token_hash, None)
                return False
            return True

    def login(self, submitted_password: str, client_ip: str) -> LoginResult:
        if not self.enabled:
            return LoginResult(success=True)
        now = float(self._clock())
        ip = client_ip or "unknown"
        with self._lock:
            attempts, blocked_until = self._failures.get(ip, (0, 0.0))
            if blocked_until > now:
                return LoginResult(success=False, rate_limited=True)
            if blocked_until:
                attempts = 0
                self._failures.pop(ip, None)
            # Constant-time comparison prevents a trivial timing oracle.  The
            # type/length checks also keep malformed JSON from reaching it.
            candidate = submitted_password if isinstance(submitted_password, str) else ""
            valid = bool(self._password) and hmac.compare_digest(self._password, candidate)
            if not valid:
                attempts += 1
                if attempts >= self.max_failed_attempts:
                    self._failures[ip] = (attempts, now + self.lockout_seconds)
                    return LoginResult(success=False, rate_limited=True)
                self._failures[ip] = (attempts, 0.0)
                return LoginResult(success=False)
            self._failures.pop(ip, None)
            token = secrets.token_urlsafe(32)
            self._sessions[self._hash_token(token)] = now + self.session_ttl_seconds
            self._purge_expired(now)
            return LoginResult(success=True, token=token)

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(self._hash_token(token), None)

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, value in self._sessions.items() if value <= now]
        for key in expired:
            self._sessions.pop(key, None)


__all__ = ["AuthManager", "LoginResult"]
