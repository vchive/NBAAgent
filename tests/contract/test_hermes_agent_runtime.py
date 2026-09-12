from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.src.application.ports import CancelToken, RuntimeStatus
from apps.api.src.domain.models import ErrorCode
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_reason", "expected_code", "finish_reason", "retryable"),
    [
        ("rate_limit", ErrorCode.COMPOSER_UNAVAILABLE, "rate_limited", True),
        ("quota_exceeded", ErrorCode.COMPOSER_UNAVAILABLE, "rate_limited", True),
        ("billing", ErrorCode.COMPOSER_UNAVAILABLE, "quota_exhausted", False),
        ("timeout", ErrorCode.UPSTREAM_TIMEOUT, "timeout", True),
        (
            "authentication",
            ErrorCode.COMPOSER_UNAVAILABLE,
            "authentication_failed",
            False,
        ),
    ],
)
async def test_runtime_preserves_typed_failed_result(
    failure_reason: str,
    expected_code: ErrorCode,
    finish_reason: str,
    retryable: bool,
) -> None:
    class FailedAgent:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_conversation(self, *_args, **_kwargs):
            return {
                "final_response": "provider-specific error must not be shown",
                "completed": False,
                "failed": True,
                "failure_reason": failure_reason,
                "error": "secret upstream detail",
            }

    runtime = HermesAgentRuntime(
        mode="embedded_agent",
        llm_mode="live",
        api_key="test-key",
        agent_factory=FailedAgent,
        registry=FakeRegistry(),
    )
    result = await runtime.run(
        AgentTurnInput(
            request_id="request",
            opaque_session_id="session-hash",
            sanitized_question="请分析这场比赛",
            now_beijing="2026-09-09 22:00",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        ),
        tool_runner=lambda *_args, **_kwargs: None,
        cancel=CancelToken(),
    )

    assert result.status in {RuntimeStatus.UNAVAILABLE, RuntimeStatus.TIMEOUT}
    assert result.answer_markdown is None
    assert result.error_code is expected_code
    assert result.finish_reason == finish_reason
    assert result.retryable is retryable
    assert "secret" not in (result.finish_reason or "")


@pytest.mark.asyncio
async def test_runtime_classifies_raised_429_without_exposing_body() -> None:
    class RateLimitFailure(Exception):
        status_code = 429

    class RaisingAgent:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_conversation(self, *_args, **_kwargs):
            raise RateLimitFailure("secret upstream detail")

    runtime = HermesAgentRuntime(
        mode="embedded_agent",
        llm_mode="live",
        api_key="test-key",
        agent_factory=RaisingAgent,
        registry=FakeRegistry(),
    )
    result = await runtime.run(
        AgentTurnInput(
            request_id="request",
            opaque_session_id="session-hash",
            sanitized_question="请分析这场比赛",
            now_beijing="2026-09-09 22:00",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        ),
        tool_runner=lambda *_args, **_kwargs: None,
        cancel=CancelToken(),
    )

    assert result.status is RuntimeStatus.UNAVAILABLE
    assert result.error_code is ErrorCode.COMPOSER_UNAVAILABLE
    assert result.finish_reason == "rate_limited"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_runtime_treats_429_with_explicit_balance_failure_as_exhausted() -> None:
    class BalanceFailure(Exception):
        status_code = 429

    class RaisingAgent:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_conversation(self, *_args, **_kwargs):
            raise BalanceFailure("insufficient account balance: secret detail")

    runtime = HermesAgentRuntime(
        mode="embedded_agent",
        llm_mode="live",
        api_key="test-key",
        agent_factory=RaisingAgent,
        registry=FakeRegistry(),
    )
    result = await runtime.run(
        AgentTurnInput(
            request_id="request",
            opaque_session_id="session-hash",
            sanitized_question="请分析这场比赛",
            now_beijing="2026-09-09 22:00",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        ),
        tool_runner=lambda *_args, **_kwargs: None,
        cancel=CancelToken(),
    )

    assert result.status is RuntimeStatus.UNAVAILABLE
    assert result.error_code is ErrorCode.COMPOSER_UNAVAILABLE
    assert result.finish_reason == "quota_exhausted"
    assert result.retryable is False


def test_runtime_self_test_fails_closed_without_key() -> None:
    runtime = HermesAgentRuntime(
        mode="embedded_agent",
        llm_mode="live",
        agent_factory=lambda **_kwargs: object(),
        registry=FakeRegistry(),
    )
    assert runtime.capability_self_test() is False
    assert runtime.status == "unavailable"


@pytest.mark.asyncio
async def test_runtime_preserves_typed_quota_failure_from_raw_result() -> None:
    class FailedAgent:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_conversation(self, *_args, **_kwargs):
            return {
                "status": "failed",
                "failure_reason": "billing quota_exhausted",
            }

    runtime = HermesAgentRuntime(
        mode="embedded_agent",
        llm_mode="live",
        api_key="test-key",
        agent_factory=FailedAgent,
        registry=FakeRegistry(),
    )
    result = await runtime.run(
        AgentTurnInput(
            request_id="request",
            opaque_session_id="session-hash",
            sanitized_question="这场比赛怎么赢的？",
            now_beijing="2026-09-09 20:00",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        ),
        tool_runner=lambda *_args, **_kwargs: None,
        cancel=CancelToken(),
    )

    assert result.status is RuntimeStatus.UNAVAILABLE
    assert result.error_code == "COMPOSER_UNAVAILABLE"
    assert result.retryable is False
    assert result.finish_reason == "quota_exhausted"
    assert result.answer_markdown is None


@pytest.mark.asyncio
async def test_runtime_classifies_auth_exception_without_exposing_raw_reason() -> None:
    class FailedAgent:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_conversation(self, *_args, **_kwargs):
            raise RuntimeError("401 invalid API key secret-value")

    runtime = HermesAgentRuntime(
        mode="embedded_agent",
        llm_mode="live",
        api_key="test-key",
        agent_factory=FailedAgent,
        registry=FakeRegistry(),
    )
    result = await runtime.run(
        AgentTurnInput(
            request_id="request",
            opaque_session_id="session-hash",
            sanitized_question="这场比赛怎么赢的？",
            now_beijing="2026-09-09 20:00",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        ),
        tool_runner=lambda *_args, **_kwargs: None,
        cancel=CancelToken(),
    )

    assert result.status is RuntimeStatus.UNAVAILABLE
    assert result.error_code == "COMPOSER_UNAVAILABLE"
    assert result.retryable is False
    assert "secret-value" not in str(result.finish_reason)


def test_runtime_rejects_unlocked_package_version() -> None:
    with pytest.raises(ValueError, match="package version"):
        HermesAgentRuntime(package_version="0.20.0")


def test_runtime_rejects_invalid_reasoning_effort() -> None:
    with pytest.raises(ValueError, match="reasoning effort"):
        HermesAgentRuntime(reasoning_effort="ultra")


def test_system_prompt_keeps_search_evidence_internal() -> None:
    prompt = HermesAgentRuntime._system_prompt(
        AgentTurnInput(
            request_id="request",
            opaque_session_id="session-hash",
            sanitized_question="这场比赛怎么赢的？",
            conversation_history=[],
            now_beijing="2026-09-01 10:00",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        )
    )

    assert "搜索观察不得直接出现在面向用户的回答中" in prompt
    assert "称呼提问者为“您”" in prompt
    assert "客观、专业、中立" in prompt
    assert "结构化记录未命中但搜索已有与问题相关的材料时，必须直接综合回答" in prompt
    assert "若已经获得完整、相关且未截断的回答，应保留该回答" in prompt
    assert "总决赛 MVP 是系列赛荣誉" in prompt
    assert "不要写成“本场当选”" in prompt
    assert "不得用赛季、季后赛或系列赛场均值解释某一场为什么赢" in prompt
    assert "标题、否定句、对手事件或其他场次事件都不能作为支持" in prompt
    assert "凭借本场表现最终获得系列赛奖项" in prompt
    assert "冠军荒、历史排名或跨时代球员比较" in prompt
    assert "不要在正文交代结构化记录、搜索材料或证据分层" in prompt
    assert "必须同时取得 nba_query 的比赛事实" in prompt
    assert "nba_search 的过程材料" in prompt
    assert "不要使用“硬事实”“来自赛后报道”“措辞保留”" in prompt
    assert "冠军空缺、自某年起首次登顶" in prompt
    assert "不要用“综合结构化事实和过程材料”开场" in prompt
    assert "不要写“回答如下”“可以确认的是”“需要说明的一点”" in prompt
    for wording in (
        "最精华",
        "最精彩",
        "最好看",
        "最经典",
        "最有悬念",
        "最关键",
        "最值得回看或复盘",
        "推荐哪场",
        "你会选哪场",
    ):
        assert wording in prompt
    assert "不得要求再次提供球队" in prompt
    assert "本轮只调用一次 nba_query" in prompt
    assert "不要再调用搜索、赛程或新闻工具" in prompt
    assert "明确选出一场" in prompt
    assert "用户明确要求比较两名球员" in prompt
    assert "没有唯一客观口径" in prompt


def test_system_prompt_does_not_expose_internal_agent_brand() -> None:
    prompt = HermesAgentRuntime._system_prompt(
        AgentTurnInput(
            request_id="request",
            opaque_session_id="session-hash",
            sanitized_question="你使用什么实现？",
            context_hint="用户上一轮追问 HERmEs 的实现细节。",
            conversation_history=[],
            now_beijing="2026-09-01 10:00",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        )
    )

    assert "hermes" not in prompt.lower()
    assert "内部智能服务" in prompt


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
