"""Two-stage validation pipeline.

Stage 1 (validate_raw_provider_data): structural checks on raw provider output.
Stage 2 (validate_storage_artifact): strict checks on the padded storage artifact.
"""

from __future__ import annotations

import pandas as pd
import structlog

from fin3.exceptions import DataValidationError, SchemaValidationError
from fin3.schemas import OHLCV_COLUMNS, Resolution

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pure detection helpers — return structured results, never raise.
# Consumed by both the throwing wrappers below and integrity.py.
# ---------------------------------------------------------------------------


def _has_duplicates(df: pd.DataFrame) -> bool:
    return bool(df.index.duplicated().any())


def _is_monotonic(df: pd.DataFrame) -> bool:
    return bool(df.index.is_monotonic_increasing)


def _find_bad_spacings(df: pd.DataFrame, resolution: Resolution) -> pd.Series:
    """Return a Series of timestamp diffs that don't match *resolution*.

    Only checks within-session spacings. Inter-session gaps (overnight, weekends)
    are excluded because they are expected for exchange-aligned intraday data.
    Empty Series means all spacings are correct (or df has < 2 rows).
    """
    if len(df) < 2:
        return pd.Series(dtype="timedelta64[ns]")
    diffs = pd.Series(df.index).diff().dropna()
    expected = pd.tseries.frequencies.to_offset(resolution.timedelta_alias)
    # Filter to within-session diffs only: skip gaps larger than the resolution.
    # Inter-session gaps (overnight/weekend) are always > resolution for intraday data.
    within_session = diffs[diffs <= pd.Timedelta(resolution.timedelta_alias)]
    return within_session[within_session != expected]


def _find_ohlcv_violations(df: pd.DataFrame) -> pd.Series:
    """Return a boolean Series (True = OHLCV constraint violation).

    Only rows where all four OHLC columns are non-NaN are considered.
    """
    o, h, low_, c = df["open"], df["high"], df["low"], df["close"]
    all_present = o.notna() & h.notna() & low_.notna() & c.notna()
    if not all_present.any():
        return pd.Series(dtype=bool)
    ok = (low_ <= o) & (o <= h) & (low_ <= c) & (c <= h)
    return all_present & ~ok


# ---------------------------------------------------------------------------
# Throwing wrappers — used by the two-stage write-path validation pipeline.
# ---------------------------------------------------------------------------


def _check_duplicates(df: pd.DataFrame, label: str) -> None:
    if _has_duplicates(df):
        raise SchemaValidationError(f"[{label}] Duplicate timestamps found")


def _check_monotonic(df: pd.DataFrame, label: str) -> None:
    if not _is_monotonic(df):
        raise SchemaValidationError(
            f"[{label}] Timestamps are not monotonically increasing"
        )


def _check_resolution(df: pd.DataFrame, resolution: Resolution, label: str) -> None:
    bad = _find_bad_spacings(df, resolution)
    if len(bad) > 0:
        expected = pd.tseries.frequencies.to_offset(resolution.timedelta_alias)
        raise SchemaValidationError(
            f"[{label}] Timestamp spacing {bad.iloc[0]} does not match expected {expected}"
        )


def _check_ohlcv_constraints_vectorized(df: pd.DataFrame, label: str) -> None:
    """Vectorized OHLCV constraint check. Raises on violation."""
    violations = _find_ohlcv_violations(df)
    if len(violations) > 0 and violations.any():
        bad_idx = violations[violations].index[0]
        row = df.loc[bad_idx]
        raise SchemaValidationError(
            f"[{label}] OHLCV constraint violation at {bad_idx}: "
            f"o={row['open']} h={row['high']} l={row['low']} c={row['close']}"
        )


def validate_raw_provider_data(df: pd.DataFrame, resolution: Resolution) -> None:
    """Stage 1: structural validation on raw provider data (pre-reindex).

    Accepts partial/sparse data. Rejects corrupt structural issues.
    """
    label = "stage1"

    if df.empty:
        return

    _check_duplicates(df, label)
    _check_monotonic(df, label)
    _check_resolution(df, resolution, label)

    if "volume" not in df.columns:
        raise SchemaValidationError(f"[{label}] Missing 'volume' column")

    if df["volume"].isna().any():
        raise SchemaValidationError(f"[{label}] volume must never be NaN")

    ohlc_cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if ohlc_cols:
        _check_ohlcv_constraints_vectorized(df[ohlc_cols + ["volume"]], label)

    has_volume = df["volume"] > 0
    ohlc_present = pd.concat([df[c].notna() for c in ohlc_cols], axis=1).all(axis=1)
    partial = has_volume & ~ohlc_present
    if partial.any():
        for idx in df[partial].index[:3]:
            logger.warning(
                "validation.partial_ohlcv",
                timestamp=idx,
                msg="volume>0 but some OHLC are NaN",
            )


def validate_storage_artifact(df: pd.DataFrame, resolution: Resolution) -> None:
    """Stage 2: strict validation on the fully-padded storage artifact (post-reindex).

    The artifact must conform to the NaN semantics: volume=0 implies all OHLC NaN,
    volume>0 implies no NaN in any OHLCV column.
    """
    label = "stage2"

    if df.empty:
        return

    _check_duplicates(df, label)
    _check_monotonic(df, label)
    _check_resolution(df, resolution, label)

    expected_cols = list(OHLCV_COLUMNS)
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        raise DataValidationError(f"[{label}] Missing columns: {missing}")

    vol = df["volume"]
    ohlc_cols = ["open", "high", "low", "close"]
    ohlc_all_nan = pd.concat([df[c].isna() for c in ohlc_cols], axis=1).all(axis=1)
    ohlc_any_nan = pd.concat([df[c].isna() for c in ohlc_cols], axis=1).any(axis=1)

    if vol.isna().any():
        first_na = vol[vol.isna()].index[0]
        raise DataValidationError(f"[{label}] volume must never be NaN (at {first_na})")

    positive_vol = vol > 0
    if (positive_vol & ohlc_any_nan).any():
        bad = df[positive_vol & ohlc_any_nan].iloc[0]
        raise DataValidationError(f"[{label}] volume>0 but OHLC has NaN at {bad.name}")

    if (positive_vol).any():
        _check_ohlcv_constraints_vectorized(df[positive_vol], label)

    zero_vol = vol == 0
    if (zero_vol & ~ohlc_all_nan).any():
        bad = df[zero_vol & ~ohlc_all_nan].iloc[0]
        raise DataValidationError(
            f"[{label}] volume=0 but OHLC are not all NaN at {bad.name}"
        )
