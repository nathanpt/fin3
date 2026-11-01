"""Shared data-quality rule registry.

One ordered source of quality rules consumed by both write-path validation
stages (``fin3.utils.validation``) and the bar-level integrity audit
(``fin3.utils.integrity``). Rules are plain immutable dataclasses in a
constant tuple — no plugin or registration framework.

``evaluate_rules`` is lazy: validators stop at the first error-severity
finding without evaluating later rules, while the audit exhausts the same
iterator to collect a full report. Predicates return findings and never
raise or mutate their input.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from fin3.schemas import OHLCV_COLUMNS, Resolution

Severity = Literal["error", "warning"]
Scope = Literal["stage1", "stage2", "audit"]

_OHLC = OHLCV_COLUMNS[:4]  # open, high, low, close

_ALL_SCOPES: frozenset[Scope] = frozenset({"stage1", "stage2", "audit"})
_WRITE_SCOPES: frozenset[Scope] = frozenset({"stage1", "stage2"})
_STAGE2_AUDIT: frozenset[Scope] = frozenset({"stage2", "audit"})
_AUDIT_ONLY: frozenset[Scope] = frozenset({"audit"})
_STAGE1_ONLY: frozenset[Scope] = frozenset({"stage1"})

# Statistical-outlier detection contract (deterministic, advisory-only).
_OUTLIER_MIN_RETURNS = 100
_OUTLIER_THRESHOLD = 8.0
# Standard normal quantile at 0.75; makes the MAD score comparable to a
# z-score for Gaussian returns.
_OUTLIER_SCALE = 0.6744897501960817


@dataclass(frozen=True)
class RuleContext:
    """Evaluation context for one rule pass over a frame."""

    resolution: Resolution
    scope: Scope
    grid: pd.DatetimeIndex | None = None


@dataclass(frozen=True)
class Finding:
    """A single detection result produced by a rule predicate."""

    detail: str
    timestamp: pd.Timestamp | None = None


@dataclass(frozen=True)
class Rule:
    """One named, ordered data-quality rule."""

    name: str
    severity: Severity
    description: str
    scopes: frozenset[Scope]
    predicate: Callable[[pd.DataFrame, RuleContext], tuple[Finding, ...]]


# ---------------------------------------------------------------------------
# Detection helpers — moved from validation.py / integrity.py.
# Positional (iloc/mask-position) first-offender selection throughout, so a
# duplicate index can never make a report throw while rendering a finding.
# ---------------------------------------------------------------------------


def _find_bad_spacings(df: pd.DataFrame, resolution: Resolution) -> pd.Series:
    """Return a Series of timestamp diffs that don't match *resolution*.

    Only checks within-session spacings. Inter-session gaps (overnight,
    weekends) are excluded because they are expected for exchange-aligned
    intraday data. Empty Series means all spacings are correct (or df has
    < 2 rows).
    """
    if len(df) < 2:
        return pd.Series(dtype="timedelta64[ns]")
    diffs = pd.Series(df.index).diff().dropna()
    expected = pd.tseries.frequencies.to_offset(resolution.timedelta_alias)
    # Filter to within-session diffs only: skip gaps larger than the resolution.
    # Inter-session gaps (overnight/weekend) are always > resolution for intraday data.
    within_session = diffs[diffs <= pd.Timedelta(resolution.timedelta_alias)]
    return within_session[within_session != expected]


def _first_position(mask: pd.Series) -> int:
    """Position of the first True in a boolean Series."""
    return int(np.flatnonzero(mask.to_numpy())[0])


def _partial_ohlcv_mask(df: pd.DataFrame) -> pd.Series:
    """Positive-volume rows where some present OHLC column is NaN."""
    ohlc = [c for c in _OHLC if c in df.columns]
    partial = (df["volume"] > 0) & ~df[ohlc].notna().all(axis=1)
    return partial


# ---------------------------------------------------------------------------
# Rule predicates
# ---------------------------------------------------------------------------


def _missing_bar_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    grid = ctx.grid
    if grid is None or len(grid) == 0:
        return ()
    df_idx = df.index
    if not isinstance(df_idx, pd.DatetimeIndex):
        df_idx = pd.DatetimeIndex(df_idx)
    missing = grid.difference(df_idx)
    if len(missing) == 0:
        return ()
    return (
        Finding(f"{len(missing)} bar(s) present in grid but missing from stored data"),
    )


def _extra_bar_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    grid = ctx.grid
    if grid is None or len(grid) == 0:
        return ()
    extra = df.index.difference(grid)
    if len(extra) == 0:
        return ()
    return (Finding(f"{len(extra)} bar(s) in stored data not present in grid"),)


def _duplicate_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    dup_mask = df.index.duplicated()
    count = int(dup_mask.sum())
    if count == 0:
        return ()
    return (Finding(f"{count} duplicate timestamp(s)", df.index[dup_mask][0]),)


def _non_monotonic_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    if len(df) < 2 or df.index.is_monotonic_increasing:
        return ()
    return (Finding("Timestamps are not monotonically increasing"),)


def _resolution_mismatch_findings(
    df: pd.DataFrame, ctx: RuleContext
) -> tuple[Finding, ...]:
    bad = _find_bad_spacings(df, ctx.resolution)
    if len(bad) == 0:
        return ()
    expected = pd.tseries.frequencies.to_offset(ctx.resolution.timedelta_alias)
    return (
        Finding(
            f"{len(bad)} bar(s) with spacing != {expected}; "
            f"first mismatch: {bad.iloc[0]}"
        ),
    )


def _missing_column_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    if len(df) == 0:
        return ()
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if not missing:
        return ()
    return (Finding(f"Missing columns: {missing}"),)


def _nan_volume_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    if len(df) == 0 or "volume" not in df.columns:
        return ()
    nan_mask = df["volume"].isna()
    count = int(nan_mask.sum())
    if count == 0:
        return ()
    return (
        Finding(
            f"{count} bar(s) with NaN volume",
            df.index[_first_position(nan_mask)],
        ),
    )


def _negative_price_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    if len(df) == 0:
        return ()
    ohlc = [c for c in _OHLC if c in df.columns]
    if not ohlc:
        return ()
    neg_mask = (df[ohlc] < 0).any(axis=1)
    count = int(neg_mask.sum())
    if count == 0:
        return ()
    return (
        Finding(
            f"{count} bar(s) with negative price value",
            df.index[_first_position(neg_mask)],
        ),
    )


def _negative_volume_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    if len(df) == 0 or "volume" not in df.columns:
        return ()
    neg_mask = df["volume"] < 0
    count = int(neg_mask.sum())
    if count == 0:
        return ()
    return (
        Finding(
            f"{count} bar(s) with negative volume",
            df.index[_first_position(neg_mask)],
        ),
    )


def _nan_semantics_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    if len(df) == 0 or "volume" not in df.columns:
        return ()
    ohlc = [c for c in _OHLC if c in df.columns]
    if not ohlc:
        return ()
    vol = df["volume"]
    ohlc_na = df[ohlc].isna()
    any_nan = ohlc_na.any(axis=1)
    all_nan = ohlc_na.all(axis=1)

    findings: list[Finding] = []
    # volume > 0 but OHLC has NaN
    positive_with_nan = (vol > 0) & any_nan
    count = int(positive_with_nan.sum())
    if count > 0:
        findings.append(
            Finding(
                f"{count} bar(s) with volume>0 but NaN in OHLC",
                df.index[_first_position(positive_with_nan)],
            )
        )
    # volume == 0 but OHLC not all NaN
    zero_with_ohlcv = (vol == 0) & ~all_nan
    count = int(zero_with_ohlcv.sum())
    if count > 0:
        findings.append(
            Finding(
                f"{count} bar(s) with volume=0 but OHLC not all NaN",
                df.index[_first_position(zero_with_ohlcv)],
            )
        )
    return tuple(findings)


def _ohlcv_violation_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    if len(df) == 0:
        return ()
    if not all(c in df.columns for c in _OHLC):
        return ()
    o, h, low_, c = df["open"], df["high"], df["low"], df["close"]
    complete = o.notna() & h.notna() & low_.notna() & c.notna()
    if ctx.scope == "stage1":
        eligible = complete
    else:
        # Stage 2 and audit judge the storage contract: only positive-volume
        # rows must satisfy OHLC consistency. Zero-volume populated rows are
        # covered by nan_semantics.
        eligible = complete & (df["volume"] > 0)
    ok = (low_ <= o) & (o <= h) & (low_ <= c) & (c <= h)
    bad = eligible & ~ok
    count = int(bad.sum())
    if count == 0:
        return ()
    pos = _first_position(bad)
    ts = df.index[pos]
    row = df.iloc[pos]
    return (
        Finding(
            f"{count} bar(s) with OHLCV constraint violation; "
            f"first at {ts}: o={row['open']} h={row['high']} "
            f"l={row['low']} c={row['close']}",
            ts,
        ),
    )


def _partial_ohlcv_findings(df: pd.DataFrame, ctx: RuleContext) -> tuple[Finding, ...]:
    if len(df) == 0 or "volume" not in df.columns:
        return ()
    if not any(c in df.columns for c in _OHLC):
        return ()
    partial = _partial_ohlcv_mask(df)
    positions = np.flatnonzero(partial.to_numpy())[:3]
    return tuple(
        Finding(
            f"volume>0 but some OHLC are NaN at {df.index[pos]}",
            df.index[pos],
        )
        for pos in positions
    )


def _statistical_outlier_findings(
    df: pd.DataFrame, ctx: RuleContext
) -> tuple[Finding, ...]:
    mask = _statistical_outlier_mask(df, ctx.resolution)
    count = int(mask.sum())
    if count == 0:
        return ()
    first = df.index[_first_position(mask)]
    return (
        Finding(f"{count} close-return outlier(s); first at {first}", first),
    )


def _statistical_outlier_mask(df: pd.DataFrame, resolution: Resolution) -> pd.Series:
    """Boolean mask of statistical close-return outliers, aligned to *df*.

    Deterministic and advisory: a return is eligible only when the two
    original adjacent rows have finite strictly positive close and volume
    and their timestamp difference equals exactly the resolution (missing
    bars are never bridged). Eligible returns are scored against the median
    absolute deviation; a score above ``_OUTLIER_THRESHOLD`` flags the
    ending row of the pair. Fewer than ``_OUTLIER_MIN_RETURNS`` eligible
    returns, a zero MAD, or an unordered/duplicate index yield no findings.
    """
    mask = pd.Series(False, index=df.index, dtype=bool)
    if len(df) < 2 or "close" not in df.columns or "volume" not in df.columns:
        return mask
    index = df.index
    if (
        not isinstance(index, pd.DatetimeIndex)
        or not index.is_monotonic_increasing
        or index.has_duplicates
    ):
        return mask

    delta = pd.Timedelta(resolution.timedelta_alias)
    gap_ok = index.to_series().diff().eq(delta).to_numpy()
    close = df["close"].to_numpy(dtype=float)
    volume = df["volume"].to_numpy(dtype=float)
    usable = np.isfinite(close) & (close > 0) & np.isfinite(volume) & (volume > 0)

    eligible = np.zeros(len(df), dtype=bool)
    eligible[1:] = gap_ok[1:] & usable[:-1] & usable[1:]
    if int(eligible.sum()) < _OUTLIER_MIN_RETURNS:
        return mask

    log_close = np.full(len(df), np.nan)
    np.log(close, out=log_close, where=usable)
    returns = np.full(len(df), np.nan)
    returns[1:] = log_close[1:] - log_close[:-1]
    returns[~eligible] = np.nan

    values = returns[eligible]
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    if mad == 0:
        return mask

    scores = np.zeros(len(df))
    scores[eligible] = _OUTLIER_SCALE * np.abs(values - median) / mad
    return pd.Series(eligible & (scores > _OUTLIER_THRESHOLD), index=df.index)


# ---------------------------------------------------------------------------
# Ordered registry — the single source of rule truth.
# ---------------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    Rule(
        name="missing_bar",
        severity="error",
        description=(
            "Bars present in the expected calendar grid but missing from stored data."
        ),
        scopes=_AUDIT_ONLY,
        predicate=_missing_bar_findings,
    ),
    Rule(
        name="extra_bar",
        severity="warning",
        description=(
            "Bars in stored data that are not part of the expected calendar grid."
        ),
        scopes=_AUDIT_ONLY,
        predicate=_extra_bar_findings,
    ),
    Rule(
        name="duplicate",
        severity="error",
        description="Duplicate timestamps in the data.",
        scopes=_ALL_SCOPES,
        predicate=_duplicate_findings,
    ),
    Rule(
        name="non_monotonic",
        severity="error",
        description="Timestamps are not monotonically increasing.",
        scopes=_ALL_SCOPES,
        predicate=_non_monotonic_findings,
    ),
    Rule(
        name="resolution_mismatch",
        severity="error",
        description=(
            "Within-session timestamp spacing does not match the expected "
            "resolution. Inter-session gaps (overnight/weekend) are expected "
            "and excluded."
        ),
        scopes=_ALL_SCOPES,
        predicate=_resolution_mismatch_findings,
    ),
    Rule(
        name="missing_column",
        severity="error",
        description=(
            "A required OHLCV column is missing from a frame with at least one row."
        ),
        scopes=_ALL_SCOPES,
        predicate=_missing_column_findings,
    ),
    Rule(
        name="nan_volume",
        severity="error",
        description="Bars with NaN volume.",
        scopes=_ALL_SCOPES,
        predicate=_nan_volume_findings,
    ),
    Rule(
        name="negative_price",
        severity="error",
        description="Bars with negative price values.",
        scopes=_ALL_SCOPES,
        predicate=_negative_price_findings,
    ),
    Rule(
        name="negative_volume",
        severity="error",
        description="Bars with negative volume.",
        scopes=_ALL_SCOPES,
        predicate=_negative_volume_findings,
    ),
    Rule(
        name="nan_semantics",
        severity="error",
        description=(
            "Inconsistent NaN semantics: volume>0 with NaN OHLC, or volume=0 "
            "with non-NaN OHLC."
        ),
        scopes=_STAGE2_AUDIT,
        predicate=_nan_semantics_findings,
    ),
    Rule(
        name="ohlcv_violation",
        severity="error",
        description=(
            "OHLC relationships broken (open>high, close>high, open<low). "
            "Likely bad source data — individual bars have inconsistent "
            "price construction."
        ),
        scopes=_ALL_SCOPES,
        predicate=_ohlcv_violation_findings,
    ),
    Rule(
        name="partial_ohlcv",
        severity="warning",
        description=(
            "Positive-volume rows with some OHLC cells missing (Stage-1 "
            "advisory; the audit has no partial-cell category)."
        ),
        scopes=_STAGE1_ONLY,
        predicate=_partial_ohlcv_findings,
    ),
    Rule(
        name="statistical_outlier",
        severity="warning",
        description=(
            "Close-to-close log return deviates from the median by more than "
            "8 median absolute deviations. Advisory only; never blocks writes."
        ),
        scopes=_AUDIT_ONLY,
        predicate=_statistical_outlier_findings,
    ),
)

RULE_BY_NAME: dict[str, Rule] = {rule.name: rule for rule in RULES}


def evaluate_rules(
    df: pd.DataFrame, context: RuleContext
) -> Iterator[tuple[Rule, Finding]]:
    """Lazily yield ``(rule, finding)`` pairs applicable to *context*.

    Rules run in registry order. Iteration ends immediately after a
    ``missing_column`` finding: every subsequent rule is column-dependent,
    and index/grid findings have already been collected.
    """
    for rule in RULES:
        if context.scope not in rule.scopes:
            continue
        findings = rule.predicate(df, context)
        for finding in findings:
            yield rule, finding
        if rule.name == "missing_column" and findings:
            return
