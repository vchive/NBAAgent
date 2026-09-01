from __future__ import annotations

import pytest

from apps.api.src.application.chat_use_case import ChatUseCase
from apps.api.src.domain.models import IntelligenceMode
from apps.api.src.evaluation.runner import EvaluationRunner
from apps.api.src.providers.fixture_provider import FixtureProvider


def test_agent_acceptance_trio_is_versioned_in_golden_set() -> None:
    runner = EvaluationRunner(object())
    cases = {case.case_id: case for case in runner.load_cases()}
    expected = {
        "AGENT-greeting": "nihao",
        "AGENT-schedule-typo": "下周有比赛买",
        "AGENT-schedule-empty": "下周有比赛吗",
    }
    assert expected.keys() <= cases.keys()
    for case_id, prompt in expected.items():
        [turn] = cases[case_id].turns
        assert turn.prompt == prompt
        assert turn.intelligence_mode is IntelligenceMode.FULL


@pytest.mark.asyncio
async def test_follow_up_golden_case_scores_the_explicit_terminal_shot_intent() -> None:
    """A new event question must not be scored as a generic follow-up."""

    runner = EvaluationRunner(ChatUseCase(FixtureProvider()))
    case = next(item for item in runner.load_cases() if item.case_id == "H-follow-up")

    [run] = await runner.run(cases=[case])

    assert run.ratings["understanding"] == 1.0
    assert run.ratings["consistency"] == 1.0
    assert run.ratings["safety"] == 1.0
