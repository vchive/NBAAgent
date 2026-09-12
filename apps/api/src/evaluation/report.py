"""JSON/Markdown report helpers for evaluation runs.

The public report follows the seven weighted dimensions in the brief while
remaining tolerant of older ``EvaluationRun`` objects produced by the initial
fixture slice.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from .runner import DIMENSION_WEIGHTS, DIMENSIONS

_LEGACY_ALIASES = {
    "factual_correctness": "accuracy",
    "time_accuracy": "latency",
    "style": "expression",
}


def _scores(run: Any) -> Mapping[str, Any]:
    values = getattr(run, "scores", None)
    if isinstance(values, Mapping):
        return values
    values = getattr(run, "ratings", None)
    return values if isinstance(values, Mapping) else {}


def _dimension_value(run: Any, dimension: str) -> float:
    values = _scores(run)
    value = values.get(dimension)
    if value is None:
        for legacy, canonical in _LEGACY_ALIASES.items():
            if canonical == dimension and legacy in values:
                value = values[legacy]
                break
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def weighted_case_score(run: Any) -> float:
    """Return one case's weighted score on a 0..100 scale.

    The PDF's independent veto applies when safety is wrong or the two core
    dimensions (understanding/accuracy) are zero.  ``safety_veto`` is checked
    explicitly so callers can record the reason without mutating dimensions.
    """

    veto = bool(getattr(run, "safety_veto", False))
    if (
        veto
        or _dimension_value(run, "understanding") <= 0
        or _dimension_value(run, "accuracy") <= 0
    ):
        return 0.0
    return 100.0 * sum(
        _dimension_value(run, dimension) * weight for dimension, weight in DIMENSION_WEIGHTS.items()
    )


def _public_run(run: Any) -> dict[str, Any]:
    provider_mode = getattr(run, "provider_mode", None)
    provider_mode = getattr(provider_mode, "value", provider_mode)
    run_id = getattr(run, "run_id", None)
    category = getattr(run, "category", None)
    category = getattr(category, "value", category)
    corrections = getattr(run, "corrections", []) or []
    public_corrections: list[Any] = []
    for correction in corrections:
        if hasattr(correction, "model_dump"):
            correction = correction.model_dump(mode="json")
        if isinstance(correction, Mapping):
            # PublicCorrection already contains only localised message/status;
            # project unknown mapping keys away in case an old run persisted
            # internal correction metadata.
            public_corrections.append(
                {key: correction[key] for key in ("status", "message") if key in correction}
            )
    notes = getattr(run, "notes", None)
    if isinstance(notes, str) and notes.startswith("deterministic fixture suite"):
        evaluation_suite = "fixture"
    elif isinstance(notes, str) and notes.startswith("live Agent suite"):
        evaluation_suite = "live_agent"
    else:
        evaluation_suite = "mixed"
    public_scores = {
        dimension: round(_dimension_value(run, dimension), 4) for dimension in DIMENSIONS
    }
    failed_dimensions = [
        dimension for dimension, value in public_scores.items() if value <= 0
    ]
    return {
        "run_id": str(run_id) if run_id is not None else None,
        "case_id": getattr(run, "case_id", None),
        "category": str(category).upper() if category is not None else None,
        "repeat_index": getattr(run, "repeat_index", None),
        "provider_mode": str(provider_mode).lower() if provider_mode is not None else None,
        "evaluation_suite": evaluation_suite,
        "scores": public_scores,
        "failed_dimensions": failed_dimensions,
        "weighted_score": round(weighted_case_score(run), 4),
        "safety_veto": bool(getattr(run, "safety_veto", False)),
        "evidence_state": getattr(
            getattr(run, "evidence_state", None),
            "value",
            getattr(run, "evidence_state", None),
        ),
        "ttft_ms": getattr(run, "ttft_ms", None),
        "total_latency_ms": getattr(run, "total_latency_ms", None),
        "corrections": public_corrections,
        "notes": notes,
    }


def summarise(runs: Iterable[Any]) -> dict[str, Any]:
    values = list(runs)
    means = {
        dimension: round(
            sum(_dimension_value(run, dimension) for run in values) / len(values),
            4,
        )
        if values
        else 0.0
        for dimension in DIMENSIONS
    }
    case_scores = [weighted_case_score(run) for run in values]
    ttfts = [
        int(getattr(run, "ttft_ms", 0) or 0)
        for run in values
        if getattr(run, "ttft_ms", None) is not None
    ]
    overall = round(sum(case_scores) / len(case_scores), 4) if case_scores else 0.0
    quality_gate_failures = sum(1 for score in case_scores if score <= 0)
    # Keep ``mean_scores`` as the stable top-level field used by the first
    # evaluator, and add explicit weighted/逐次 details for the PDF report.
    public_cases = [_public_run(run) for run in values]
    suite_counts: dict[str, int] = {}
    for case in public_cases:
        suite = str(case.get("evaluation_suite", "mixed"))
        suite_counts[suite] = suite_counts.get(suite, 0) + 1
    return {
        "runs": len(values),
        "mean_scores": means,
        "dimension_weights": dict(DIMENSION_WEIGHTS),
        "weighted_score": overall,
        "overall_score": overall,
        "safety_vetoes": sum(1 for run in values if bool(getattr(run, "safety_veto", False))),
        "quality_gate_failures": quality_gate_failures,
        "latency_ms": {
            "p50": _percentile(
                [int(getattr(run, "total_latency_ms", 0) or 0) for run in values],
                0.5,
            ),
            "p90": _percentile(
                [int(getattr(run, "total_latency_ms", 0) or 0) for run in values],
                0.9,
            ),
        },
        "ttft_ms": {
            "p50": _percentile(ttfts, 0.5),
            "p90": _percentile(ttfts, 0.9),
        },
        "evaluation_suites": suite_counts,
        "cases": public_cases,
    }


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * quantile))))
    return int(values[index])


def to_markdown(summary: Mapping[str, Any]) -> str:
    overall = summary.get("weighted_score", summary.get("overall_score", 0))
    lines = [
        "# NBA Agent Evaluation",
        "",
        f"Runs: {summary.get('runs', 0)}",
        f"Weighted score (0–100): {float(overall):.2f}",
        f"Execution suites: {summary.get('evaluation_suites', {})}",
        "",
        "| Dimension | Weight | Mean score |",
        "|---|---:|---:|",
    ]
    means = summary.get("mean_scores", {}) or {}
    weights = summary.get("dimension_weights", DIMENSION_WEIGHTS) or DIMENSION_WEIGHTS
    for dimension in DIMENSIONS:
        weight = float(weights.get(dimension, 0))
        mean = float(means.get(dimension, 0))
        lines.append(f"| {dimension} | {weight:.0%} | {mean:.3f} |")
    latency = summary.get("latency_ms", {}) or {}
    ttft = summary.get("ttft_ms", {}) or {}
    lines.extend(
        [
            "",
            f"Safety vetoes: {summary.get('safety_vetoes', 0)}",
            f"Core quality gate failures: {summary.get('quality_gate_failures', 0)}",
            f"P50/P90 latency: {latency.get('p50', 0)} / {latency.get('p90', 0)} ms",
            f"P50/P90 TTFT: {ttft.get('p50', 0)} / {ttft.get('p90', 0)} ms",
        ]
    )
    cases = summary.get("cases", []) or []
    if cases:
        lines.extend(
            [
                "",
                "| Case | Suite | Provider | Repeat | Score | Failed dimensions | Veto |",
                "|---|---|---|---:|---:|---|:---:|",
            ]
        )
        for case in cases:
            case_id = case.get("case_id", "")
            suite = case.get("evaluation_suite", "")
            provider = case.get("provider_mode", "")
            repeat_index = case.get("repeat_index", "")
            score = float(case.get("weighted_score", 0))
            failed = ", ".join(case.get("failed_dimensions", []) or []) or "—"
            veto = "yes" if case.get("safety_veto") else "no"
            lines.append(
                f"| {case_id} | {suite} | {provider} | {repeat_index} | "
                f"{score:.2f} | {failed} | {veto} |"
            )
    return "\n".join(lines)


def write_report(
    runs: Iterable[Any],
    *,
    json_path: str | None = None,
    markdown_path: str | None = None,
) -> dict[str, Any]:
    summary = summarise(runs)
    if json_path:
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
    if markdown_path:
        with open(markdown_path, "w", encoding="utf-8") as handle:
            handle.write(to_markdown(summary))
    return summary


__all__ = [
    "DIMENSIONS",
    "DIMENSION_WEIGHTS",
    "summarise",
    "to_markdown",
    "weighted_case_score",
    "write_report",
]
