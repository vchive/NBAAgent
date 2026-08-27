"""Bounded local admission controller."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from apps.api.src.domain.models import AdmissionResult


@dataclass(slots=True)
class AdmissionLease:
    controller: AdmissionController
    acquired_at: float
    released: bool = False

    async def release(self) -> None:
        if not self.released:
            self.released = True
            self.controller._semaphore.release()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.release()


class AdmissionController:
    def __init__(
        self, *, max_inflight: int = 32, queue_max_depth: int = 64, queue_wait_ms: int = 1000
    ) -> None:
        self.max_inflight = max(1, max_inflight)
        self.queue_max_depth = max(0, queue_max_depth)
        self.queue_wait_ms = max(1, queue_wait_ms)
        self._semaphore = asyncio.Semaphore(self.max_inflight)
        self._waiting = 0

    @property
    def waiting(self) -> int:
        return self._waiting

    async def acquire(
        self, *, timeout_ms: int | None = None
    ) -> tuple[AdmissionResult, AdmissionLease | None, int]:
        started = time.monotonic()
        if self._semaphore._value <= 0 and self._waiting >= self.queue_max_depth:
            return AdmissionResult.QUEUE_FULL, None, 0
        self._waiting += 1
        try:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(), timeout=(timeout_ms or self.queue_wait_ms) / 1000
                )
            except asyncio.TimeoutError:
                return AdmissionResult.RATE_LIMITED, None, int((time.monotonic() - started) * 1000)
            return (
                AdmissionResult.ADMITTED,
                AdmissionLease(self, time.monotonic()),
                int((time.monotonic() - started) * 1000),
            )
        finally:
            self._waiting = max(0, self._waiting - 1)


__all__ = ["AdmissionController", "AdmissionLease"]
