"""Command-line entry point; errors are safe, concise and machine-detectable."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import PipelineError
from .pipeline import run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a quality-gated Customer360 snapshot")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Extract, validate, transform and publish a snapshot")
    run.add_argument("--output", type=Path, required=True, help="Artifact directory")
    run.add_argument("--fixture", type=Path, help="Use a local deterministic JSON source")
    run.add_argument("--base-url", default="https://dummyjson.com")
    run.add_argument("--page-size", type=int, default=50)
    args = parser.parse_args(argv)
    try:
        summary = run_pipeline(args.output, args.fixture, args.base_url, args.page_size)
    except (PipelineError, ValueError) as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
