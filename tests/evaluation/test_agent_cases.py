from __future__ import annotations

from apps.api.src.domain.models import IntelligenceMode
from apps.api.src.evaluation.runner import EvaluationRunner


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
