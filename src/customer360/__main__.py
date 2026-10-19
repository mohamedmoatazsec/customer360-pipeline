"""Command-line entry point; errors are safe, concise and machine-detectable."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from .errors import PipelineError


def _run(args: argparse.Namespace) -> dict[str, Any]:
    from .pipeline import run_pipeline

    return run_pipeline(args.output, args.fixture, args.base_url, args.page_size)


def _load_bronze(args: argparse.Namespace) -> dict[str, list[dict[str, Any]]]:
    from .contracts import sanitize

    if args.fixture is not None:
        try:
            with Path(args.fixture).open(encoding="utf-8") as handle:
                payload = json.load(handle, parse_float=Decimal)
        except (OSError, ValueError):
            raise PipelineError("Fixture cannot be read as valid UTF-8 JSON") from None
        if not isinstance(payload, dict):
            raise PipelineError("Fixture must contain a JSON object")
    else:
        from .source import extract_live

        payload = extract_live(args.base_url, args.page_size)
    return sanitize(payload)


def _report(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from . import pandas_transform, reporting
    except ImportError:
        raise PipelineError(
            "Reporting needs the analytics extras: pip install -e '.[analytics]'"
        ) from None
    bronze = _load_bronze(args)
    gold = pandas_transform.transform(bronze)
    return reporting.build_report(bronze, gold, args.output)


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, required=True, help="Artifact directory")
    parser.add_argument("--fixture", type=Path, help="Use a local deterministic JSON source")
    parser.add_argument("--base-url", default="https://dummyjson.com")
    parser.add_argument("--page-size", type=int, default=50)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a quality-gated Customer360 snapshot")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_source_arguments(
        commands.add_parser("run", help="Extract, validate, transform and publish a snapshot")
    )
    _add_source_arguments(
        commands.add_parser(
            "report",
            help="Build an Excel dashboard, category chart and Power BI export (pandas engine)",
        )
    )
    args = parser.parse_args(argv)
    try:
        summary = _run(args) if args.command == "run" else _report(args)
    except (PipelineError, ValueError) as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
