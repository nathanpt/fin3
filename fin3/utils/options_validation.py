"""Structural validation and small derivations for chain DataFrames.

Lives beside the OHLCV validators in :mod:`fin3.utils.validation` but shares
nothing with the bar rule registry: chains are never padded, aggregated, or
judged by volume semantics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fin3.exceptions import SchemaValidationError
from fin3.options_schemas import (
    CHAIN_COLUMNS,
    CHAIN_INDEX_NAMES,
    _BOOL_COLUMNS,
    _FLOAT_COLUMNS,
    _STRING_COLUMNS,
    _UTC_TIMESTAMP_COLUMNS,
    parse_osi_contract,
)


def compute_quote_flags(
    bid: pd.Series,
    ask: pd.Series,
    bid_utc: pd.Series,
    ask_utc: pd.Series,
) -> dict[str, pd.Series]:
    """Derive the quote-quality boolean flags for one chain frame.

    - ``has_zero_bid`` / ``has_zero_ask``: a *non-null* price equal to 0
      (zero quotes are retained and flagged, never dropped or clamped).
    - ``has_crossed_quote``: both prices non-null and ``bid > ask``.
    - ``has_unresolved_quote_time``: a non-null bid/ask whose normalized UTC
      timestamp is NaT (naive source strings stay raw and unresolved).
    """
    has_zero_bid = bid.notna() & (bid == 0)
    has_zero_ask = ask.notna() & (ask == 0)
    has_crossed = bid.notna() & ask.notna() & (bid > ask)
    unresolved = (bid.notna() & bid_utc.isna()) | (ask.notna() & ask_utc.isna())
    return {
        "has_zero_bid": has_zero_bid,
        "has_zero_ask": has_zero_ask,
        "has_crossed_quote": has_crossed,
        "has_unresolved_quote_time": unresolved,
    }


def standard_contract_heuristic(
    osi_root: str, underlying_root: str, expiration_type: str | None
) -> bool:
    """Label a contract "standard" by heuristic only.

    True only when the OSI root equals the letters-only underlying root and
    the vendor labels the contract ``weekly`` or ``monthly``.

    Limits (this flag is NOT a deliverable guarantee and MUST NOT be used to
    fill a multiplier):

    - False positives: adjusted contracts (split, special dividend) whose OSI
      root still equals the underlying and whose vendor expiration_type is
      weekly/monthly have non-100 multipliers but flag True.
    - False negatives: genuinely standard contracts whose vendor
      expiration_type is null or unexpectedly labelled flag False.
    """
    return (
        osi_root == underlying_root
        and bool(underlying_root.isalpha())
        and expiration_type in ("weekly", "monthly")
    )


_NONNEGATIVE_PRICE_COLUMNS = (
    "strike",
    "open",
    "high",
    "low",
    "last",
    "bid",
    "ask",
    "midpoint",
    "theoretical",
    "implied_volatility",
)
_INTEGRAL_COLUMNS = ("bid_size", "ask_size", "volume", "open_interest")
_GREEK_COLUMNS = ("delta", "gamma", "theta", "vega", "rho")


def validate_chain_frame(frame: pd.DataFrame) -> None:
    """Validate the structural chain contract; raise on the first failure.

    Checks exact columns and dtypes, the sorted/unique
    ``("as_of", "contract")`` MultiIndex, OSI identity/date agreement,
    finite/nonnegative non-null quotes/strike/IV and integral sizes/volume/OI
    (signed Greeks are legal), and the consistency of the derived quote
    flags. Missing financial values stay null — no filling, clamping, or
    IV ceiling is imposed here or anywhere downstream.
    """
    columns = [str(c) for c in frame.columns]
    if columns != list(CHAIN_COLUMNS):
        raise SchemaValidationError(
            "chain columns mismatch: expected exact CHAIN_COLUMNS order, got "
            f"{columns}"
        )
    if not isinstance(frame.index, pd.MultiIndex):
        raise SchemaValidationError("chain index must be a MultiIndex")
    if tuple(frame.index.names) != CHAIN_INDEX_NAMES:
        raise SchemaValidationError(
            f"chain index names must be {CHAIN_INDEX_NAMES}, got "
            f"{tuple(frame.index.names)}"
        )
    _check_dtypes(frame)
    if len(frame) == 0:
        return
    _check_sorted_unique(frame)
    _check_identity(frame)
    _check_numeric_domain(frame)
    _check_flags(frame)


def _check_dtypes(frame: pd.DataFrame) -> None:
    for column, dtype in frame.dtypes.items():
        name = str(column)
        if name in _STRING_COLUMNS:
            if not pd.api.types.is_object_dtype(dtype):
                raise SchemaValidationError(
                    f"column {name!r} must be object-backed strings, got {dtype}"
                )
        elif name in _FLOAT_COLUMNS:
            if not pd.api.types.is_float_dtype(dtype):
                raise SchemaValidationError(
                    f"column {name!r} must be float64, got {dtype}"
                )
        elif name in _BOOL_COLUMNS:
            if not pd.api.types.is_bool_dtype(dtype):
                raise SchemaValidationError(
                    f"column {name!r} must be bool, got {dtype}"
                )
        elif name in _UTC_TIMESTAMP_COLUMNS:
            if str(dtype) != "datetime64[ns, UTC]":
                raise SchemaValidationError(
                    f"column {name!r} must be datetime64[ns, UTC], got {dtype}"
                )
        else:  # pragma: no cover - column set is checked before dtypes
            raise SchemaValidationError(f"unexpected column {name!r}")


def _check_sorted_unique(frame: pd.DataFrame) -> None:
    if frame.index.has_duplicates:
        raise SchemaValidationError("chain index contains duplicate (as_of, contract) keys")
    if not frame.index.is_monotonic_increasing:
        raise SchemaValidationError("chain index must be sorted by (as_of, contract)")
    as_of_level = pd.DatetimeIndex(frame.index.get_level_values("as_of"))
    tz = getattr(as_of_level.dtype, "tz", None)
    if tz is None:
        raise SchemaValidationError("as_of index level must be UTC-normalized")
    if not as_of_level.equals(as_of_level.normalize()):
        raise SchemaValidationError("as_of index values must be UTC midnight labels")


def _check_identity(frame: pd.DataFrame) -> None:
    contracts = frame.index.get_level_values("contract")
    for i, contract in enumerate(contracts):
        try:
            parsed = parse_osi_contract(str(contract))
        except ValueError as exc:
            raise SchemaValidationError(
                f"contract {contract!r} at position {i} is not OSI-valid: {exc}"
            ) from exc
        osi_root = frame["osi_root"].iloc[i]
        if osi_root != parsed.root:
            raise SchemaValidationError(
                f"osI root {osi_root!r} disagrees with contract "
                f"{contract!r} (expected {parsed.root!r})"
            )
        expiry = frame["expiry"].iloc[i]
        if expiry != parsed.expiry.isoformat():
            raise SchemaValidationError(
                f"expiry {expiry!r} disagrees with contract {contract!r} "
                f"(expected {parsed.expiry.isoformat()!r})"
            )
        right = frame["right"].iloc[i]
        if right != parsed.right:
            raise SchemaValidationError(
                f"right {right!r} disagrees with contract {contract!r} "
                f"(expected {parsed.right!r})"
            )
        strike = frame["strike"].iloc[i]
        if not isinstance(strike, float) or pd.isna(strike):
            raise SchemaValidationError(
                f"strike for contract {contract!r} must be non-null float64"
            )
        if strike != parsed.strike:
            raise SchemaValidationError(
                f"strike {strike!r} disagrees with contract {contract!r} "
                f"(expected {parsed.strike!r})"
            )


def _check_numeric_domain(frame: pd.DataFrame) -> None:
    for column in _NONNEGATIVE_PRICE_COLUMNS:
        values = frame[column].to_numpy(dtype="float64", na_value=np.nan)
        finite = np.isfinite(values) | np.isnan(values)
        if not finite.all():
            raise SchemaValidationError(
                f"column {column!r} contains non-finite (inf) values"
            )
        nonneg = (values >= 0) | np.isnan(values)
        if not nonneg.all():
            raise SchemaValidationError(
                f"column {column!r} contains negative non-null values"
            )
    for column in _INTEGRAL_COLUMNS:
        values = frame[column].to_numpy(dtype="float64", na_value=np.nan)
        present = ~np.isnan(values)
        integral = np.isclose(values[present], np.round(values[present]), rtol=0, atol=0)
        if not integral.all():
            raise SchemaValidationError(
                f"column {column!r} must be integral when present"
            )
        if present.any() and values[present].min() < 0:
            raise SchemaValidationError(
                f"column {column!r} contains negative non-null values"
            )
    for column in _GREEK_COLUMNS:
        values = frame[column].to_numpy(dtype="float64", na_value=np.nan)
        if not (np.isfinite(values) | np.isnan(values)).all():
            raise SchemaValidationError(
                f"column {column!r} contains non-finite (inf) values"
            )


def _check_flags(frame: pd.DataFrame) -> None:
    recomputed = compute_quote_flags(
        frame["bid"], frame["ask"], frame["bid_timestamp_utc"], frame["ask_timestamp_utc"]
    )
    for name, expected in recomputed.items():
        actual = frame[name].to_numpy(dtype=bool)
        if not np.array_equal(actual, expected.to_numpy(dtype=bool)):
            raise SchemaValidationError(
                f"flag column {name!r} disagrees with the quote/timestamp "
                "values it is derived from"
            )
