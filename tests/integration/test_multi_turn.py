from __future__ import annotations

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.providers.fixture_provider import FixtureProvider


@pytest.mark.asyncio
async def test_three_turn_context_and_fresh_session_isolation() -> None:
    usecase = ChatUseCase(FixtureProvider())
    session_id = None
    answers = []
    for message in (
        "2025-26 总决赛 G4 谁得分最高？",
        "那场最后五秒发生了什么？",
        "最后那个球是哪位球员？",
    ):
        result = await usecase.handle({"session_id": session_id, "message": message})
        assert result.status == "completed"
        session_id = result.session_id
        answers.append(result.answer_markdown)
    assert "杰伦·布朗" in answers[0]
    assert "2 个回合" in answers[1]
    fresh = await usecase.handle({"message": "那场最后五秒发生了什么？"})
    assert fresh.status == "needs_clarification"
