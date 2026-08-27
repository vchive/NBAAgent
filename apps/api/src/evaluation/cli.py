"""Command-line entry point for the deterministic fixture evaluation."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

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
        help="evaluation metadata label",
    )
    parser.add_argument("--json", dest="json_path", type=Path, help="write JSON report")
    parser.add_argument("--markdown", dest="markdown_path", type=Path, help="write Markdown report")
    return parser


async def run_evaluation(args: argparse.Namespace) -> dict:
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1")
    app = create_app()
    runner = EvaluationRunner(
        app.state.chat_use_case,
        provider_mode=args.provider_mode,
    )
    runs = await runner.run(repeat=args.repeat)
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
        asyncio.run(run_evaluation(args))
    except ValueError as exc:
        _parser().error(str(exc))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI smoke path
    raise SystemExit(main())


__all__ = ["main", "run_evaluation"]
