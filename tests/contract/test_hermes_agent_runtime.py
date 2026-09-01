from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.src.application.ports import CancelToken, RuntimeStatus
from apps.api.src.infrastructure.agent_tools import NBA_TOOL_NAMES
from apps.api.src.infrastructure.hermes_agent_runtime import (
    AgentHistoryMessage,
    AgentTurnInput,
    HermesAgentRuntime,
)


class FakeRegistry:
    def __init__(self) -> None:
        self.entries = {}

    def register(self, **kwargs) -> None:
        self.entries[kwargs["name"]] = kwargs

    def get_tool_names_for_toolset(self, toolset: str):
        return sorted(
            name for name, entry in self.entries.items() if entry["toolset"] == toolset
        )


@pytest.mark.asyncio
async def test_official_runtime_exposes_only_nba_tools_and_normalises_result() -> None:
    registry = FakeRegistry()
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run_conversation(
            self,
            _message: str,
            *,
            conversation_history,
            task_id: str,
        ):
            captured["conversation_history"] = conversation_history
            captured["task_id"] = task_id
            raw = registry.entries["nba_schedule"]["handler"](
                {"date_expression": "下周"}, task_id=task_id
            )
            observation = json.loads(raw)
            return {
                "final_response": observation["answer_markdown"],
                "iterations": 2,
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            }

    runtime = HermesAgentRuntime(
        mode="embedded_agent",
        llm_mode="live",
        api_key="test-key",
        agent_factory=FakeAgent,
        registry=registry,
    )

    async def tool_runner(_name, _arguments):
        return {
            "status": "no_data",
            "intent": "schedule_result",
            "query_scope": {
                "start_date": "2026-08-31",
                "end_date": "2026-09-06",
                "timezone": "Asia/Shanghai",
            },
            "answer_markdown": "北京时间 2026-08-31 至 2026-09-06 没有查到比赛。",
            "blocks": [],
            "evidence_state": "none",
            "as_of_beijing": "2026-08-30 18:04",
        }

    result = await runtime.run(
        AgentTurnInput(
            request_id="request",
            opaque_session_id="session-hash",
            sanitized_question="下周有比赛吗",
            conversation_history=[
                {"role": "user", "content": "上一轮问了什么？"},
                {"role": "assistant", "content": "上一轮的安全摘要。"},
            ],
            now_beijing="2026-08-30 18:04",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        ),
        tool_runner=tool_runner,
        cancel=CancelToken(),
    )

    assert runtime.capability_self_test() is True
    assert tuple(sorted(runtime.manifest.tools_enabled)) == NBA_TOOL_NAMES
    assert tuple(sorted(registry.get_tool_names_for_toolset("nba"))) == NBA_TOOL_NAMES
    assert captured["enabled_toolsets"] == ["nba"]
    assert captured["session_id"] == "session-hash"
    assert captured["task_id"] != captured["session_id"]
    assert captured["conversation_history"] == [
        {"role": "user", "content": "上一轮问了什么？"},
        {"role": "assistant", "content": "上一轮的安全摘要。"},
    ]
    assert captured["skip_context_files"] is True
    assert captured["skip_memory"] is True
    assert captured["session_db"] is None
    assert captured["save_trajectories"] is False
    assert captured["reasoning_config"] == {"enabled": False, "effort": "none"}
    assert captured["request_overrides"]["extra_body"] == {
        "enable_thinking": False
    }
    assert 0 < captured["request_overrides"]["timeout"] <= 20
    assert result.status is RuntimeStatus.OK
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "nba_schedule"
    assert "2026-09-06" in result.answer_markdown


def test_runtime_self_test_fails_closed_without_key() -> None:
    runtime = HermesAgentRuntime(
        mode="embedded_agent",
        llm_mode="live",
        agent_factory=lambda **_kwargs: object(),
        registry=FakeRegistry(),
    )
    assert runtime.capability_self_test() is False
    assert runtime.status == "unavailable"


def test_runtime_rejects_unlocked_package_version() -> None:
    with pytest.raises(ValueError, match="package version"):
        HermesAgentRuntime(package_version="0.20.0")


def test_runtime_rejects_invalid_reasoning_effort() -> None:
    with pytest.raises(ValueError, match="reasoning effort"):
        HermesAgentRuntime(reasoning_effort="ultra")


@pytest.mark.parametrize(
    ("history", "message"),
    [
        (
            [AgentHistoryMessage(role="assistant", content="不能作为首条消息")],
            "roles must alternate",
        ),
        (
            [AgentHistoryMessage(role="user", content="缺少助手回复")],
            "complete turns",
        ),
        (
            [
                AgentHistoryMessage(role="user", content="第一问"),
                AgentHistoryMessage(role="assistant", content="第一答"),
                AgentHistoryMessage(role="assistant", content="重复角色"),
                AgentHistoryMessage(role="user", content="错误结尾"),
            ],
            "roles must alternate",
        ),
    ],
)
def test_agent_turn_rejects_non_alternating_or_incomplete_history(
    history: list[AgentHistoryMessage], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        AgentTurnInput(
            request_id="request",
            opaque_session_id="session-hash",
            sanitized_question="继续问",
            conversation_history=history,
            now_beijing="2026-09-01 10:00",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        )


def test_agent_turn_rejects_history_over_total_byte_budget() -> None:
    history = []
    for _ in range(4):
        history.extend(
            [
                AgentHistoryMessage(role="user", content="问" * 1_000),
                AgentHistoryMessage(role="assistant", content="答" * 1_000),
            ]
        )

    with pytest.raises(ValueError, match="bounded context size"):
        AgentTurnInput(
            request_id="request",
            opaque_session_id="session-hash",
            sanitized_question="继续问",
            conversation_history=history,
            now_beijing="2026-09-01 10:00",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        )
