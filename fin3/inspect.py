"""Library inspection utilities for data visibility and health diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd
import structlog

from fin3.schemas import Resolution
from fin3.storage.arctic import ArcticStorage

if TYPE_CHECKING:
    from fin3.calendar.exchange import CalendarStrategy

from fin3.utils.integrity import check_integrity

logger = structlog.get_logger(__name__)


@dataclass
class SymbolProfile:
    """Per-symbol summary of stored data."""

    symbol: str
    first_bar: pd.Timestamp | None
    last_bar: pd.Timestamp | None
    total_bars: int
    null_bars: int
    total_volume: float
    size_bytes: int
    issue_count: int
    issues_summary: dict[str, int]


@dataclass
class LibraryOverview:
    """Aggregated overview of a single library (bucket)."""

    library: str
    symbol_count: int
    total_rows: int
    date_range: tuple[pd.Timestamp | None, pd.Timestamp | None]
    symbols: list[SymbolProfile]

    def to_dataframe(self) -> pd.DataFrame:
        """Return a DataFrame with one row per symbol for easy sorting/filtering."""
        rows = []
        for s in self.symbols:
            rows.append(
                {
                    "symbol": s.symbol,
                    "first_bar": s.first_bar,
                    "last_bar": s.last_bar,
                    "total_bars": s.total_bars,
                    "null_bars": s.null_bars,
                    "data_bars": s.total_bars - s.null_bars,
                    "total_volume": s.total_volume,
                    "size_bytes": s.size_bytes,
                    "issue_count": s.issue_count,
                }
            )
        return pd.DataFrame(rows)

    def summary(self) -> dict[str, object]:
        """Return top-level stats as a plain dict."""
        with_issues = sum(1 for s in self.symbols if s.issue_count > 0)
        total_size = sum(s.size_bytes for s in self.symbols)
        return {
            "library": self.library,
            "symbol_count": self.symbol_count,
            "total_rows": self.total_rows,
            "total_size_bytes": total_size,
            "date_range": self.date_range,
            "symbols_with_issues": with_issues,
        }


def inspect_library(
    storage: ArcticStorage,
    library: str,
    resolution: Resolution,
    *,
    include_integrity: bool = False,
    calendar_strategy: CalendarStrategy | None = None,
) -> LibraryOverview:
    """Inspect a library and return a per-symbol profile of stored data.

    Parameters
    ----------
    storage : ArcticStorage
        Storage backend to read from.
    library : str
        Library/bucket name (e.g. ``equities-1d-databento``).
    resolution : Resolution
        Expected bar resolution (used for integrity checks).
    include_integrity : bool
        If True, run bar-level integrity checks per symbol (slower).
        Requires ``calendar_strategy`` to generate the expected grid.
    calendar_strategy : CalendarStrategy or None
        Required when ``include_integrity=True`` to build the expected grid.
    """
    symbols = storage.list_symbols(library)
    if not symbols:
        return LibraryOverview(
            library=library,
            symbol_count=0,
            total_rows=0,
            date_range=(None, None),
            symbols=[],
        )

    profiles: list[SymbolProfile] = []
    for symbol in symbols:
        profile = _profile_symbol(
            storage, library, symbol, resolution,
            include_integrity=include_integrity,
            calendar_strategy=calendar_strategy,
        )
        profiles.append(profile)
        logger.debug(
            "inspect.symbol_done",
            library=library,
            symbol=symbol,
            bars=profile.total_bars,
        )

    total_rows = sum(p.total_bars for p in profiles)
    all_first = [p.first_bar for p in profiles if p.first_bar is not None]
    all_last = [p.last_bar for p in profiles if p.last_bar is not None]
    date_range: tuple[pd.Timestamp | None, pd.Timestamp | None] = (
        min(all_first) if all_first else None,
        max(all_last) if all_last else None,
    )

    return LibraryOverview(
        library=library,
        symbol_count=len(profiles),
        total_rows=total_rows,
        date_range=date_range,
        symbols=profiles,
    )


def _profile_symbol(
    storage: ArcticStorage,
    library: str,
    symbol: str,
    resolution: Resolution,
    *,
    include_integrity: bool,
    calendar_strategy: CalendarStrategy | None,
) -> SymbolProfile:
    """Build a SymbolProfile for a single symbol."""
    if include_integrity and calendar_strategy is None:
        raise ValueError(
            "calendar_strategy is required when include_integrity=True"
        )

    # When integrity is not needed, only read volume + index to reduce I/O
    columns: list[str] | None = None if include_integrity else ["volume"]
    df = storage.read(library, symbol, columns=columns)
    size_bytes = storage.get_symbol_size(library, symbol)

    if df is None or df.empty:
        return SymbolProfile(
            symbol=symbol,
            first_bar=None,
            last_bar=None,
            total_bars=0,
            null_bars=0,
            total_volume=0.0,
            size_bytes=size_bytes,
            issue_count=0,
            issues_summary={},
        )

    first_bar = df.index[0]
    last_bar = df.index[-1]
    total_bars = len(df)
    has_volume = "volume" in df.columns
    null_bars = int((df["volume"] == 0).sum()) if has_volume else 0
    total_volume = float(df["volume"].sum()) if has_volume else 0.0

    issue_count = 0
    issues_summary: dict[str, int] = {}

    if include_integrity:
        assert calendar_strategy is not None  # guaranteed by guard above
        grid = calendar_strategy.generate_grid(first_bar, last_bar, resolution)
        report = check_integrity(df, grid, resolution)
        issue_count = len(report.issues)
        issues_summary = report.summary

    return SymbolProfile(
        symbol=symbol,
        first_bar=first_bar,
        last_bar=last_bar,
        total_bars=total_bars,
        null_bars=null_bars,
        total_volume=total_volume,
        size_bytes=size_bytes,
        issue_count=issue_count,
        issues_summary=issues_summary,
    )
