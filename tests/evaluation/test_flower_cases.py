"""Offline golden-question evaluation for the flower assistant.

The flower vertical is intentionally evaluated independently from the legacy
NBA rubric.  These cases are a small product contract: a response must route
to the right public status, contain the useful domain terms, and never expose
implementation details.  The runner uses the deterministic local core only;
network/model quality is measured by a separate deployment check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from apps.api.src.api.schemas import ChatRequest
from apps.api.src.application.flower_chat_use_case import FlowerChatUseCase
from apps.api.src.config import Settings

_CASES_PATH = Path(__file__).with_name("flower_cases.jsonl")
_VALID_CATEGORIES = set("ABCDEFGHI")
_VALID_STATUSES = {
    "completed",
    "needs_clarification",
    "blocked",
    "no_data",
    "failed",
}
_URL_RE = re.compile(r"(?:https?|ftp|file)://|www\.", re.IGNORECASE)
_PRIVATE_RE = re.compile(
    r"(?:\bhermes\b|\bprovider\b|\bruntime\b|\btool[_ -]?call\b|"
    r"系统提示词|接口密钥|api[_ -]?key|bearer)",
    re.IGNORECASE,
)


def _load_cases() -> list[dict[str, Any]]:
    """Load and validate the JSONL contract before pytest parametrisation."""

    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        _CASES_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - schema test reports it
            raise AssertionError(f"invalid JSON on line {line_number}: {exc}") from exc
        assert isinstance(value, dict), f"line {line_number} must be an object"
        for key in ("case_id", "category", "prompt", "expected_status"):
            assert key in value, f"line {line_number} is missing {key}"
        assert isinstance(value["case_id"], str) and value["case_id"].strip()
        assert value["category"] in _VALID_CATEGORIES
        assert isinstance(value["prompt"], str) and value["prompt"].strip()
        expected = value["expected_status"]
        expected_values = [expected] if isinstance(expected, str) else expected
        assert isinstance(expected_values, list) and expected_values
        assert all(str(item).lower() in _VALID_STATUSES for item in expected_values)
        for field in ("required_terms", "forbidden_terms"):
            terms = value.get(field, [])
            assert isinstance(terms, list), f"{field} must be a list"
            for term in terms:
                if field == "required_terms" and isinstance(term, list):
                    assert term and all(isinstance(item, str) and item.strip() for item in term)
                else:
                    assert isinstance(term, str) and term.strip()
        if value["category"] == "F":
            assert value.get("safety_expected") == "block"
        cases.append(value)
    return cases


CASES = _load_cases()


def _expected_status(case: dict[str, Any]) -> set[str]:
    value = case["expected_status"]
    values = [value] if isinstance(value, str) else value
    return {str(item).lower() for item in values}


def _requirement_matches(answer: str, requirement: str | list[str]) -> bool:
    if isinstance(requirement, list):
        return any(item.casefold() in answer.casefold() for item in requirement)
    return requirement.casefold() in answer.casefold()


def _failures(case: dict[str, Any], result: Any) -> list[str]:
    answer = str(getattr(result, "answer_markdown", "") or "")
    failures: list[str] = []
    status = str(getattr(result, "status", "")).lower()
    if status not in _expected_status(case):
        failures.append(f"status={status!r}, expected={sorted(_expected_status(case))!r}")
    if not answer.strip():
        failures.append("answer is empty")
    for requirement in case.get("required_terms", []):
        if not _requirement_matches(answer, requirement):
            failures.append(f"missing required term(s): {requirement!r}")
    for forbidden in case.get("forbidden_terms", []):
        if forbidden.casefold() in answer.casefold():
            failures.append(f"contains forbidden term: {forbidden!r}")
    # Check the complete domain projection as well as the answer: a provider
    # token hidden in notices/composition is still a public-boundary failure.
    public_text = json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    if _URL_RE.search(public_text):
        failures.append("public projection contains a URL")
    if _PRIVATE_RE.search(public_text):
        failures.append("public projection contains an implementation token")
    return failures


def _offline_usecase() -> FlowerChatUseCase:
    # No adapter/runtime is supplied, so this runner cannot accidentally spend
    # a network or model quota.  Each case still receives a fresh session ID.
    return FlowerChatUseCase(
        settings=Settings(
            agent_domain="flower",
            full_intelligence_enabled=False,
            default_intelligence_mode="hybrid",
        )
    )


def _case_id(case: dict[str, Any]) -> str:
    return str(case["case_id"])


def test_flower_gold_schema_is_complete_and_unique() -> None:
    assert len(CASES) == 63
    ids = [case["case_id"] for case in CASES]
    assert len(ids) == len(set(ids))
    assert {case["category"] for case in CASES} == _VALID_CATEGORIES
    counts = {
        category: sum(case["category"] == category for case in CASES)
        for category in _VALID_CATEGORIES
    }
    assert counts == {
        "A": 10,
        "B": 10,
        "C": 10,
        "D": 8,
        "E": 8,
        "F": 5,
        "G": 4,
        "H": 4,
        "I": 4,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=_case_id)
async def test_flower_gold_case(case: dict[str, Any]) -> None:
    result = await _offline_usecase().handle(
        ChatRequest(message=case["prompt"], session_id=uuid4())
    )
    failures = _failures(case, result)
    assert not failures, (
        f"{case['case_id']}: {'; '.join(failures)}\n"
        f"Answer: {result.answer_markdown}"
    )
    if case["category"] == "F":
        assert result.status == "blocked"
        assert result.evidence_state == "none"


@pytest.mark.asyncio
async def test_sc001_non_safety_gold_cases_meet_90_percent_threshold() -> None:
    cases = [case for case in CASES if case["category"] != "F"]
    usecase = _offline_usecase()
    passed = 0
    failures: dict[str, list[str]] = {}
    for case in cases:
        result = await usecase.handle(ChatRequest(message=case["prompt"], session_id=uuid4()))
        case_failures = _failures(case, result)
        if not case_failures:
            passed += 1
        else:
            failures[case["case_id"]] = case_failures
    ratio = passed / len(cases)
    assert ratio >= 0.90, f"SC-001 passed {passed}/{len(cases)} ({ratio:.1%}); failures={failures}"


@pytest.mark.asyncio
async def test_sc004_every_safety_case_is_blocked_with_actionable_help() -> None:
    cases = [case for case in CASES if case["category"] == "F"]
    usecase = _offline_usecase()
    failures: dict[str, list[str]] = {}
    for case in cases:
        result = await usecase.handle(ChatRequest(message=case["prompt"], session_id=uuid4()))
        case_failures = _failures(case, result)
        if result.status != "blocked":
            case_failures.append(f"safety status is {result.status!r}")
        if not any(term in result.answer_markdown for term in ("不要", "立即", "求助", "兽医")):
            case_failures.append("missing immediate/do-not-do/help action")
        if case_failures:
            failures[case["case_id"]] = case_failures
    assert not failures, f"SC-004 safety failures: {failures}"


@pytest.mark.asyncio
async def test_multi_turn_context_and_session_isolation() -> None:
    usecase = _offline_usecase()
    session_a, session_b = uuid4(), uuid4()
    first = await usecase.handle(
        ChatRequest(
            message="我在上海养绣球，北阳台明亮散射光",
            session_id=session_a,
        )
    )
    follow_up = await usecase.handle(
        ChatRequest(message="那这盆多久浇水？", session_id=session_a)
    )
    symptom = await usecase.handle(
        ChatRequest(message="它黄叶怎么办？", session_id=session_a)
    )
    unrelated = await usecase.handle(
        ChatRequest(message="那这盆多久浇水？", session_id=session_b)
    )

    assert first.session_id == follow_up.session_id == symptom.session_id == session_a
    assert unrelated.session_id == session_b
    assert "绣球浇水" in follow_up.answer_markdown
    assert "上海" in follow_up.answer_markdown
    assert "观察" in symptom.answer_markdown
    assert "请告诉我花卉名称" in unrelated.answer_markdown
    assert "绣球" not in unrelated.answer_markdown


__all__ = ["CASES"]
