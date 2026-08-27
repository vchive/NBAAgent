"""Bounded in-memory session and idempotency store for the local/fixture profile."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class IdempotencyRecord:
    request_id: UUID
    # A client message id is only idempotent for the same logical payload.
    # Keeping a one-way digest prevents accidental key reuse from silently
    # replaying an unrelated answer while avoiding storage of user text.
    message_hash: str | None = None
    state: str = "in_flight"
    result: Any = None
    done: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(slots=True)
class StoredContext:
    value: Any
    version: int = 0
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(days=1))


class SessionConflictError(RuntimeError):
    """Raised when an optimistic context version no longer matches."""


class InMemorySessionStore:
    """A process-local store with per-session locks and idempotency replay.

    The in-memory implementation is deliberately replaceable.  It is sufficient for the
    interview/demo profile and makes the isolation invariants observable in integration tests.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 86_400,
        max_turns: int = 8,
        clock: Any | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns
        self.clock = clock
        self._contexts: dict[UUID, StoredContext] = {}
        self._idempotency: dict[tuple[UUID, str], IdempotencyRecord] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    def _now(self) -> datetime:
        """Return the store clock as an aware UTC instant.

        Tests and deterministic evaluation inject the same clock used by the
        application.  Production keeps the system clock when no clock is
        supplied.  Supporting callable clocks as well as ``now_utc``/``now``
        methods keeps this small store compatible with the domain clock ports.
        """

        if self.clock is None:
            return datetime.now(UTC)
        if callable(self.clock):
            value = self.clock()
        elif hasattr(self.clock, "now_utc"):
            value = self.clock.now_utc()
        elif hasattr(self.clock, "now"):
            value = self.clock.now()
        else:
            raise TypeError("clock must be callable or expose now_utc()")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return an aware timestamp")
        return value.astimezone(UTC)

    async def _lock_for(self, session_id: UUID) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(session_id, asyncio.Lock())

    async def load(self, session_id: UUID) -> Any | None:
        item = self._contexts.get(session_id)
        if item is None:
            return None
        if item.expires_at <= self._now():
            self._contexts.pop(session_id, None)
            return None
        return copy.deepcopy(item.value)

    async def save(
        self, session_id: UUID, context: Any, *, expected_version: int | None = None
    ) -> Any:
        lock = await self._lock_for(session_id)
        async with lock:
            current = self._contexts.get(session_id)
            if current is not None and current.expires_at <= self._now():
                # An expired context starts a fresh optimistic-version chain;
                # otherwise a caller with the normal expected_version=0 would
                # incorrectly receive a conflict against stale state.
                self._contexts.pop(session_id, None)
                current = None
            current_version = current.version if current else 0
            if expected_version is not None and current_version != expected_version:
                raise SessionConflictError(
                    f"session {session_id} version changed "
                    f"(expected {expected_version}, got {current_version})"
                )
            next_version = current_version + 1
            # Pydantic models and dataclasses both support a copy-like update; keep this store
            # agnostic and let the context manager set the canonical version field.
            stored = copy.deepcopy(context)
            if hasattr(stored, "version"):
                try:
                    setattr(stored, "version", next_version)
                except (AttributeError, TypeError):
                    pass
            self._contexts[session_id] = StoredContext(
                value=stored,
                version=next_version,
                expires_at=self._now() + timedelta(seconds=self.ttl_seconds),
            )
            return copy.deepcopy(stored)

    async def reserve_idempotency(
        self,
        session_id: UUID,
        client_message_id: str,
        request_id: UUID,
        *,
        message_hash: str | None = None,
    ) -> tuple[bool, IdempotencyRecord]:
        key = (session_id, client_message_id)
        async with self._guard:
            existing = self._idempotency.get(key)
            if existing is not None:
                return False, existing
            record = IdempotencyRecord(
                request_id=request_id,
                message_hash=message_hash,
            )
            self._idempotency[key] = record
            return True, record

    async def complete_idempotency(
        self, session_id: UUID, client_message_id: str, result: Any
    ) -> None:
        key = (session_id, client_message_id)
        async with self._guard:
            record = self._idempotency.get(key)
            if record is None:
                return
            record.state = "completed"
            record.result = copy.deepcopy(result)
            record.done.set()

    async def fail_idempotency(self, session_id: UUID, client_message_id: str) -> None:
        key = (session_id, client_message_id)
        async with self._guard:
            record = self._idempotency.pop(key, None)
            if record is not None:
                record.state = "failed"
                record.done.set()

    async def replay_or_wait(
        self, session_id: UUID, client_message_id: str, *, timeout: float = 10.0
    ) -> Any | None:
        async with self._guard:
            record = self._idempotency.get((session_id, client_message_id))
        if record is None:
            return None
        if record.state == "completed":
            return copy.deepcopy(record.result)
        try:
            await asyncio.wait_for(record.done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return copy.deepcopy(record.result) if record.state == "completed" else None

    async def purge_expired(self) -> None:
        now = self._now()
        for session_id, item in list(self._contexts.items()):
            if item.expires_at <= now:
                self._contexts.pop(session_id, None)

    @staticmethod
    def hash_session(session_id: UUID) -> str:
        return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:16]


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
