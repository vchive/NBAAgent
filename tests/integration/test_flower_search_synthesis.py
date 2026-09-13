"""Search-grounding projection tests for the flower chat use case."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.api.src.application.flower_chat_use_case import FlowerChatUseCase
from apps.api.src.application.ports import RuntimeStatus
from apps.api.src.config import Settings
from apps.api.src.domain.models import ChatRequest, IntelligenceMode


class _Search:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def search_web(self, _query, _budget):
        self.calls += 1
        return self.result


@pytest.mark.asyncio
async def test_non_full_search_keeps_local_answer_and_adds_one_controlled_line() -> None:
    search = _Search(
        {
            "data": [
                {
                    "title": "月季施肥参考",
                    "summary": "资料提醒根据温度、盆土干湿和光照调整施肥。",
                },
                {
                    "title": "月季养护文章",
                    "summary": "这是一段不应原样展示的长摘要。",
                },
            ],
            "partial": True,
            "evidence": [],
        }
    )
    use_case = FlowerChatUseCase(
        settings=Settings(default_intelligence_mode="hybrid"),
        search_provider=search,
    )

    result = await use_case.handle(
        ChatRequest(message="上海九月月季需要怎样调整施肥？")
    )

    assert search.calls == 1
    assert result.status == "completed"
    assert result.evidence_state == "partial"
    assert result.data_origin == "mixed"
    assert "月季施肥" in result.answer_markdown
    assert result.answer_markdown.count("补充背景：") == 1
    assert "月季施肥参考" not in result.answer_markdown
    assert "不应原样展示" not in result.answer_markdown
    assert "http" not in result.answer_markdown.lower()


@pytest.mark.asyncio
async def test_full_agent_echo_of_search_payload_is_not_public_answer() -> None:
    class Agent:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(
                status=RuntimeStatus.OK,
                answer_markdown=(
                    "公开资料线索：\n- 月季施肥参考：请按文章步骤执行。\n"
                    "- 月季养护文章：另一个摘要。"
                ),
                observations=[
                    {
                        "title": "月季施肥参考",
                        "snippet": "请按文章步骤执行。",
                        "evidence_state": "partial",
                        "data_origin": "search",
                    },
                    {
                        "title": "月季养护文章",
                        "snippet": "另一个摘要。",
                        "evidence_state": "partial",
                        "data_origin": "search",
                    },
                ],
                error_code=None,
                latency_ms=2,
            )

    settings = Settings(
        full_intelligence_enabled=True,
        default_intelligence_mode="full",
    )
    use_case = FlowerChatUseCase(settings=settings, agent_runtime=Agent())
    result = await use_case.handle(
        ChatRequest(
            message="上海九月月季需要怎样调整施肥？",
            intelligence_mode=IntelligenceMode.FULL,
        )
    )

    assert result.composition["status"] == "fallback"
    assert "月季施肥参考" not in result.answer_markdown
    assert result.answer_markdown.count("补充背景：") == 1
    assert result.data_origin == "mixed"
    assert result.evidence_state == "partial"
