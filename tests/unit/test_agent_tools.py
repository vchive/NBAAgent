from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.src.application.ports import CancelToken
from apps.api.src.infrastructure.agent_tools import (
    TOOL_SCHEMAS,
    AgentTaskBridge,
    resolve_date_expression,
    sanitise_observation,
)


def test_search_tool_requires_narrative_evidence_for_explicit_game_recap() -> None:
    description = str(TOOL_SCHEMAS["nba_search"]["description"])

    assert "明确比赛的过程或胜因" in description
    assert "必须在最终回答前调用本工具" in description


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


@pytest.mark.asyncio
async def test_typed_tool_failure_stays_internal_to_bridge() -> None:
    bridge = AgentTaskBridge()

    async def runner(_name, _arguments):
        return {
            "status": "failed",
            "intent": "web_search",
            "answer_markdown": "公开资料检索暂时不可用。",
            "blocks": [],
            "evidence_state": "none",
            "_error_code": "UPSTREAM_RATE_LIMITED",
            "_retryable": False,
        }

    bridge.register(
        "task",
        loop=__import__("asyncio").get_running_loop(),
        runner=runner,
        deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        cancel=CancelToken(),
    )
    visible = await bridge.invoke(
        "nba_search", {"query": "NBA 总决赛"}, task_id="task"
    )
    calls, _ = bridge.unregister("task")

    assert "_error_code" not in visible
    assert "_retryable" not in visible
    assert calls[0].error_code == "UPSTREAM_RATE_LIMITED"
    assert calls[0].retryable is False


@pytest.mark.asyncio
async def test_completed_tool_keeps_quota_issue_internal_for_notice() -> None:
    bridge = AgentTaskBridge()

    async def runner(_name, _arguments):
        return {
            "status": "completed",
            "intent": "web_search",
            "answer_markdown": "已从备用公开资料取得相关内容。",
            "blocks": [],
            "evidence_state": "partial",
            "_capability_issues": [
                {"kind": "QUOTA_EXHAUSTED", "retryable": False}
            ],
        }

    bridge.register(
        "task",
        loop=__import__("asyncio").get_running_loop(),
        runner=runner,
        deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        cancel=CancelToken(),
    )
    visible = await bridge.invoke(
        "nba_search", {"query": "NBA 总决赛"}, task_id="task"
    )
    calls, _ = bridge.unregister("task")

    assert visible["status"] == "completed"
    assert "_capability_issues" not in visible
    assert calls[0].status == "completed"
    assert calls[0].error_code == "SEARCH_QUOTA_EXHAUSTED"
    assert calls[0].retryable is False


@pytest.mark.asyncio
async def test_completed_nested_query_keeps_public_quota_notice_internal() -> None:
    """A successful nested answer must not discard its request-local notice."""

    bridge = AgentTaskBridge()

    async def runner(_name, _arguments):
        return {
            "status": "completed",
            "intent": "nba_query",
            "answer_markdown": "尼克斯以 94–90 取胜。",
            "blocks": [],
            "evidence_state": "verified",
            "_public_notices": [
                {
                    "code": "SEARCH_QUOTA_EXHAUSTED",
                    "message": "provider-specific text must stay internal",
                    "retryable": False,
                }
            ],
        }

    bridge.register(
        "task",
        loop=__import__("asyncio").get_running_loop(),
        runner=runner,
        deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        cancel=CancelToken(),
    )
    visible = await bridge.invoke(
        "nba_query", {"question": "这场谁赢了"}, task_id="task"
    )
    calls, _ = bridge.unregister("task")

    assert visible["status"] == "completed"
    assert "_public_notices" not in visible
    assert "provider-specific" not in json.dumps(visible, ensure_ascii=False)
    assert calls[0].status == "completed"
    assert calls[0].error_code == "SEARCH_QUOTA_EXHAUSTED"
    assert calls[0].retryable is False


@pytest.mark.asyncio
async def test_nested_query_rejects_unknown_public_notice_code() -> None:
    bridge = AgentTaskBridge()

    async def runner(_name, _arguments):
        return {
            "status": "completed",
            "intent": "nba_query",
            "answer_markdown": "已完成查询。",
            "blocks": [],
            "evidence_state": "verified",
            "_public_notices": [
                {
                    "code": "INTERNAL_PROVIDER_BILLING_DETAIL",
                    "retryable": False,
                }
            ],
        }

    bridge.register(
        "task",
        loop=__import__("asyncio").get_running_loop(),
        runner=runner,
        deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        cancel=CancelToken(),
    )
    visible = await bridge.invoke(
        "nba_query", {"question": "这场谁赢了"}, task_id="task"
    )
    calls, _ = bridge.unregister("task")

    assert "_public_notices" not in visible
    assert calls[0].error_code is None


def test_external_source_names_are_removed_from_tool_observation() -> None:
    observation = sanitise_observation(
        {
            "status": "completed",
            "intent": "web_search",
            "answer_markdown": "ESPN 与 Sportsradar provider 的公开资料摘要。",
            "evidence_state": "partial",
        },
        max_bytes=4096,
    )
    text = observation["answer_markdown"]
    assert "ESPN" not in text
    assert "Sportsradar" not in text
    assert "provider" not in text.lower()
    assert "公开资料" in text


def test_tool_observation_preserves_markdown_line_breaks() -> None:
    observation = sanitise_observation(
        {
            "status": "completed",
            "intent": "web_search",
            "answer_markdown": "相关线索：\r\n- 第一条\n- 第二条\t含制表符",
            "evidence_state": "partial",
        },
        max_bytes=4096,
    )

    assert observation["answer_markdown"] == "相关线索：\n- 第一条\n- 第二条 含制表符"


@pytest.mark.parametrize(
    "coverage",
    [
        "complete",
        "requested_detail_missing",
        "series_candidates_ready",
        "server_typed_game_grounding",
        "server_typed_pbp_grounding",
    ],
)
def test_tool_observation_preserves_server_owned_coverage(coverage: str) -> None:
    observation = sanitise_observation(
        {
            "status": "completed",
            "intent": "nba_query",
            "answer_markdown": "已取得本轮所需的有界事实。",
            "evidence_state": "verified",
            "coverage": coverage,
        },
        max_bytes=4096,
    )

    assert observation["coverage"] == coverage


def test_tool_observation_drops_unknown_coverage() -> None:
    observation = sanitise_observation(
        {
            "status": "completed",
            "intent": "nba_query",
            "answer_markdown": "已取得本轮所需的有界事实。",
            "evidence_state": "verified",
            "coverage": "model_claimed_complete",
        },
        max_bytes=4096,
    )

    assert "coverage" not in observation


@pytest.mark.parametrize("active_game_number", [1, 4, 7])
def test_tool_observation_preserves_bounded_active_game_number(
    active_game_number: int,
) -> None:
    observation = sanitise_observation(
        {
            "status": "completed",
            "intent": "nba_query",
            "query_scope": {
                "active_game_number": active_game_number,
                "game_id": "hupu:168858",
            },
            "answer_markdown": "本轮可供比较的比赛。",
            "evidence_state": "verified",
        },
        max_bytes=4096,
    )

    assert observation["query_scope"] == {
        "active_game_number": active_game_number
    }
    assert "game_id" not in observation["query_scope"]


@pytest.mark.parametrize("active_game_number", [True, 0, 8, "4", None])
def test_tool_observation_drops_invalid_active_game_number(
    active_game_number,
) -> None:
    observation = sanitise_observation(
        {
            "status": "completed",
            "intent": "nba_query",
            "query_scope": {"active_game_number": active_game_number},
            "answer_markdown": "本轮可供比较的比赛。",
            "evidence_state": "verified",
        },
        max_bytes=4096,
    )

    assert observation["query_scope"] is None


@pytest.mark.asyncio
async def test_resolved_game_id_is_server_side_only() -> None:
    bridge = AgentTaskBridge()

    async def runner(_name, _arguments):
        return {
            "status": "completed",
            "intent": "nba_query",
            "query_scope": {
                "game_id": "hupu:168858",
                "active_game_number": 4,
            },
            "answer_markdown": "尼克斯以 107–106 取胜。",
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
    visible = await bridge.invoke(
        "nba_query", {"question": "这场谁赢了"}, task_id="task"
    )
    _, stored = bridge.unregister("task")

    assert visible["query_scope"] == {"active_game_number": 4}
    assert "_resolved_game_id" not in visible
    assert stored[0]["_resolved_game_id"] == "hupu:168858"


@pytest.mark.asyncio
async def test_invalid_resolved_game_id_is_not_stored() -> None:
    bridge = AgentTaskBridge()

    async def runner(_name, _arguments):
        return {
            "status": "completed",
            "intent": "nba_query",
            "query_scope": {"game_id": "../../etc/passwd"},
            "answer_markdown": "已完成查询。",
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
    visible = await bridge.invoke(
        "nba_query", {"question": "这场谁赢了"}, task_id="task"
    )
    _, stored = bridge.unregister("task")

    assert visible["query_scope"] is None
    assert "_resolved_game_id" not in stored[0]


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
