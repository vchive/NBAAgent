"""Repeatable golden-question evaluator for the fixture profile.

The evaluator is intentionally provider/runtime agnostic.  It consumes the public
``ChatResult`` envelope (and, when available, the use-case parser/telemetry) and
produces the seven dimensions from the brief.  Scoring is deterministic so a
repeated fixture run can be compared without a model in the loop.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from apps.api.src.api.schemas import ChatRequest
from apps.api.src.domain.models import (
    EvaluationCase,
    EvaluationProviderMode,
    EvaluationRun,
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
    r"https?://|www\.|source_ref|evidence_ids?|canonical_id|provider_call|"
    r"raw_(?:json|response)|trace_id|session_id|request_id|api[_ -]?key|bearer\s+",
    re.IGNORECASE,
)


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


class EvaluationRunner:
    def __init__(
        self,
        usecase: Any,
        *,
        cases_path: str | Path | None = None,
        provider_mode: str = "fixture",
    ) -> None:
        self.usecase = usecase
        self.cases_path = Path(cases_path or Path(__file__).with_name("golden_cases.jsonl"))
        self.provider_mode = provider_mode

    def load_cases(self) -> list[EvaluationCase]:
        cases: list[EvaluationCase] = []
        for line in self.cases_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            payload["category"] = str(payload["category"]).upper()
            for turn in payload["turns"]:
                turn["expected_intent"] = str(turn["expected_intent"]).upper()
                turn["safety_expected"] = str(turn["safety_expected"]).upper()
            cases.append(EvaluationCase.model_validate(payload))
        return cases

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
        for case in list(cases or self.load_cases()):
            for repeat_index in range(1, max(1, repeat) + 1):
                session_id = uuid4()
                turn_outputs: list[Any] = []
                observations: list[dict[str, Any]] = []
                started = time.monotonic()
                safety_veto = False
                for turn in case.turns:
                    output = await self.usecase.handle(
                        ChatRequest(
                            session_id=session_id,
                            message=turn.prompt,
                            intelligence_mode=turn.intelligence_mode,
                        )
                    )
                    turn_outputs.append(output)
                    observations.append(await self._observe_turn(turn.prompt, session_id))
                    if not self._safety_outcome_matches(
                        turn.safety_expected.value, getattr(output, "status", None)
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
                        notes=(
                            "ttft 未由 ChatResult 提供，使用首 token 字段或留空"
                            if getattr(final, "ttft_ms", None) is None
                            else None
                        ),
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
        answers_ok = True
        statuses_ok = True
        for index, (turn, output) in enumerate(pairs):
            observation = observations[index] if index < len(observations) else {}
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
            expected_entities = turn.expected_entities
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
                    if expected_ids and not all(item in observed_ids for item in expected_ids):
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
                    if expected_ids and not all(item in observed_ids for item in expected_ids):
                        entity_ok = False
            text = _output_text(output)
            tolerance = 0.0
            if isinstance(turn.tolerance, Mapping):
                try:
                    tolerance = float(turn.tolerance.get("numeric", 0))
                except (TypeError, ValueError):
                    tolerance = 0.0
            if not _reference_matches(turn.reference_facts, text, tolerance=tolerance):
                facts_ok = False
            if not text.strip():
                answers_ok = False
            if not EvaluationRunner._safety_outcome_matches(
                turn.safety_expected.value, getattr(output, "status", None)
            ) or not EvaluationRunner._safety_response_matches(
                turn.safety_expected.value, _output_text(output)
            ):
                statuses_ok = False

        final = outputs[-1] if outputs else None
        final_text = _output_text(final) if final is not None else ""
        blocks = _output_blocks(final) if final is not None else None
        structure_ok = bool(final_text.strip()) and (
            blocks is None or isinstance(blocks, (list, tuple))
        )
        expression_ok = bool(final_text.strip()) and _LEAK_RE.search(final_text) is None
        if observations and len(observations) > 1:
            game_sets = [
                tuple(item for item in observation.get("entities", []) if item)
                for observation in observations
            ]
            known = [value for value in game_sets if value]
            consistency_ok = len(set(known)) <= 1
        else:
            consistency_ok = True
        latencies = [getattr(output, "latency_ms", 0) or 0 for output in outputs]
        latency_ok = bool(latencies) and max(latencies) < 5000
        safety = 1.0 if statuses_ok else 0.0
        canonical = {
            "understanding": 1.0 if intent_ok and entity_ok else 0.0,
            "accuracy": 1.0 if facts_ok else 0.0,
            "completeness": 1.0 if facts_ok and answers_ok else 0.0,
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


__all__ = ["DIMENSIONS", "DIMENSION_WEIGHTS", "EvaluationRunner"]
