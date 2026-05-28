"""Defragmentation utilities for ArcticDB libraries.

Each ``update(date_range=...)`` creates a new data segment. Over time, many
small segments degrade read performance. This module exposes helpers to check
fragmentation and compact segments using ArcticDB's ``defragment_symbol_data``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog

from fin3.storage.arctic import ArcticStorage

logger = structlog.get_logger(__name__)


@dataclass
class SymbolDefragResult:
    """Per-symbol defragmentation outcome."""

    symbol: str
    was_fragmented: bool
    segments_before: int
    segments_after: int


@dataclass
class DefragReport:
    """Aggregated result of a defragmentation pass over a library."""

    library: str
    results: list[SymbolDefragResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def defragmented_count(self) -> int:
        return sum(1 for r in self.results if r.was_fragmented)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self.results if not r.was_fragmented)


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
        Report with ``was_fragmented`` populated per symbol.
    """
    if symbols is None:
        symbols = storage.list_symbols(library)

    results: list[SymbolDefragResult] = []
    for symbol in symbols:
        seg_count = storage.get_segment_count(library, symbol)
        # Access the underlying ArcticDB library to check fragmentation
        lib = storage._get_or_create_library(library)
        try:
            fragmented = lib.is_symbol_fragmented(symbol)
        except Exception:
            fragmented = False
        results.append(
            SymbolDefragResult(
                symbol=symbol,
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
        Per-symbol results with before/after segment counts.
    """
    start = time.monotonic()

    if symbols is None:
        symbols = storage.list_symbols(library)

    lib = storage._get_or_create_library(library)
    results: list[SymbolDefragResult] = []

    for symbol in symbols:
        seg_before = storage.get_segment_count(library, symbol)
        was_fragmented = False

        try:
            was_fragmented = lib.is_symbol_fragmented(symbol)
        except Exception:
            pass

        if was_fragmented and not dry_run:
            try:
                lib.defragment_symbol_data(
                    symbol,
                    segment_size=segment_size,
                    prune_previous_versions=prune_previous_versions,
                )
                logger.info(
                    "defrag.symbol_compacted",
                    library=library,
                    symbol=symbol,
                    segments_before=seg_before,
                )
            except Exception as exc:
                logger.warning(
                    "defrag.symbol_failed",
                    library=library,
                    symbol=symbol,
                    error=str(exc),
                )

        seg_after = storage.get_segment_count(library, symbol) if was_fragmented and not dry_run else seg_before

        results.append(
            SymbolDefragResult(
                symbol=symbol,
                was_fragmented=was_fragmented,
                segments_before=seg_before,
                segments_after=seg_after,
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
        skipped=report.skipped_count,
        elapsed_seconds=report.elapsed_seconds,
    )

    return report
