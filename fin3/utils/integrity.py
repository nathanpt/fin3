"""Bar-level data integrity audit for stored OHLCV data.

Checks stored data against an expected master grid, collecting all issues
into a non-throwing report. Complements the two-stage write-path validation
(validation.py) by verifying bar-level completeness and data quality on
already-stored artifacts.

Issues are built from the shared rule registry (``fin3.utils.rules``):
category is the rule name and severity the rule's registry severity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from fin3.schemas import AssetType, Resolution, empty_ohlcv
from fin3.utils.rules import RuleContext, evaluate_rules


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
        """True when no integrity issues were found."""
        return len(self.issues) == 0

    @property
    def summary(self) -> dict[str, int]:
        """Return issue counts grouped by category.

        Returns
        -------
        dict[str, int]
            Mapping of category name to count of issues in that category.
        """
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
    *,
    asset_type: AssetType | None = None,
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
    asset_type : AssetType or None
        Asset context; ``AssetType.INDEX_US`` selects the index volume
        interpretation (observations carry volume 0 and are judged by close
        presence).

    Returns
    -------
    IntegrityReport
        Non-throwing report collecting all issues found.
    """
    if df is None:
        # Zero-row frames evaluate only grid rules: no-data with a nonempty
        # grid reports missing_bar via the registry like any other frame.
        df = empty_ohlcv()

    context = RuleContext(
        resolution=resolution, scope="audit", grid=grid, asset_type=asset_type
    )
    issues = [
        IntegrityIssue(
            severity=rule.severity,
            category=rule.name,
            detail=finding.detail,
            timestamp=finding.timestamp,
        )
        for rule, finding in evaluate_rules(df, context)
    ]
    return IntegrityReport(
        total_bars_expected=len(grid),
        total_bars_found=len(df),
        issues=issues,
    )
