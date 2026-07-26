"""Finalize a machine-generated official-source review with human decisions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from danish_rag.source_review import (
    SourceReviewError,
    write_completed_source_review_bundle,
)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate hash-bound human decisions and create a durable completed "
            "official-source review bundle."
        )
    )
    parser.add_argument("--machine-bundle", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the completed official-source review bundle CLI."""

    args = _argument_parser().parse_args(argv)
    try:
        write_completed_source_review_bundle(
            machine_bundle_dir=args.machine_bundle,
            decisions_path=args.decisions,
            output_dir=args.output,
        )
    except SourceReviewError as exc:
        print(f"completed source-review bundle error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote completed source-review bundle to {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
