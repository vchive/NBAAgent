"""Provider gateway: typed dispatch, TTL caching and bounded retry policy."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from apps.api.src.application.ports import ProviderPort, ProviderResult, RequestBudget
from apps.api.src.domain.errors import ProviderErrorKind
from apps.api.src.domain.models import Standing, canonical_conference
from apps.api.src.infrastructure.cache import InMemoryTTLCache


class ProviderGateway:
    def __init__(
        self,
        provider: ProviderPort,
        cache: InMemoryTTLCache | None = None,
        *,
        fallback: ProviderPort | None = None,
        fallback_provider: ProviderPort | None = None,
        max_retries: int = 2,
        default_ttl_seconds: int = 300,
    ) -> None:
        self.provider = provider
        if (
            fallback is not None
            and fallback_provider is not None
            and fallback is not fallback_provider
        ):
            raise ValueError("fallback and fallback_provider must refer to the same provider")
        # ``fallback_provider`` is an explicit alias used by configuration
        # callers; keeping both names avoids coupling them to one deployment
        # profile while retaining a single internal field.
        self.fallback = fallback if fallback is not None else fallback_provider
        self.cache = cache
        self.max_retries = max(0, max_retries)
        self.default_ttl_seconds = default_ttl_seconds
        self.call_count = 0
        self.fallback_call_count = 0
        self.cache_read_count = 0
        self.cache_write_count = 0
        self.cache_hit_count = 0

    @staticmethod
    def _key(operation: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        def normalize(value: Any) -> Any:
            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json")
            if isinstance(value, (list, tuple)):
                return [normalize(item) for item in value]
            if isinstance(value, dict):
                return {
                    str(key): normalize(item)
                    for key, item in sorted(value.items(), key=lambda item: str(item[0]))
                }
            return value

        payload = json.dumps(
            {
                "operation": operation,
                "args": normalize(args),
                "kwargs": normalize(kwargs),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return "provider:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _invoke(
        self,
        operation: str,
        *args: Any,
        ttl_seconds: int | None = None,
        budget: RequestBudget,
        **kwargs: Any,
    ) -> ProviderResult[Any]:
        # Admission/deadline checks happen before *any* cache read.  This keeps
        # rejected requests observable as true zero-downstream-call branches.
        if (
            budget.remaining_ms() <= 0
            or budget.provider_operations >= budget.max_provider_operations
        ):
            from apps.api.src.domain.errors import ProviderError

            return ProviderResult(
                data=None,
                evidence=[],
                partial=False,
                error=ProviderError(
                    kind=ProviderErrorKind.TIMEOUT,
                    retryable=True,
                    safe_message="request deadline exceeded",
                ),
                retrieved_at_utc=datetime.now(UTC),
            )
        key = self._key(operation, args, kwargs)
        if self.cache is not None:
            self.cache_read_count += 1
            cached = self.cache.get(key)
            self.cache_hit_count = self.cache.hit_count
            if isinstance(cached, ProviderResult):
                return cached
        method: Callable[..., Awaitable[ProviderResult[Any]]] = getattr(self.provider, operation)
        last: ProviderResult[Any] | None = None
        attempts = 0
        used_fallback = False
        while True:
            # Reserve centrally so providers which do not implement their own
            # budget check still cannot exceed the request operation cap.  A
            # built-in adapter may consume the one-shot hand-off via
            # ``reserve_operation`` without double-counting it.
            if hasattr(budget, "reserve_gateway_operation"):
                if not budget.reserve_gateway_operation():
                    from apps.api.src.domain.errors import ProviderError

                    last = ProviderResult(
                        data=None,
                        evidence=[],
                        partial=False,
                        error=ProviderError(
                            kind=ProviderErrorKind.TIMEOUT,
                            retryable=True,
                            safe_message="request deadline exceeded",
                        ),
                        retrieved_at_utc=datetime.now(UTC),
                    )
                    break
            self.call_count += 1
            try:
                result = await method(*args, budget=budget, **kwargs)
            except asyncio.CancelledError:
                if hasattr(budget, "clear_gateway_reservation"):
                    budget.clear_gateway_reservation()
                raise
            except TimeoutError:
                from apps.api.src.domain.errors import ProviderError

                result = ProviderResult(
                    data=None,
                    evidence=[],
                    partial=False,
                    error=ProviderError(
                        kind=ProviderErrorKind.TIMEOUT,
                        retryable=True,
                        safe_message="upstream timed out",
                    ),
                    retrieved_at_utc=datetime.now(UTC),
                )
            except (ValueError, TypeError, KeyError, IndexError):
                from apps.api.src.domain.errors import ProviderError

                # A provider that violates its typed result/schema contract is
                # not retryable: retrying the same malformed payload only
                # burns the caller's deadline and can hide a deployment bug.
                result = ProviderResult(
                    data=None,
                    evidence=[],
                    partial=False,
                    error=ProviderError(
                        kind=ProviderErrorKind.SCHEMA_MISMATCH,
                        retryable=False,
                        safe_message="upstream payload invalid",
                    ),
                    retrieved_at_utc=datetime.now(UTC),
                )
            except Exception:
                # Provider implementations must not leak stack traces or raw
                # response details through the application boundary.
                from apps.api.src.domain.errors import ProviderError

                result = ProviderResult(
                    data=None,
                    evidence=[],
                    partial=False,
                    error=ProviderError(
                        kind=ProviderErrorKind.HTTP,
                        retryable=True,
                        safe_message="upstream unavailable",
                    ),
                    retrieved_at_utc=datetime.now(UTC),
                )
            if not isinstance(result, ProviderResult):
                from apps.api.src.domain.errors import ProviderError

                result = ProviderResult(
                    data=None,
                    evidence=[],
                    partial=False,
                    error=ProviderError(
                        kind=ProviderErrorKind.SCHEMA_MISMATCH,
                        retryable=False,
                        safe_message="provider result invalid",
                    ),
                    retrieved_at_utc=datetime.now(UTC),
                )
            if hasattr(budget, "clear_gateway_reservation"):
                budget.clear_gateway_reservation()
            last = result
            if result.error is None:
                break
            if (
                not result.error.retryable
                or attempts >= self.max_retries
                or not budget.can_retry(attempts)
            ):
                break
            attempts += 1
            retry_after = result.error.retry_after_seconds
            exponential = 0.05 * (2 ** (attempts - 1))
            delay = exponential if retry_after is None else max(0.0, float(retry_after))
            # Never let a server hint hold an API worker beyond the request
            # deadline; the cap keeps fixture/contract retries fast while still
            # respecting a non-zero Retry-After signal.
            delay = min(delay, 0.2, budget.remaining_ms() / 1000)
            if delay > 0:
                await asyncio.sleep(delay)
        assert last is not None
        # Hybrid mode can provide a deterministic/local source while the live
        # source is unavailable.  Do this only after bounded retries and only
        # on a typed upstream error; an authoritative empty response is not
        # silently replaced by stale fixture data.
        if last.error is not None and self.fallback is not None:
            fallback_method = getattr(self.fallback, operation, None)
            if fallback_method is not None and budget.remaining_ms() > 0:
                try:
                    if (
                        hasattr(budget, "reserve_gateway_operation")
                        and not budget.reserve_gateway_operation()
                    ):
                        fallback_result = None
                    else:
                        self.fallback_call_count += 1
                        self.call_count += 1
                        try:
                            fallback_result = await fallback_method(*args, budget=budget, **kwargs)
                        finally:
                            if hasattr(budget, "clear_gateway_reservation"):
                                budget.clear_gateway_reservation()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    fallback_result = None
                if isinstance(fallback_result, ProviderResult) and fallback_result.error is None:
                    last = fallback_result
                    used_fallback = True
        if (
            last.error is None
            and last.data is not None
            and self.cache is not None
            and not used_fallback
        ):
            self.cache_write_count += 1
            self.cache.set(
                key,
                last,
                ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds,
            )
        return last

    async def search_games(self, filters: Any, budget: RequestBudget) -> ProviderResult[Any]:
        return await self._invoke("search_games", filters, budget=budget, ttl_seconds=45)

    async def get_game_summary(self, game_id: str, budget: RequestBudget) -> ProviderResult[Any]:
        return await self._invoke("get_game_summary", game_id, budget=budget, ttl_seconds=300)

    async def get_play_by_play(self, game_id: str, budget: RequestBudget) -> ProviderResult[Any]:
        return await self._invoke("get_play_by_play", game_id, budget=budget, ttl_seconds=45)

    async def get_player_stats(self, query: Any, budget: RequestBudget) -> ProviderResult[Any]:
        return await self._invoke("get_player_stats", query, budget=budget, ttl_seconds=300)

    async def get_team_stats(self, query: Any, budget: RequestBudget) -> ProviderResult[Any]:
        return await self._invoke("get_team_stats", query, budget=budget, ttl_seconds=300)

    async def get_standings(
        self,
        season: Any,
        budget: RequestBudget,
        *,
        conference: str | None = None,
    ) -> ProviderResult[Any]:
        """Read standings and apply an optional conference projection.

        The provider port intentionally remains season-only for compatibility
        with existing adapters. Filtering here keeps the cache/source result
        canonical and prevents an East/West rank question from leaking the
        other conference into the answer. Only canonical ``Standing`` rows are
        projected; malformed payloads are left untouched for normal schema
        verification to handle.
        """

        result = await self._invoke("get_standings", season, budget=budget, ttl_seconds=300)
        target = canonical_conference(conference)
        if target is None or result.error is not None:
            return result
        rows = result.data
        if not isinstance(rows, list) or not all(isinstance(row, Standing) for row in rows):
            return result
        filtered = [row for row in rows if canonical_conference(row.conference) == target]
        # ``model_copy`` preserves evidence, freshness and partial metadata;
        # only the typed data projection changes.
        return result.model_copy(update={"data": filtered})

    async def get_history(self, query: Any, budget: RequestBudget) -> ProviderResult[Any]:
        return await self._invoke("get_history", query, budget=budget, ttl_seconds=86_400)

    async def search_news(self, query: Any, budget: RequestBudget) -> ProviderResult[Any]:
        return await self._invoke("search_news", query, budget=budget, ttl_seconds=300)

    def counters(self) -> dict[str, int]:
        cache = self.cache.counters() if self.cache is not None else {}
        return {
            "provider_call_count": self.call_count,
            "fallback_call_count": self.fallback_call_count,
            "cache_read_count": self.cache_read_count,
            "cache_write_count": self.cache_write_count,
            "cache_hit_count": self.cache_hit_count,
            **cache,
        }


__all__ = ["ProviderGateway"]
