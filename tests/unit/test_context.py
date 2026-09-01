"""Session isolation, optimistic versions, and deterministic expiry tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.api.src.application.context_manager import ContextManager
from apps.api.src.domain.models import (
    Category,
    ConversationContext,
    IntentName,
    Operation,
    QueryIntent,
    QueryMode,
)
from apps.api.src.infrastructure.session_store import (
    InMemorySessionStore,
    SessionConflictError,
)


class _MutableClock:
    def __init__(self, instant: datetime) -> None:
        self.instant = instant

    def now_utc(self) -> datetime:
        return self.instant


def _context(session_id, expires_at: datetime) -> ConversationContext:
    return ConversationContext(
        session_id=session_id,
        timezone="Asia/Shanghai",
        expires_at_utc=expires_at,
    )


@pytest.mark.asyncio
async def test_store_uses_injected_clock_for_expiry_and_resets_version() -> None:
    clock = _MutableClock(datetime(2026, 6, 12, 0, tzinfo=UTC))
    store = InMemorySessionStore(ttl_seconds=10, clock=clock)
    session_id = uuid4()
    context = _context(session_id, clock.instant + timedelta(seconds=10))

    saved = await store.save(session_id, context, expected_version=0)
    assert saved.version == 1
    assert await store.load(session_id) is not None

    clock.instant += timedelta(seconds=11)
    assert await store.load(session_id) is None

    # An expired record must not make a fresh version-0 save conflict.
    fresh = await store.save(
        session_id,
        _context(session_id, clock.instant + timedelta(seconds=10)),
        expected_version=0,
    )
    assert fresh.version == 1


@pytest.mark.asyncio
async def test_idempotency_is_atomic_and_session_scoped() -> None:
    store = InMemorySessionStore()
    session_a, session_b = uuid4(), uuid4()
    client_id = "message-1"

    reservations = await asyncio.gather(
        *(store.reserve_idempotency(session_a, client_id, uuid4()) for _ in range(8))
    )
    assert sum(owner for owner, _record in reservations) == 1

    result = {"status": "completed", "answer_markdown": "已核验"}
    await store.complete_idempotency(session_a, client_id, result)
    replay = await store.replay_or_wait(session_a, client_id, timeout=0.01)
    assert replay == result
    assert await store.replay_or_wait(session_b, client_id, timeout=0.01) is None


@pytest.mark.asyncio
async def test_optimistic_save_rejects_stale_context() -> None:
    store = InMemorySessionStore()
    session_id = uuid4()
    now = datetime.now(UTC)
    await store.save(session_id, _context(session_id, now + timedelta(days=1)), expected_version=0)

    with pytest.raises(SessionConflictError):
        await store.save(
            session_id,
            _context(session_id, now + timedelta(days=1)),
            expected_version=0,
        )


@pytest.mark.asyncio
async def test_accurate_turn_count_is_independent_from_bounded_summaries() -> None:
    store = InMemorySessionStore()
    manager = ContextManager(store, max_turns=8)
    session_id = uuid4()
    context = await manager.ensure(session_id)
    intent = QueryIntent(
        category=Category.H,
        intent_name=IntentName.FOLLOW_UP,
        mode=QueryMode.OBJECTIVE,
        confidence=1,
        operation=Operation.EXPLAIN,
    )

    for index in range(12):
        context = await manager.commit(
            context,
            intent=intent,
            answer=f"回答 {index + 1}",
            user_message=f"问题 {index + 1}",
        )

    assert context.completed_user_turn_count == 12
    assert context.turn_count == 8
    assert len(context.recent_turn_summaries) == 8
    assert [item.turn_index for item in context.recent_turn_summaries] == list(range(5, 13))
