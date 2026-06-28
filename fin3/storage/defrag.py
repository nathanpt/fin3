"""Defragmentation utilities for ArcticDB libraries.

Each ``update(date_range=...)`` creates a new data segment. Over time, many
small segments degrade read performance. This module exposes helpers to check
fragmentation and compact segments using ArcticDB's ``defragment_symbol_data``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

import structlog

from fin3.storage.arctic import ArcticStorage

logger = structlog.get_logger(__name__)

DefragStatus = Literal["ok", "would_defrag", "defragmented", "failed", "missing"]


@dataclass
class SymbolDefragResult:
    """Per-symbol defragmentation outcome."""

    symbol: str
    status: DefragStatus
    was_fragmented: bool
    segments_before: int
    segments_after: int
    error: str | None = None

    @property
    def changed(self) -> bool:
        """Return True when the symbol was actually compacted."""
        return self.status == "defragmented"


@dataclass
class DefragReport:
    """Aggregated result of a defragmentation pass over a library."""

    library: str
    results: list[SymbolDefragResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def defragmented_count(self) -> int:
        """Number of symbols that were successfully defragmented."""
        return sum(1 for r in self.results if r.status == "defragmented")

    @property
    def would_defrag_count(self) -> int:
        """Number of symbols that would be defragmented (dry-run mode)."""
        return sum(1 for r in self.results if r.status == "would_defrag")

    @property
    def failed_count(self) -> int:
        """Number of symbols where defragmentation failed."""
        return sum(1 for r in self.results if r.status == "failed")

    @property
    def skipped_count(self) -> int:
        """Number of symbols that were already defragmented (no action needed)."""
        return sum(1 for r in self.results if r.status == "ok")

    @property
    def missing_count(self) -> int:
        """Number of symbols that were not found in the library."""
        return sum(1 for r in self.results if r.status == "missing")


def get_fragmentation_info(
    storage: ArcticStorage,
    library: str,
    symbols: list[str] | None = None,
) -> DefragReport:
    """Check fragmentation status for symbols in a library (non-mutating).

    Parameters
    ----------
    storage : ArcticStorage
        Storage backend.
    library : str
        Library/bucket name.
    symbols : list[str] or None
        Symbols to check. None checks all symbols in the library.

    Returns
    -------
    DefragReport
        Report with per-symbol fragmentation status.
    """
    if symbols is None:
        symbols = storage.list_symbols(library)

    results: list[SymbolDefragResult] = []
    for symbol in symbols:
        seg_count = storage.get_segment_count(library, symbol)
        fragmented = storage.is_symbol_fragmented(library, symbol)
        status = _status_for_fragmentation(fragmented, dry_run=True)
        results.append(
            SymbolDefragResult(
                symbol=symbol,
                status=status,
                was_fragmented=fragmented,
                segments_before=seg_count,
                segments_after=seg_count,
            )
        )

    return DefragReport(library=library, results=results)


def defragment_library(
    storage: ArcticStorage,
    library: str,
    symbols: list[str] | None = None,
    *,
    prune_previous_versions: bool = True,
    segment_size: int | None = None,
    dry_run: bool = False,
) -> DefragReport:
    """Defragment symbols in an ArcticDB library.

    Parameters
    ----------
    storage : ArcticStorage
        Storage backend.
    library : str
        Library/bucket name.
    symbols : list[str] or None
        Symbols to defragment. None defragments all symbols.
    prune_previous_versions : bool
        Remove old versions after compaction.
    segment_size : int or None
        Target max rows per segment after compaction. None uses the library default.
    dry_run : bool
        If True, only report fragmentation without performing defragmentation.

    Returns
    -------
    DefragReport
        Per-symbol results with before/after segment counts and status.
    """
    start = time.monotonic()

    if symbols is None:
        symbols = storage.list_symbols(library)

    results: list[SymbolDefragResult] = []

    for symbol in symbols:
        seg_before = storage.get_segment_count(library, symbol)
        was_fragmented = storage.is_symbol_fragmented(library, symbol)
        status = _status_for_fragmentation(was_fragmented, dry_run=dry_run)
        error: str | None = None
        seg_after = seg_before

        if was_fragmented and not dry_run:
            try:
                storage.defragment_symbol(
                    library,
                    symbol,
                    segment_size=segment_size,
                    prune_previous_versions=prune_previous_versions,
                )
                status = "defragmented"
                seg_after = storage.get_segment_count(library, symbol)
                logger.info(
                    "defrag.symbol_compacted",
                    library=library,
                    symbol=symbol,
                    segments_before=seg_before,
                    segments_after=seg_after,
                )
            except Exception as exc:
                status = "failed"
                error = str(exc)
                seg_after = storage.get_segment_count(library, symbol)
                logger.warning(
                    "defrag.symbol_failed",
                    library=library,
                    symbol=symbol,
                    error=error,
                )

        results.append(
            SymbolDefragResult(
                symbol=symbol,
                status=status,
                was_fragmented=was_fragmented,
                segments_before=seg_before,
                segments_after=seg_after,
                error=error,
            )
        )

    elapsed = time.monotonic() - start
    report = DefragReport(
        library=library,
        results=results,
        elapsed_seconds=round(elapsed, 3),
    )

    logger.info(
        "defrag.complete",
        library=library,
        defragmented=report.defragmented_count,
        would_defrag=report.would_defrag_count,
        skipped=report.skipped_count,
        failed=report.failed_count,
        elapsed_seconds=report.elapsed_seconds,
    )

    return report


def _status_for_fragmentation(fragmented: bool, *, dry_run: bool) -> DefragStatus:
    if not fragmented:
        return "ok"
    if dry_run:
        return "would_defrag"
    return "defragmented"
