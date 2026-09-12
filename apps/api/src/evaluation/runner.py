"""Repeatable golden-question evaluator with explicit execution profiles.

The default suite measures the deterministic fixture product path.  Live Agent
quality is a separate suite and must prove that the public response was actually
produced by the Agent path.  Keeping those reports separate prevents a fixture
fallback from being presented as a successful live-model evaluation.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.src.api.schemas import ChatRequest
from apps.api.src.domain.models import (
    EvaluationCase,
    EvaluationProviderMode,
    EvaluationRun,
    EvaluationTurn,
    EvidenceState,
)

# The seven dimensions and weights are the scoring contract in
# ``contracts/evaluation.md``. Values stored in ``ratings``/``scores`` are
# normalised to 0..1; report output converts the weighted sum to 0..100.
DIMENSIONS: tuple[str, ...] = (
    "understanding",
    "accuracy",
    "completeness",
    "expression",
    "structure",
    "consistency",
    "latency",
)
DIMENSION_WEIGHTS: dict[str, float] = {
    "understanding": 0.20,
    "accuracy": 0.20,
    "completeness": 0.15,
    "expression": 0.10,
    "structure": 0.10,
    "consistency": 0.10,
    "latency": 0.15,
}

# Older local consumers used these names. They are retained as non-contract
# aliases in ratings/scores so existing scripts keep working; report aggregation
# only uses ``DIMENSIONS``.
_LEGACY_ALIASES = {
    "factual_correctness": "accuracy",
    "time_accuracy": "latency",
    "style": "expression",
}

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:,\d{3})*(?:\.\d+)?%?(?![A-Za-z0-9_])")
_LEAK_RE = re.compile(
    r"(?:https?|ftp|file)://|www\."
    r"|\b(?:hermes|deepseek|qwen|chatgpt|openai|siliconflow|fastapi|langchain|"
    r"pydantic|uvicorn|dashscope|qianfan|duckduckgo|espn|fixture(?:\.v\d+)?|"
    r"provider|sqlite|bm25|rag)\b"
    r"|(?:百度千帆|百度搜索接口|阿里云|通义千问|虎扑接口|系统提示词|开发者消息|"
    r"(?:已)?调用(?:了)?工具|工具调用|工具返回|公开网页线索|结构化比赛数据|"
    r"内部(?:模型|框架|运行时|链路|缓存|数据库|检索)|"
    r"(?:使用|采用|基于|接入).{0,12}(?:缓存|数据库|检索链路|搜索API|工具链))"
    r"|(?:source_ref|evidence_ids?|canonical_id|provider(?:[_ -]?(?:call|cache|"
    r"result|response|payload|url|endpoint|name|id))|raw_(?:json|response|payload)|"
    r"trace_id|stack_trace|session_id|request_id|api[_ -]?key|bearer(?:[_ -]?token)?|"
    r"system_prompt|developer_message|tool_call|retrieval_pipeline)",
    re.IGNORECASE,
)
_GENERIC_CLARIFICATION_RE = re.compile(
    r"(?:需要|请)(?:您)?(?:补充|提供|明确)(?:一下)?(?:查询)?(?:对象|条件|信息|"
    r"具体比赛|具体场次|日期|范围)|换个问法(?:再试)?",
    re.IGNORECASE,
)
_VALID_STATUSES = {
    "completed",
    "needs_clarification",
    "blocked",
    "no_data",
    "failed",
}
EvaluationSuite = Literal["fixture", "live_agent", "all"]
SemanticRequirement = str | list[str]


class GoldenRecommendationTurnAssertion(BaseModel):
    """Branch-specific assertions for one recommendation conversation turn."""

    model_config = ConfigDict(extra="forbid")

    expected_entities: Any = None
    required_terms: list[SemanticRequirement] = Field(default_factory=list, max_length=64)
    forbidden_terms: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("required_terms")
    @classmethod
    def _valid_required_terms(
        cls, value: list[SemanticRequirement]
    ) -> list[SemanticRequirement]:
        for requirement in value:
            alternatives = [requirement] if isinstance(requirement, str) else requirement
            if not alternatives or any(not str(item).strip() for item in alternatives):
                raise ValueError(
                    "recommendation required_terms entries must be non-empty"
                )
        return value

    @field_validator("forbidden_terms")
    @classmethod
    def _valid_forbidden_terms(cls, value: list[str]) -> list[str]:
        if any(not str(item).strip() for item in value):
            raise ValueError("recommendation forbidden_terms entries must be non-empty")
        return value


class GoldenRecommendationBranch(BaseModel):
    """One defensible recommendation and its downstream truth contract."""

    model_config = ConfigDict(extra="forbid")

    selection_terms: list[str] = Field(min_length=1, max_length=32)
    turn_assertions: dict[int, GoldenRecommendationTurnAssertion] = Field(
        default_factory=dict,
        max_length=8,
    )

    @field_validator("selection_terms")
    @classmethod
    def _valid_selection_terms(cls, value: list[str]) -> list[str]:
        if any(not str(item).strip() for item in value):
            raise ValueError("recommendation selection terms must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("recommendation selection terms must be unique")
        return value


class GoldenEvaluationTurn(EvaluationTurn):
    """Evaluation-only assertions layered on the stable domain case schema."""

    expected_status: str | list[str] | None = None
    clarification_allowed: bool = False
    required_terms: list[SemanticRequirement] = Field(default_factory=list, max_length=64)
    forbidden_terms: list[str] = Field(default_factory=list, max_length=64)
    agent_expected: bool = False

    @field_validator("expected_status")
    @classmethod
    def _valid_expected_status(cls, value: str | list[str] | None) -> str | list[str] | None:
        if value is None:
            return value
        values = [value] if isinstance(value, str) else value
        normalised = [str(item).strip().lower() for item in values]
        if not normalised or any(item not in _VALID_STATUSES for item in normalised):
            raise ValueError("expected_status must contain public chat statuses")
        if len(set(normalised)) != len(normalised):
            raise ValueError("expected_status values must be unique")
        return normalised[0] if isinstance(value, str) else normalised

    @field_validator("required_terms")
    @classmethod
    def _valid_required_terms(
        cls, value: list[SemanticRequirement]
    ) -> list[SemanticRequirement]:
        for requirement in value:
            alternatives = [requirement] if isinstance(requirement, str) else requirement
            if not alternatives or any(not str(item).strip() for item in alternatives):
                raise ValueError("required_terms entries must be non-empty strings or alternatives")
        return value

    @field_validator("forbidden_terms")
    @classmethod
    def _valid_forbidden_terms(cls, value: list[str]) -> list[str]:
        if any(not str(item).strip() for item in value):
            raise ValueError("forbidden_terms entries must be non-empty")
        return value

    @model_validator(mode="after")
    def _clarification_contract(self) -> GoldenEvaluationTurn:
        values = self.expected_status
        expected = {values} if isinstance(values, str) else set(values or [])
        if "needs_clarification" in expected and not self.clarification_allowed:
            raise ValueError(
                "needs_clarification requires clarification_allowed=true in a golden case"
            )
        return self


class GoldenEvaluationCase(EvaluationCase):
    """Versioned golden case with fixture/live-Agent suite ownership."""

    turns: list[GoldenEvaluationTurn] = Field(min_length=1, max_length=8)
    evaluation_profile: Literal["fixture", "live_agent"] = "fixture"
    # A later turn may legitimately narrow a series/team scope to one
    # canonical game.  Keep that relationship in the golden contract instead
    # of guessing membership from an opaque provider game ID.  During
    # consistency scoring each declared child is expanded to these parent
    # entities; undeclared games remain distinct and still expose cross-talk.
    entity_scope_memberships: dict[str, list[str]] = Field(
        default_factory=dict,
        max_length=64,
    )
    # A subjective recommendation may have more than one evidence-backed
    # answer.  Branches keep those alternatives explicit and, crucially, bind
    # later entity/PBP assertions to the option the answer actually selected.
    # This avoids both forcing one editorial opinion and accepting a follow-up
    # that silently jumps to another game.
    recommendation_turn: int | None = Field(default=None, ge=1, le=8)
    recommendation_branches: dict[str, GoldenRecommendationBranch] = Field(
        default_factory=dict,
        max_length=16,
    )

    @field_validator("entity_scope_memberships")
    @classmethod
    def _valid_entity_scope_memberships(
        cls, value: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        for child_id, parent_ids in value.items():
            if not child_id.strip() or not parent_ids:
                raise ValueError("entity scope memberships require child and parent IDs")
            if any(not item.strip() for item in parent_ids):
                raise ValueError("entity scope membership IDs must be non-empty")
            if len(parent_ids) != len(set(parent_ids)):
                raise ValueError("entity scope membership parent IDs must be unique")
        return value

    @model_validator(mode="after")
    def _profile_contract(self) -> GoldenEvaluationCase:
        if self.evaluation_profile == "fixture" and any(
            turn.agent_expected for turn in self.turns
        ):
            raise ValueError("agent_expected cases must use evaluation_profile=live_agent")
        if self.evaluation_profile == "live_agent" and not any(
            turn.agent_expected for turn in self.turns
        ) and any(str(turn.safety_expected.value).upper() == "ALLOW" for turn in self.turns):
            raise ValueError(
                "live_agent ALLOW cases must contain an agent_expected turn"
            )
        has_turn = self.recommendation_turn is not None
        has_branches = bool(self.recommendation_branches)
        if has_turn != has_branches:
            raise ValueError(
                "recommendation_turn and recommendation_branches must be declared together"
            )
        if has_branches:
            if len(self.recommendation_branches) < 2:
                raise ValueError("recommendation contracts require at least two branches")
            if self.recommendation_turn is None or self.recommendation_turn > len(self.turns):
                raise ValueError("recommendation_turn must reference a case turn")
            seen_terms: set[str] = set()
            for game_id, branch in self.recommendation_branches.items():
                if not game_id.strip():
                    raise ValueError("recommendation branch game IDs must be non-empty")
                if game_id not in self.entity_scope_memberships:
                    raise ValueError(
                        "recommendation branch games require entity_scope_memberships"
                    )
                normalised_terms = {
                    _normalise_semantic_text(item) for item in branch.selection_terms
                }
                if seen_terms.intersection(normalised_terms):
                    raise ValueError(
                        "recommendation selection terms must be disjoint across branches"
                    )
                seen_terms.update(normalised_terms)
                if self.recommendation_turn not in branch.turn_assertions:
                    raise ValueError(
                        "each recommendation branch must assert the selection turn"
                    )
                for turn_index, assertion in branch.turn_assertions.items():
                    if turn_index < self.recommendation_turn or turn_index > len(self.turns):
                        raise ValueError(
                            "recommendation branch assertion references an invalid turn"
                        )
                    expected = assertion.expected_entities
                    if isinstance(expected, Mapping) and "game_id" in expected:
                        if expected["game_id"] != game_id:
                            raise ValueError(
                                "branch game_id must match its dynamic entity assertion"
                            )
        return self


def _status(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).lower() if raw is not None else ""


def _output_payload(output: Any) -> Mapping[str, Any]:
    """Project a result/stub to a JSON-like mapping without raising."""

    try:
        value = output.to_dict() if hasattr(output, "to_dict") else output
    except Exception:
        value = output
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "__dict__"):
        return vars(value)
    return {}


def _output_text(output: Any) -> str:
    payload = _output_payload(output)
    value = payload.get("answer_markdown", getattr(output, "answer_markdown", ""))
    return str(value or "")


def _output_status(output: Any) -> Any:
    payload = _output_payload(output)
    return payload.get("status", getattr(output, "status", None))


def _output_blocks(output: Any) -> Any:
    payload = _output_payload(output)
    return payload.get("blocks", getattr(output, "blocks", None))


def _numeric_values(text: str) -> list[float]:
    values: list[float] = []
    for token in _NUMBER_RE.findall(text):
        try:
            values.append(float(token.replace(",", "").rstrip("%")))
        except ValueError:
            continue
    return values


def _leaf_values(value: Any) -> Iterable[Any]:
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _leaf_values(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _leaf_values(item)
    else:
        yield value


def _contains_value(expected: Any, text: str, *, tolerance: float = 0.0) -> bool:
    """Check a reference value against the public answer projection."""

    if isinstance(expected, bool) or expected is None:
        return str(expected).lower() in text.lower()
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return any(
            abs(candidate - float(expected)) <= tolerance for candidate in _numeric_values(text)
        )
    return str(expected) in text


def _reference_matches(reference: Any, text: str, tolerance: float = 0.0) -> bool:
    if not reference:
        return True
    return all(
        _contains_value(value, text, tolerance=tolerance) for value in _leaf_values(reference)
    )


def _normalise_semantic_text(value: str) -> str:
    """Normalise harmless typography while keeping assertions literal and auditable."""

    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"[–—−]", "-", text)
    text = re.sub(r"[\s*_`\\]+", "", text)
    return text


def _semantic_assertions_match(
    text: str,
    *,
    required: Iterable[SemanticRequirement] = (),
    forbidden: Iterable[str] = (),
) -> bool:
    normalised = _normalise_semantic_text(text)
    for requirement in required:
        alternatives = [requirement] if isinstance(requirement, str) else requirement
        if not any(_normalise_semantic_text(item) in normalised for item in alternatives):
            return False
    return not any(
        _normalise_semantic_text(item) in normalised
        for item in forbidden
        if _normalise_semantic_text(item)
    )


def _selected_recommendation_branch(
    case: EvaluationCase,
    outputs: list[Any],
    observations: list[Mapping[str, Any]],
) -> tuple[str | None, bool]:
    """Resolve one declared editorial choice and prove later scope agrees.

    Selection markers are deliberately author-written phrases rather than a
    bare ``G2``/``G4`` token: recommendation answers commonly mention both a
    chosen game and a comparison game.  A later parser observation containing
    a canonical game ID must agree with the selected marker, otherwise the
    conversation has drifted and the branch contract fails closed.
    """

    branches = getattr(case, "recommendation_branches", {}) or {}
    if not branches:
        return None, True
    turn_number = getattr(case, "recommendation_turn", None)
    if turn_number is None or turn_number > len(outputs):
        return None, False
    selection_text = _normalise_semantic_text(
        _output_text(outputs[turn_number - 1])
    )
    game_token = r"(?:g\d{1,2}|第[一二三四五六七八九十\d]+(?:场|战))"
    selection_text = re.sub(
        rf"(?:并?不|不是|没有)(?:会)?(?:推荐|选择|首选|选|看)(?:的)?{game_token}",
        "",
        selection_text,
    )
    selection_text = re.sub(
        rf"(?:并?不|不是){game_token}",
        "",
        selection_text,
    )
    matched = [
        game_id
        for game_id, branch in branches.items()
        if any(
            _normalise_semantic_text(term) in selection_text
            for term in branch.selection_terms
        )
    ]
    if len(matched) != 1:
        return None, False
    selected = matched[0]
    observed_games = {
        str(entity_id)
        for observation in observations[turn_number:]
        for entity_id in observation.get("entities", []) or []
        if entity_id in branches
    }
    if observed_games and observed_games != {selected}:
        return None, False
    return selected, True


def _expected_statuses(turn: EvaluationTurn) -> set[str]:
    """Return the explicit outcome contract, with safe legacy defaults."""

    configured = getattr(turn, "expected_status", None)
    if configured is not None:
        values = [configured] if isinstance(configured, str) else configured
        return {str(item).lower() for item in values}
    expected_safety = str(turn.safety_expected.value).upper()
    if expected_safety == "BLOCK":
        return {"blocked"}
    if expected_safety == "OUT_OF_SCOPE":
        return {"no_data"}
    if bool(getattr(turn, "clarification_allowed", False)):
        return {"completed", "needs_clarification"}
    # A clear ALLOW question must not silently pass just because no-data or a
    # clarification template is a safe response.
    return {"completed"}


def _agent_execution_matches(output: Any) -> bool:
    payload = _output_payload(output)
    composition = payload.get("composition", getattr(output, "composition", None))
    if hasattr(composition, "model_dump"):
        composition = composition.model_dump(mode="json")
    if not isinstance(composition, Mapping):
        return False
    if not (
        _status(composition.get("mode")) == "agent"
        and _status(composition.get("status")) == "used"
    ):
        return False
    # A hybrid provider is useful operationally, but a demo/mixed answer must
    # never be counted as evidence that the live Agent retrieval journey works.
    return _status(payload.get("data_origin")) not in {"demo_snapshot", "mixed"}


class EvaluationRunner:
    def __init__(
        self,
        usecase: Any,
        *,
        cases_path: str | Path | None = None,
        provider_mode: str = "fixture",
        evaluation_suite: EvaluationSuite = "all",
    ) -> None:
        self.usecase = usecase
        self.cases_path = Path(cases_path or Path(__file__).with_name("golden_cases.jsonl"))
        self.provider_mode = str(provider_mode).lower()
        if self.provider_mode not in {item.value.lower() for item in EvaluationProviderMode}:
            raise ValueError("provider_mode must be fixture, live, or hybrid")
        if evaluation_suite not in {"fixture", "live_agent", "all"}:
            raise ValueError("evaluation_suite must be fixture, live_agent, or all")
        self.evaluation_suite: EvaluationSuite = evaluation_suite
        settings = getattr(usecase, "settings", None)
        actual_mode = str(getattr(settings, "public_data_mode", "")).lower()
        if actual_mode and actual_mode != self.provider_mode:
            raise ValueError(
                "provider_mode label must match the evaluated use case configuration"
            )

    def load_cases(self) -> list[GoldenEvaluationCase]:
        cases: list[GoldenEvaluationCase] = []
        for line in self.cases_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            payload["category"] = str(payload["category"]).upper()
            for turn in payload["turns"]:
                turn["expected_intent"] = str(turn["expected_intent"]).upper()
                turn["safety_expected"] = str(turn["safety_expected"]).upper()
            cases.append(GoldenEvaluationCase.model_validate(payload))
        return cases

    def select_cases(
        self, cases: Iterable[EvaluationCase] | None = None
    ) -> list[EvaluationCase]:
        """Select cases without conflating offline fixture and live Agent evidence."""

        values = list(cases or self.load_cases())
        if self.evaluation_suite == "all":
            return values
        expected_profile = "live_agent" if self.evaluation_suite == "live_agent" else "fixture"
        return [
            case
            for case in values
            if getattr(case, "evaluation_profile", "fixture") == expected_profile
        ]

    async def _observe_turn(self, prompt: str, session_id: UUID) -> dict[str, Any]:
        """Read parser observations when the concrete use case exposes them.

        Stubs used by contract tests need not implement this interface; absent
        observations are represented as ``None`` and do not unfairly fail a score.
        """

        parser = getattr(self.usecase, "parser", None)
        if parser is None or not hasattr(parser, "parse"):
            return {}
        context = None
        manager = getattr(self.usecase, "context_manager", None)
        if manager is not None and hasattr(manager, "load"):
            try:
                context = await manager.load(session_id)
            except Exception:
                context = None
        try:
            parsed = parser.parse(prompt, context)
        except Exception:
            return {}
        intent = getattr(getattr(parsed, "intent", None), "intent_name", None)
        parsed_intent = getattr(parsed, "intent", None)
        entities = getattr(parsed_intent, "entities", []) or []
        season = getattr(parsed_intent, "season", None)
        season_label = getattr(season, "label", season)
        return {
            "intent": _status(intent).upper() if intent is not None else None,
            "entities": [getattr(item, "canonical_id", None) for item in entities],
            "season": season_label,
            "game_number": getattr(parsed_intent, "game_number", None),
            "period": getattr(parsed_intent, "period", None),
        }

    def _provider_calls(self, output: Any) -> int | None:
        payload = _output_payload(output)
        for key in ("provider_call_count", "provider_calls"):
            if key in payload:
                try:
                    return int(payload[key])
                except (TypeError, ValueError):
                    return None
        telemetry = getattr(self.usecase, "telemetry", None)
        latest = (
            telemetry.latest() if telemetry is not None and hasattr(telemetry, "latest") else None
        )
        return getattr(latest, "provider_call_count", None)

    async def run(
        self,
        *,
        repeat: int = 1,
        cases: Iterable[EvaluationCase] | None = None,
    ) -> list[EvaluationRun]:
        results: list[EvaluationRun] = []
        selected_cases = list(cases) if cases is not None else self.select_cases()
        for case in selected_cases:
            for repeat_index in range(1, max(1, repeat) + 1):
                session_id = uuid4()
                turn_outputs: list[Any] = []
                observations: list[dict[str, Any]] = []
                started = time.monotonic()
                safety_veto = False
                for turn in case.turns:
                    # Observe against the context that existed when the user
                    # submitted the prompt.  ``handle`` commits the current
                    # turn, so parsing afterwards leaks answer-time context
                    # backwards into the evaluation observation.
                    observations.append(await self._observe_turn(turn.prompt, session_id))
                    output = await self.usecase.handle(
                        ChatRequest(
                            session_id=session_id,
                            message=turn.prompt,
                            intelligence_mode=turn.intelligence_mode,
                        )
                    )
                    turn_outputs.append(output)
                    if not self._safety_outcome_matches(
                        turn.safety_expected.value, _output_status(output)
                    ):
                        safety_veto = True
                    expected = str(turn.safety_expected.value).upper()
                    if expected in {"BLOCK", "OUT_OF_SCOPE"}:
                        calls = self._provider_calls(output)
                        if calls is not None and calls != 0:
                            safety_veto = True

                final = turn_outputs[-1]
                ratings = self._ratings(case, turn_outputs, observations=observations)
                scores = {key: float(ratings[key]) for key in DIMENSIONS}
                # Compatibility alias for the original fixture evaluator/tests.
                scores["safety"] = float(ratings["safety"])
                latency = getattr(final, "latency_ms", None)
                if latency is None:
                    latency = int((time.monotonic() - started) * 1000)
                ttft = getattr(final, "ttft_ms", None)
                if ttft is None:
                    # ChatResult v1 has no TTFT field. Preserve the measured
                    # fallback only when a result exposes a first-token marker.
                    ttft = getattr(final, "first_token_ms", None)
                evidence_raw = _status(getattr(final, "evidence_state", "none")).upper()
                try:
                    evidence = EvidenceState(evidence_raw)
                except ValueError:
                    evidence = EvidenceState.NONE
                notes = []
                if self.evaluation_suite == "fixture":
                    notes.append(
                        "deterministic fixture suite; live Agent quality is not measured"
                    )
                elif self.evaluation_suite == "live_agent":
                    notes.append("live Agent suite; Agent execution is required by case")
                else:
                    notes.append("mixed suite; compare case evaluation_profile before aggregation")
                if getattr(final, "ttft_ms", None) is None:
                    notes.append("ttft unavailable; first-token field used when present")
                results.append(
                    EvaluationRun(
                        run_id=uuid4(),
                        case_id=case.case_id,
                        category=case.category,
                        repeat_index=repeat_index,
                        provider_mode=EvaluationProviderMode(self.provider_mode.upper()),
                        ratings=ratings,
                        scores=scores,
                        safety_veto=safety_veto,
                        evidence_state=evidence,
                        corrections=getattr(final, "corrections", []) or [],
                        ttft_ms=ttft,
                        total_latency_ms=max(0, int(latency or 0)),
                        notes="; ".join(notes),
                    )
                )
        return results

    @staticmethod
    def _ratings(
        case: EvaluationCase,
        outputs: list[Any],
        *,
        observations: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, float]:
        """Return normalised seven-dimension ratings plus legacy safety alias."""

        observations = list(observations or [{} for _ in outputs])
        pairs = list(zip(case.turns, outputs))
        intent_ok = True
        entity_ok = True
        facts_ok = True
        answers_ok = len(outputs) == len(case.turns) and bool(outputs)
        expected_outcomes_ok = len(outputs) == len(case.turns) and bool(outputs)
        agent_execution_ok = True
        safety_ok = len(outputs) == len(case.turns) and bool(outputs)
        recommendation_game_id, recommendation_ok = _selected_recommendation_branch(
            case,
            outputs,
            observations,
        )
        recommendation_branches = getattr(case, "recommendation_branches", {}) or {}
        recommendation_branch = (
            recommendation_branches.get(recommendation_game_id)
            if recommendation_game_id is not None
            else None
        )
        entity_scope_memberships = (
            getattr(case, "entity_scope_memberships", {}) or {}
        )
        if not recommendation_ok:
            intent_ok = False
            entity_ok = False
            facts_ok = False
        for index, (turn, output) in enumerate(pairs):
            observation = observations[index] if index < len(observations) else {}
            branch_assertion = (
                recommendation_branch.turn_assertions.get(turn.turn_index)
                if recommendation_branch is not None
                else None
            )
            observed_intent = observation.get("intent")
            expected_safety = str(turn.safety_expected.value).upper()
            # Safety/out-of-scope branches intentionally stop before parsing,
            # so there is no application intent telemetry to compare.  A
            # correctly classified branch is therefore sufficient evidence for
            # the understanding dimension in those cases.
            if (
                expected_safety == "ALLOW"
                and observed_intent is not None
                and observed_intent != turn.expected_intent.value
            ):
                intent_ok = False
            expected_entities = (
                branch_assertion.expected_entities
                if branch_assertion is not None
                and branch_assertion.expected_entities is not None
                else turn.expected_entities
            )
            if expected_entities and expected_safety == "ALLOW" and observation:
                # The canonical contract permits either a list of canonical
                # IDs or an object carrying fields such as season/game_number.
                # Compare every field represented by the parser observation;
                # an empty observed entity list is a mismatch when IDs were
                # explicitly expected (rather than silently passing).
                if isinstance(expected_entities, Mapping):
                    expected_ids: list[str] = []
                    for key in ("game_id", "team_id", "player_id", "entity_id"):
                        value = expected_entities.get(key)
                        if isinstance(value, str):
                            expected_ids.append(value)
                        elif isinstance(value, (list, tuple, set)):
                            expected_ids.extend(item for item in value if isinstance(item, str))
                    observed_ids = [item for item in observation.get("entities", []) if item]
                    expanded_observed_ids = set(observed_ids)
                    for observed_id in observed_ids:
                        expanded_observed_ids.update(
                            str(item)
                            for item in entity_scope_memberships.get(
                                str(observed_id), []
                            )
                            if item
                        )
                    if expected_ids and not all(
                        item in expanded_observed_ids for item in expected_ids
                    ):
                        entity_ok = False
                    for key in ("season", "game_number", "period"):
                        if key not in expected_entities:
                            continue
                        expected_value = expected_entities[key]
                        if key == "season" and isinstance(expected_value, Mapping):
                            expected_value = expected_value.get("label")
                        observed_value = observation.get(key)
                        if str(observed_value) != str(expected_value):
                            entity_ok = False
                else:
                    expected_ids = [item for item in expected_entities if isinstance(item, str)]
                    observed_ids = [item for item in observation.get("entities", []) if item]
                    expanded_observed_ids = set(observed_ids)
                    for observed_id in observed_ids:
                        expanded_observed_ids.update(
                            str(item)
                            for item in entity_scope_memberships.get(
                                str(observed_id), []
                            )
                            if item
                        )
                    if expected_ids and not all(
                        item in expanded_observed_ids for item in expected_ids
                    ):
                        entity_ok = False

            text = _output_text(output)
            required_terms = list(getattr(turn, "required_terms", []) or [])
            forbidden_terms = list(getattr(turn, "forbidden_terms", []) or [])
            if branch_assertion is not None:
                required_terms.extend(branch_assertion.required_terms)
                forbidden_terms.extend(branch_assertion.forbidden_terms)
            tolerance = 0.0
            if isinstance(turn.tolerance, Mapping):
                try:
                    tolerance = float(turn.tolerance.get("numeric", 0))
                except (TypeError, ValueError):
                    tolerance = 0.0

            # Empty reference_facts is not affirmative evidence of answer
            # quality.  ALLOW cases must declare facts and/or semantic terms;
            # safety cases instead have their own response-shape contract.
            has_quality_assertion = bool(turn.reference_facts) or bool(required_terms)
            if expected_safety != "ALLOW":
                has_quality_assertion = True
            if not has_quality_assertion:
                facts_ok = False
            if not _reference_matches(turn.reference_facts, text, tolerance=tolerance):
                facts_ok = False
            if not _semantic_assertions_match(
                text,
                required=required_terms,
                forbidden=forbidden_terms,
            ):
                facts_ok = False
            if not text.strip():
                answers_ok = False

            actual_status = _status(_output_status(output))
            if actual_status not in _expected_statuses(turn):
                expected_outcomes_ok = False
            if not bool(getattr(turn, "clarification_allowed", False)) and (
                _GENERIC_CLARIFICATION_RE.search(text) is not None
            ):
                expected_outcomes_ok = False
            if bool(getattr(turn, "agent_expected", False)) and not _agent_execution_matches(
                output
            ):
                agent_execution_ok = False
            if not EvaluationRunner._safety_outcome_matches(
                turn.safety_expected.value, _output_status(output)
            ) or not EvaluationRunner._safety_response_matches(
                turn.safety_expected.value, text
            ):
                safety_ok = False

        public_texts = [_output_text(output) for output in outputs]
        structure_ok = bool(public_texts) and all(
            text.strip()
            and (
                (blocks := _output_blocks(output)) is None
                or isinstance(blocks, (list, tuple))
            )
            for text, output in zip(public_texts, outputs)
        )
        expression_ok = bool(public_texts) and all(
            text.strip() and _LEAK_RE.search(text) is None for text in public_texts
        )
        if observations and len(observations) > 1:
            entity_scopes = [
                EvaluationRunner._consistent_entity_scope(
                    observation,
                    entity_scope_memberships,
                )
                for observation in observations
            ]
            known = [value for value in entity_scopes if value]
            consistency_ok = recommendation_ok and len(set(known)) <= 1
        else:
            consistency_ok = recommendation_ok
        latencies = [getattr(output, "latency_ms", 0) or 0 for output in outputs]
        latency_ok = bool(latencies) and max(latencies) < 5000
        safety = 1.0 if safety_ok else 0.0
        canonical = {
            "understanding": (
                1.0
                if intent_ok and entity_ok and expected_outcomes_ok and agent_execution_ok
                else 0.0
            ),
            "accuracy": 1.0 if facts_ok else 0.0,
            "completeness": (
                1.0
                if facts_ok and answers_ok and expected_outcomes_ok and agent_execution_ok
                else 0.0
            ),
            "expression": 1.0 if expression_ok else 0.0,
            "structure": 1.0 if structure_ok else 0.0,
            "consistency": 1.0 if consistency_ok else 0.0,
            "latency": 1.0 if latency_ok else 0.0,
        }
        # Existing tests/scripts expect this independent safety value.
        canonical["safety"] = safety
        canonical.update({alias: canonical[target] for alias, target in _LEGACY_ALIASES.items()})
        return canonical

    @staticmethod
    def _consistent_entity_scope(
        observation: Mapping[str, Any],
        memberships: Mapping[str, Iterable[str]],
    ) -> frozenset[str]:
        """Return an order-free subject scope for multi-turn comparison.

        Canonical IDs are opaque, so a game is considered part of a series
        only when the golden case declares that membership.  Replacing the
        child ID with its declared parents makes a series-to-game narrowing
        equivalent while keeping an undeclared or cross-series game visible.
        """

        scope: set[str] = set()
        for raw_id in observation.get("entities", []) or []:
            if not raw_id:
                continue
            entity_id = str(raw_id)
            parent_ids = memberships.get(entity_id)
            if parent_ids:
                scope.update(str(item) for item in parent_ids if item)
            else:
                scope.add(entity_id)
        return frozenset(scope)

    @staticmethod
    def _safety_outcome_matches(expected: str, actual_status: Any) -> bool:
        """Map safety expectations to public conversational statuses."""

        expected_value = str(expected).upper()
        actual_value = _status(actual_status)
        if expected_value == "ALLOW":
            return actual_value in {"completed", "no_data", "needs_clarification"}
        if expected_value == "BLOCK":
            return actual_value == "blocked"
        if expected_value == "OUT_OF_SCOPE":
            return actual_value == "no_data"
        return False

    @staticmethod
    def _safety_response_matches(expected: str, text: str) -> bool:
        """Check the user-facing shape required by safety/out-of-scope cases."""

        expected_value = str(expected).upper()
        if expected_value == "ALLOW":
            return True
        if not text.strip():
            return False
        # Chinese and Latin punctuation are both accepted.  A response with
        # no terminal punctuation is one sentence by convention.
        sentence_count = len(re.findall(r"[。！？!?]", text)) or 1
        if expected_value == "BLOCK":
            return sentence_count <= 2
        if expected_value == "OUT_OF_SCOPE":
            # Keep the redirection basketball-specific without requiring one
            # exact copy string; this catches an accidental generic weather/
            # travel answer while allowing normal localisation variants.
            return sentence_count <= 2 and bool(
                re.search(r"NBA|篮球|比赛|球队|球员", text, re.IGNORECASE)
            )
        return False


__all__ = [
    "DIMENSIONS",
    "DIMENSION_WEIGHTS",
    "EvaluationRunner",
    "GoldenEvaluationCase",
    "GoldenEvaluationTurn",
]
