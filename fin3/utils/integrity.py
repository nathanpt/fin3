"""Bar-level data integrity audit for stored OHLCV data.

Checks stored data against an expected master grid, collecting all issues
into a non-throwing report. Complements the two-stage write-path validation
(validation.py) by verifying bar-level completeness and data quality on
already-stored artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from fin3.schemas import OHLCV_COLUMNS, Resolution
from fin3.utils.validation import (
    _find_bad_spacings,
    _find_ohlcv_violations,
    _has_duplicates,
    _is_monotonic,
)

_OHLC = OHLCV_COLUMNS[:4]  # open, high, low, close


@dataclass
class IntegrityIssue:
    """A single data integrity finding."""

    severity: Literal["error", "warning"]
    category: str
    detail: str
    timestamp: pd.Timestamp | None = None


@dataclass
class IntegrityReport:
    """Aggregated result of an integrity audit."""

    total_bars_expected: int
    total_bars_found: int
    issues: list[IntegrityIssue] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.issues) == 0

    @property
    def summary(self) -> dict[str, int]:
        return _summarize(self.issues)


def _summarize(issues: list[IntegrityIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.category] = counts.get(issue.category, 0) + 1
    return counts


def check_integrity(
    df: pd.DataFrame | None,
    grid: pd.DatetimeIndex,
    resolution: Resolution,
) -> IntegrityReport:
    """Bar-level integrity audit of stored data against the expected master grid.

    Parameters
    ----------
    df : DataFrame or None
        Stored OHLCV data. None means the symbol was not found.
    grid : DatetimeIndex
        Expected master grid of bar timestamps.
    resolution : Resolution
        Expected bar resolution.

    Returns
    -------
    IntegrityReport
        Non-throwing report collecting all issues found.
    """
    if df is None or df.empty:
        if len(grid) == 0:
            return IntegrityReport(total_bars_expected=0, total_bars_found=0)
        return IntegrityReport(
            total_bars_expected=len(grid),
            total_bars_found=0,
            issues=[
                IntegrityIssue(
                    severity="error",
                    category="missing_bar",
                    detail=f"Symbol has no stored data; {len(grid)} bars expected",
                )
            ],
        )

    issues: list[IntegrityIssue] = []

    issues.extend(_check_missing_bars(df, grid))
    issues.extend(_check_extra_bars(df, grid))
    issues.extend(_check_duplicates(df))
    issues.extend(_check_monotonic(df))
    issues.extend(_check_resolution(df, resolution))
    issues.extend(_check_nan_volume(df))
    issues.extend(_check_negative_volume(df))
    issues.extend(_check_nan_semantics(df))
    issues.extend(_check_ohlcv_constraints(df))
    issues.extend(_check_negative_prices(df))

    return IntegrityReport(
        total_bars_expected=len(grid),
        total_bars_found=len(df),
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Individual checks — all vectorized
# ---------------------------------------------------------------------------


def _check_missing_bars(df: pd.DataFrame, grid: pd.DatetimeIndex) -> list[IntegrityIssue]:
    if len(grid) == 0:
        return []
    df_idx = df.index
    if not isinstance(df_idx, pd.DatetimeIndex):
        df_idx = pd.DatetimeIndex(df_idx)
    missing = grid.difference(df_idx)
    if len(missing) == 0:
        return []
    return [
        IntegrityIssue(
            severity="error",
            category="missing_bar",
            detail=f"{len(missing)} bar(s) present in grid but missing from stored data",
        )
    ]


def _check_extra_bars(df: pd.DataFrame, grid: pd.DatetimeIndex) -> list[IntegrityIssue]:
    if len(grid) == 0:
        return []
    extra = df.index.difference(grid)
    if len(extra) == 0:
        return []
    return [
        IntegrityIssue(
            severity="warning",
            category="extra_bar",
            detail=f"{len(extra)} bar(s) in stored data not present in grid",
        )
    ]


def _check_duplicates(df: pd.DataFrame) -> list[IntegrityIssue]:
    if not _has_duplicates(df):
        return []
    dup_mask = df.index.duplicated()
    count = int(dup_mask.sum())
    first_dup = df.index[dup_mask][0]
    return [
        IntegrityIssue(
            severity="error",
            category="duplicate",
            detail=f"{count} duplicate timestamp(s)",
            timestamp=first_dup,
        )
    ]


def _check_monotonic(df: pd.DataFrame) -> list[IntegrityIssue]:
    if len(df) < 2 or _is_monotonic(df):
        return []
    return [
        IntegrityIssue(
            severity="error",
            category="non_monotonic",
            detail="Timestamps are not monotonically increasing",
        )
    ]


def _check_resolution(df: pd.DataFrame, resolution: Resolution) -> list[IntegrityIssue]:
    bad = _find_bad_spacings(df, resolution)
    if len(bad) == 0:
        return []
    expected = pd.tseries.frequencies.to_offset(resolution.timedelta_alias)
    return [
        IntegrityIssue(
            severity="error",
            category="resolution_mismatch",
            detail=f"{len(bad)} bar(s) with spacing != {expected}; first mismatch: {bad.iloc[0]}",
        )
    ]


def _check_nan_volume(df: pd.DataFrame) -> list[IntegrityIssue]:
    if "volume" not in df.columns:
        return [
            IntegrityIssue(
                severity="error",
                category="missing_column",
                detail="volume column is missing entirely",
            )
        ]
    nan_mask = df["volume"].isna()
    count = int(nan_mask.sum())
    if count == 0:
        return []
    first = df.index[nan_mask][0]
    return [
        IntegrityIssue(
            severity="error",
            category="nan_volume",
            detail=f"{count} bar(s) with NaN volume",
            timestamp=first,
        )
    ]


def _check_negative_volume(df: pd.DataFrame) -> list[IntegrityIssue]:
    if "volume" not in df.columns:
        return []
    neg_mask = df["volume"] < 0
    count = int(neg_mask.sum())
    if count == 0:
        return []
    first = df.index[neg_mask][0]
    return [
        IntegrityIssue(
            severity="error",
            category="negative_volume",
            detail=f"{count} bar(s) with negative volume",
            timestamp=first,
        )
    ]


def _check_nan_semantics(df: pd.DataFrame) -> list[IntegrityIssue]:
    if "volume" not in df.columns:
        return []
    ohlc_cols = [c for c in _OHLC if c in df.columns]
    if not ohlc_cols:
        return []

    issues: list[IntegrityIssue] = []
    vol = df["volume"]
    ohlc_na = df[ohlc_cols].isna()
    ohlc_any_nan = ohlc_na.any(axis=1)
    ohlc_all_nan = ohlc_na.all(axis=1)

    # volume > 0 but OHLC has NaN
    positive_with_nan = (vol > 0) & ohlc_any_nan
    count = int(positive_with_nan.sum())
    if count > 0:
        first = df.index[positive_with_nan][0]
        issues.append(
            IntegrityIssue(
                severity="error",
                category="nan_semantics",
                detail=f"{count} bar(s) with volume>0 but NaN in OHLC",
                timestamp=first,
            )
        )

    # volume == 0 but OHLC not all NaN
    zero_with_ohlcv = (vol == 0) & ~ohlc_all_nan
    count = int(zero_with_ohlcv.sum())
    if count > 0:
        first = df.index[zero_with_ohlcv][0]
        issues.append(
            IntegrityIssue(
                severity="error",
                category="nan_semantics",
                detail=f"{count} bar(s) with volume=0 but OHLC not all NaN",
                timestamp=first,
            )
        )

    return issues


def _check_ohlcv_constraints(df: pd.DataFrame) -> list[IntegrityIssue]:
    ohlc_cols = [c for c in _OHLC if c in df.columns]
    if len(ohlc_cols) < 4:
        return []
    violations = _find_ohlcv_violations(df)
    bad_count = int(violations.sum())
    if bad_count == 0:
        return []
    bad_idx = violations[violations].index[0]
    row = df.loc[bad_idx]
    return [
        IntegrityIssue(
            severity="error",
            category="ohlcv_violation",
            detail=(
                f"{bad_count} bar(s) with OHLCV constraint violation; "
                f"first at {bad_idx}: o={row['open']} h={row['high']} "
                f"l={row['low']} c={row['close']}"
            ),
            timestamp=bad_idx,
        )
    ]


def _check_negative_prices(df: pd.DataFrame) -> list[IntegrityIssue]:
    ohlc_cols = [c for c in _OHLC if c in df.columns]
    if not ohlc_cols:
        return []
    neg_mask = (df[ohlc_cols] < 0).any(axis=1)
    count = int(neg_mask.sum())
    if count == 0:
        return []
    first = df.index[neg_mask][0]
    return [
        IntegrityIssue(
            severity="warning",
            category="negative_price",
            detail=f"{count} bar(s) with negative price value",
            timestamp=first,
        )
    ]
