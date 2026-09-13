from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.api.src.application.ports import CancelToken, RuntimeStatus
from apps.api.src.infrastructure.agent_tools import (
    FLOWER_TOOL_NAMES,
    TOOL_SCHEMAS,
    AgentTaskBridge,
    register_official_flower_tools,
    sanitise_observation,
)
from apps.api.src.infrastructure.hermes_agent_runtime import (
    AgentTurnInput,
    HermesAgentRuntime,
    sanitise_agent_answer,
)


class FakeRegistry:
    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}

    def register(self, **kwargs) -> None:
        self.entries[kwargs["name"]] = kwargs

    def get_tool_names_for_toolset(self, toolset: str):
        return sorted(
            name for name, entry in self.entries.items() if entry["toolset"] == toolset
        )


@pytest.mark.asyncio
async def test_flower_runtime_registers_only_flower_tools_and_prompt_is_domain_specific() -> None:
    registry = FakeRegistry()
    captured: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run_conversation(self, message, **_kwargs):
            captured["message"] = message
            return {"final_response": "绣球在明亮散射光下养护，表土稍干再浇透。"}

    runtime = HermesAgentRuntime(
        domain="flower",
        mode="embedded_agent",
        llm_mode="live",
        api_key="test-key",
        agent_factory=FakeAgent,
        registry=registry,
    )

    async def runner(_name, _args):
        return {
            "status": "completed",
            "intent": "general_care",
            "answer_markdown": "绣球需要观察表土和排水。",
            "evidence_state": "verified",
        }

    result = await runtime.run(
        AgentTurnInput(
            request_id="r",
            opaque_session_id="s",
            sanitized_question="绣球多久浇水？",
            context_hint="植物：绣球；地点：上海；光照：明亮散射光",
            now_beijing="2026-09-13 10:00",
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        ),
        tool_runner=runner,
        cancel=CancelToken(),
    )

    assert runtime.capability_self_test() is True
    assert tuple(sorted(runtime.manifest.tools_enabled)) == FLOWER_TOOL_NAMES
    assert captured["enabled_toolsets"] == ["flower"]
    assert captured["message"] == "绣球多久浇水？"
    assert captured["save_trajectories"] is False
    assert captured["skip_context_files"] is True
    assert captured["skip_memory"] is True
    assert captured["session_db"] is None
    assert captured["checkpoints_enabled"] is False
    assert captured["load_soul_identity"] is False
    assert "NBA" not in captured["ephemeral_system_prompt"]
    assert "种花 Agent" in captured["ephemeral_system_prompt"]
    assert "始终直接回答本轮用户原问题" in captured["ephemeral_system_prompt"]
    assert result.status is RuntimeStatus.OK
    assert "绣球" in (result.answer_markdown or "")


@pytest.mark.asyncio
async def test_bridge_rejects_cross_domain_tool_calls() -> None:
    registry = FakeRegistry()
    register_official_flower_tools(registry)
    bridge = AgentTaskBridge()

    async def runner(_name, _args):
        return {"status": "completed", "answer_markdown": "ok"}

    bridge.register(
        "flower-task",
        loop=__import__("asyncio").get_running_loop(),
        runner=runner,
        deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
        cancel=CancelToken(),
        toolset="flower",
    )
    blocked = await bridge.invoke(
        "nba_query", {"question": "比分"}, task_id="flower-task"
    )
    allowed = await bridge.invoke(
        "flower_search", {"query": "上海九月月季养护"}, task_id="flower-task"
    )
    bridge.unregister("flower-task")
    assert blocked["status"] == "failed"
    assert allowed["status"] == "completed"


def test_model_answer_filter_removes_internal_names_and_urls() -> None:
    answer = sanitise_agent_answer(
        "Hermes 调用了 flower_search，详见 https://example.invalid/x。\n"
        "建议把绣球放在明亮散射光下。"
    )
    assert answer is not None
    assert "Hermes" not in answer
    assert "flower_search" not in answer
    assert "https://" not in answer
    assert "明亮散射光" in answer


def test_flower_observation_drops_private_scope_and_metadata() -> None:
    observation = sanitise_observation(
        {
            "status": "completed",
            "intent": "general_care",
            "query_scope": {
                "plant": "绣球",
                "location": "上海市浦东新区XX路88号",
                "source_url": "https://private.invalid",
            },
            "answer_markdown": "建议观察盆土；来源：https://private.invalid",
            "blocks": [{"provider_payload": "secret", "text": "明亮散射光"}],
            "evidence_state": "partial",
            "as_of_beijing": "not-a-date",
        },
        max_bytes=4096,
    )
    encoded = str(observation)
    assert "private.invalid" not in encoded
    assert "浦东" not in encoded
    assert "provider_payload" not in encoded
    assert "明亮散射光" in encoded


def test_flower_model_schema_is_canonical_and_care_plan_requires_subject() -> None:
    lookup = TOOL_SCHEMAS["flower_lookup"]["parameters"]
    assert set(lookup["properties"]) == {"question", "plant"}
    assert lookup["additionalProperties"] is False
    plan = TOOL_SCHEMAS["care_plan"]["parameters"]
    assert plan["additionalProperties"] is False

    bridge = AgentTaskBridge()
    import asyncio

    async def runner(_name, _args):
        return {"status": "completed", "answer_markdown": "ok"}

    async def exercise() -> dict:
        bridge.register(
            "flower-schema-task",
            loop=asyncio.get_running_loop(),
            runner=runner,
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
            cancel=CancelToken(),
            toolset="flower",
        )
        try:
            return await bridge.invoke(
                "care_plan", {"location": "上海"}, task_id="flower-schema-task"
            )
        finally:
            bridge.unregister("flower-schema-task")

    assert asyncio.run(exercise())["status"] == "failed"


def test_bridge_rejects_mixed_domain_allow_list() -> None:
    import asyncio

    with pytest.raises(ValueError, match="mixes domains"):
        AgentTaskBridge().register(
            "mixed-task",
            loop=asyncio.new_event_loop(),
            runner=lambda *_args: None,
            deadline_at_utc=datetime.now(UTC) + timedelta(seconds=5),
            cancel=CancelToken(),
            allowed_tools=["flower_search", "nba_query"],
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("H e r m e s 生成：建议放在散射光下。", "散射光"),
        ("由 Ｈｅｒｍｅｓ 生成。", None),
        ("使用 A g e n t 回答；绣球表土稍干再浇水。", "绣球"),
    ],
)
def test_model_answer_filter_handles_obfuscated_private_names(
    value: str, expected: str | None
) -> None:
    cleaned = sanitise_agent_answer(value)
    if expected is None:
        assert cleaned is None
    else:
        assert cleaned is not None
        assert "hermes" not in cleaned.casefold()
        assert expected in cleaned


def test_observation_byte_limit_is_true_for_noncompact_json() -> None:
    observation = sanitise_observation(
        {
            "status": "completed",
            "intent": "general_care",
            "answer_markdown": "花" * 50_000,
            "evidence_state": "partial",
        },
        max_bytes=1024,
    )
    import json

    assert len(json.dumps(observation, ensure_ascii=False).encode()) <= 1024
    assert observation["as_of_beijing"] is None
