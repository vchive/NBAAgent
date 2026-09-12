"""Command-line entry point for isolated fixture and live-Agent evaluations."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from pathlib import Path

from apps.api.src.config import Settings
from apps.api.src.evaluation.report import to_markdown, write_report
from apps.api.src.evaluation.runner import EvaluationRunner
from apps.api.src.main import create_app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run NBA Agent golden cases")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case (default: 1)")
    parser.add_argument(
        "--provider-mode",
        choices=("fixture", "live", "hybrid"),
        default="fixture",
        help="configure and record the provider profile used by the evaluated app",
    )
    parser.add_argument(
        "--suite",
        choices=("fixture", "live-agent", "all"),
        default="fixture",
        help=(
            "fixture runs deterministic offline cases; live-agent runs only cases that "
            "require a proven Agent response"
        ),
    )
    parser.add_argument("--json", dest="json_path", type=Path, help="write JSON report")
    parser.add_argument("--markdown", dest="markdown_path", type=Path, help="write Markdown report")
    return parser


async def run_evaluation(args: argparse.Namespace) -> dict:
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1")
    suite = str(args.suite).replace("-", "_")
    if suite == "fixture" and args.provider_mode != "fixture":
        raise ValueError("the fixture suite requires --provider-mode fixture")
    if suite == "live_agent" and args.provider_mode not in {"live", "hybrid"}:
        raise ValueError("the live-agent suite requires --provider-mode live or hybrid")
    settings = replace(Settings.from_env(), public_data_mode=args.provider_mode)
    if suite == "live_agent" and not (
        settings.full_intelligence_enabled
        and str(settings.hermes_lite_mode).lower() == "embedded_agent"
    ):
        raise ValueError(
            "the live-agent suite requires full intelligence and an enabled assistant runtime"
        )
    app = create_app(settings=settings)
    runner = EvaluationRunner(
        app.state.chat_use_case,
        provider_mode=args.provider_mode,
        evaluation_suite=suite,
    )
    cases = runner.select_cases()
    if not cases:
        raise ValueError(f"no golden cases are assigned to the {args.suite} suite")
    runs = await runner.run(repeat=args.repeat, cases=cases)
    summary = write_report(
        runs,
        json_path=str(args.json_path) if args.json_path else None,
        markdown_path=str(args.markdown_path) if args.markdown_path else None,
    )
    print(to_markdown(summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = asyncio.run(run_evaluation(args))
    except ValueError as exc:
        _parser().error(str(exc))
    return 1 if int(summary.get("quality_gate_failures", 0)) > 0 else 0


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI smoke path
    raise SystemExit(main())


__all__ = ["main", "run_evaluation"]
