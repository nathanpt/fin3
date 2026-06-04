"""Defragment an ArcticDB library to compact data segments.

Usage:
    uv run python scripts/defragment_library.py <library> [--symbols AAPL,TSLA] [--dry-run] [--segment-size 100000]

Examples:
    uv run python scripts/defragment_library.py equities-1m-databento
    uv run python scripts/defragment_library.py equities-1d-databento --symbols AAPL,TSLA --dry-run
"""

from __future__ import annotations

import argparse

from fin3.config.settings import ClientConfig
from fin3.storage.arctic import ArcticStorage
from fin3.storage.defrag import DefragReport, defragment_library


def format_report(report: DefragReport) -> str:
    """Format a defragmentation report for terminal output."""
    lines = [
        f"Results for {report.library}:",
        f"  Defragmented: {report.defragmented_count}",
        f"  Would defrag: {report.would_defrag_count}",
        f"  Skipped:      {report.skipped_count}",
        f"  Failed:       {report.failed_count}",
        f"  Elapsed:      {report.elapsed_seconds:.2f}s",
    ]

    if report.results:
        lines.append("")
        lines.append("Per-symbol detail:")
        for result in report.results:
            detail = (
                f"  {result.symbol:10s}  "
                f"segments_before={result.segments_before:<4d}  "
                f"segments_after={result.segments_after:<4d}  "
                f"status={result.status}"
            )
            if result.error is not None:
                detail = f"{detail}  error={result.error}"
            lines.append(detail)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Defragment an ArcticDB library to compact data segments"
    )
    parser.add_argument("library", help="ArcticDB library name")
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated list of symbols (default: all symbols in library)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report fragmentation without performing defragmentation",
    )
    parser.add_argument(
        "--segment-size",
        type=int,
        default=None,
        help="Target max rows per segment after compaction (default: library setting)",
    )
    args = parser.parse_args()

    symbols = args.symbols.split(",") if args.symbols else None

    config = ClientConfig()
    storage = ArcticStorage(config.minio)

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Defragmenting {args.library}...")
    report = defragment_library(
        storage,
        args.library,
        symbols=symbols,
        dry_run=args.dry_run,
        segment_size=args.segment_size,
    )

    print()
    print(format_report(report))

    if report.failed_count > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
