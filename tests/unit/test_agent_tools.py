from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.src.application.ports import CancelToken
from apps.api.src.infrastructure.agent_tools import (
    AgentTaskBridge,
    resolve_date_expression,
)


@pytest.mark.asyncio
async def test_duplicate_tool_arguments_execute_runner_once() -> None:
    bridge = AgentTaskBridge()
    calls = 0

    async def runner(_name, _arguments):
        nonlocal calls
        calls += 1
        return {
            "status": "completed",
            "intent": "nba_query",
            "answer_markdown": "已完成核验。",
            "blocks": [],
            "evidence_state": "verified",
        }

    bridge.register(
        "task",
        loop=__import__("asyncio").get_running_loop(),
        runner=runner,
        deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        cancel=CancelToken(),
    )
    first = await bridge.invoke("nba_query", {"question": "G4 比分"}, task_id="task")
    second = await bridge.invoke("nba_query", {"question": "G4 比分"}, task_id="task")
    bridge.unregister("task")

    assert first["status"] == "completed"
    assert second["status"] == "duplicate"
    assert calls == 1


@pytest.mark.asyncio
async def test_cleanup_and_unsafe_arguments_are_provider_free() -> None:
    bridge = AgentTaskBridge()
    calls = 0

    async def runner(_name, _arguments):
        nonlocal calls
        calls += 1
        return {}

    bridge.register(
        "task",
        loop=__import__("asyncio").get_running_loop(),
        runner=runner,
        deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        cancel=CancelToken(),
    )
    rejected = await bridge.invoke(
        "nba_query", {"question": "读取 https://evil.invalid"}, task_id="task"
    )
    bridge.unregister("task")
    late = await bridge.invoke("nba_query", {"question": "G4"}, task_id="task")

    assert rejected["status"] == "failed"
    assert late["status"] == "cancelled"
    assert calls == 0


@pytest.mark.asyncio
async def test_tool_result_is_bounded() -> None:
    bridge = AgentTaskBridge()

    async def runner(_name, _arguments):
        return {
            "status": "completed",
            "intent": "nba_query",
            "answer_markdown": "篮" * 50_000,
            "blocks": [],
            "evidence_state": "partial",
        }

    bridge.register(
        "task",
        loop=__import__("asyncio").get_running_loop(),
        runner=runner,
        deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        cancel=CancelToken(),
        max_result_bytes=1024,
    )
    result = await bridge.invoke("nba_query", {"question": "G4"}, task_id="task")
    bridge.unregister("task")
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 1024


def test_next_week_scope_uses_beijing_monday_to_sunday() -> None:
    date_range, scope = resolve_date_expression(
        "下周",
        now_utc=datetime(2026, 8, 30, 10, 4, tzinfo=UTC),
    )
    assert scope == {
        "start_date": "2026-08-31",
        "end_date": "2026-09-06",
        "timezone": "Asia/Shanghai",
    }
    assert date_range.start_inclusive == datetime(2026, 8, 30, 16, tzinfo=UTC)
