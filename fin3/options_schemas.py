"""Chain-shaped schemas and constants for historical EOD options.

This module defines the public contract for options EOD chain data: the
stored/read DataFrame shape (``CHAIN_COLUMNS``, ``empty_chain_frame``), the
OSI/OSI-style contract parsing helpers, and the metadata models that travel
with a stored root snapshot (``ChainCoverageManifest``) or a provider run
(``ChainFetchReport``).

Options chains never flow through the OHLCV schemas in :mod:`fin3.schemas`:
no padding, no aggregation, and no lifecycle bootstrap applies to them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from fin3.exceptions import SchemaValidationError

OPTIONS_EOD_LIBRARY = "options-eod-eodhd"
"""ArcticDB library (bucket) holding per-root chain snapshots."""

CHAIN_REPORT_ATTR = "fin3_options"
"""DataFrame attrs key carrying the mandatory ``ChainFetchReport`` dump."""

STANDARD_COLUMNS = (
    "root",
    "osi_root",
    "expiry",
    "right",
    "expiration_type",
    "currency",
    "strike",
    "multiplier",
    "is_standard_contract",
    "open",
    "high",
    "low",
    "last",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "volume",
    "open_interest",
    "implied_volatility",
    "midpoint",
    "theoretical",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
    "bid_timestamp_raw",
    "ask_timestamp_raw",
    "tradetime",
    "bid_timestamp_utc",
    "ask_timestamp_utc",
    "has_zero_bid",
    "has_zero_ask",
    "has_crossed_quote",
    "has_unresolved_quote_time",
    "has_non_session_label",
)
"""Ordered columns of the chain DataFrame (index: ``("as_of", "contract")``)."""

CHAIN_COLUMNS: tuple[str, ...] = STANDARD_COLUMNS
"""Public alias: exact column tuple of the chain DataFrame."""

_STRING_COLUMNS = frozenset(
    {
        "root",
        "osi_root",
        "expiry",
        "right",
        "expiration_type",
        "currency",
        "bid_timestamp_raw",
        "ask_timestamp_raw",
        "tradetime",
    }
)
_FLOAT_COLUMNS = frozenset(
    {
        "strike",
        "multiplier",
        "open",
        "high",
        "low",
        "last",
        "bid",
        "ask",
        "bid_size",
        "ask_size",
        "volume",
        "open_interest",
        "implied_volatility",
        "midpoint",
        "theoretical",
        "delta",
        "gamma",
        "theta",
        "vega",
        "rho",
    }
)
_BOOL_COLUMNS = frozenset(
    {
        "is_standard_contract",
        "has_zero_bid",
        "has_zero_ask",
        "has_crossed_quote",
        "has_unresolved_quote_time",
        "has_non_session_label",
    }
)
_UTC_TIMESTAMP_COLUMNS = ("bid_timestamp_utc", "ask_timestamp_utc")

CHAIN_INDEX_NAMES = ("as_of", "contract")
"""Sorted, unique MultiIndex names of the chain DataFrame."""

_OSI_PATTERN = re.compile(r"^([A-Z0-9.]{1,6})([0-9]{6})([CP])([0-9]{8})$")
_EOD_ID_PATTERN = re.compile(r"^(.+)-([0-9]{4}-[0-9]{2}-[0-9]{2})$")

# Sentinel reasons used by provider/orchestrator classification.
REASON_UNKNOWN_UNDERLYING = "unknown_underlying"
REASON_NO_CONTRACTS = "source_returned_no_contracts"
REASON_NO_ROWS = "source_returned_no_rows"
REASON_EXPIRY_BEFORE_WINDOW = "expiry_before_window"
REASON_EXPIRY_BEFORE_AS_OF = "expiry_before_as_of"
REASON_ROOT_NOT_INGESTED = "root_not_ingested"
REASON_OUTSIDE_COVERAGE = "outside_ingested_coverage"
REASON_DATE_NOT_INGESTED = "date_not_ingested"
REASON_NO_ROWS_FOR_EXPIRY = "no_rows_for_expiry"


@dataclass(frozen=True)
class OSIComponents:
    """Parsed identity of a compact OSI/OSI-style option contract id."""

    root: str
    """OSI root (may be adjusted, e.g. an issue-suffixed symbol)."""
    expiry: date
    right: str
    """``"C"`` or ``"P"``."""
    strike: float
    """Strike as the 6-digit integer / 1,000 (exact thousandths)."""


def parse_osi_contract(contract: str) -> OSIComponents:
    """Parse a compact OCC/OSI option identifier.

    Raises
    ------
    ValueError
        If *contract* does not match the 1–6 character root, YYMMDD, C/P,
        8-digit strike pattern (strike interpreted as thousandths).
    """
    match = _OSI_PATTERN.match(contract)
    if match is None:
        raise ValueError(
            f"contract {contract!r} is not a compact OSI identifier "
            "(expected ROOT + YYMMDD + C/P + 8-digit thousandths strike)"
        )
    root, yymmdd, right, strike_digits = match.groups()
    expiry = date(2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))
    strike = int(strike_digits) / 1000.0
    return OSIComponents(root=root, expiry=expiry, right=right, strike=strike)


@dataclass(frozen=True)
class EODIdComponents:
    """Parsed identity of a vendor per-contract EOD row id."""

    contract: str
    as_of: date


def parse_eod_id(eod_id: str) -> EODIdComponents:
    """Parse an ``CONTRACT-YYYY-MM-DD`` EOD observation id.

    Raises
    ------
    ValueError
        If the id does not end in an ISO date, or its contract prefix is not
        a compact OSI identifier.
    """
    match = _EOD_ID_PATTERN.match(eod_id)
    if match is None:
        raise ValueError(
            f"EOD id {eod_id!r} does not match CONTRACT-YYYY-MM-DD"
        )
    contract, iso = match.group(1), match.group(2)
    parse_osi_contract(contract)  # require an OSI-valid contract prefix
    as_of = date.fromisoformat(iso)
    return EODIdComponents(contract=contract, as_of=as_of)


def empty_chain_frame() -> pd.DataFrame:
    """Return an empty chain DataFrame with the canonical schema.

    Empty and nonempty frames share identical dtypes so storage round-trips
    and validation need no special cases beyond ArcticDB's own empty-frame
    dtype recast (handled by the options decoder).
    """
    index = pd.MultiIndex.from_arrays(
        [
            pd.DatetimeIndex([], tz="UTC"),
            pd.Index([], dtype=object),
        ],
        names=CHAIN_INDEX_NAMES,
    )
    data: dict[str, Any] = {}
    for column in CHAIN_COLUMNS:
        if column in _STRING_COLUMNS:
            data[column] = pd.Series([], dtype=object)
        elif column in _FLOAT_COLUMNS:
            data[column] = pd.Series([], dtype="float64")
        elif column in _BOOL_COLUMNS:
            data[column] = pd.Series([], dtype=bool)
        elif column in _UTC_TIMESTAMP_COLUMNS:
            data[column] = pd.Series([], dtype="datetime64[ns, UTC]")
        else:  # pragma: no cover - column sets above are exhaustive
            raise AssertionError(f"unhandled column {column}")
    frame = pd.DataFrame(data, index=index)
    return frame


# ---------------------------------------------------------------------------
# Metadata models
# ---------------------------------------------------------------------------

CHAIN_CONVENTIONS: dict[str, str] = {
    "coverage_basis": "ingested_observations",
    "iv_source": "vendor",
    "iv_units": "decimal",
    "price_basis": "as_published_unverified_adjustments",
    "quote_units": "as_published_currency_units",
    "multiplier_source": "unavailable",
    "snapshot_clock": "unverified",
    "oi_clearing_date": "unverified",
    "volume_convention": "unverified",
    "zero_quote_meaning": "unverified",
    "survivorship_status": "unverified",
    "canonical_timezone": "UTC",
    "naive_quote_timezone": "unverified",
    "standard_contract_flag": "heuristic_not_deliverable_confirmation",
}
"""Exact convention key/value set required in every published manifest."""


class ChainDateCoverage(BaseModel):
    """Per-observation-date coverage record (metadata, never contract rows)."""

    model_config = ConfigDict(extra="forbid")

    as_of: date
    contract_count: int
    scope: Literal["root", "contracts"]
    selected_contracts: tuple[str, ...] | None
    reference_contract_count: int
    extraction_complete: bool
    reason: str | None

    @field_validator("contract_count", "reference_contract_count", mode="before")
    @classmethod
    def _strict_nonnegative_int(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("coverage counts must be strict integers")
        if value < 0:
            raise ValueError("coverage counts must be nonnegative")
        return value

    @field_validator("extraction_complete", mode="before")
    @classmethod
    def _strict_bool(cls, value: object) -> object:
        if not isinstance(value, bool):
            raise ValueError("extraction_complete must be an actual bool")
        return value

    @field_validator("as_of", mode="before")
    @classmethod
    def _iso_date(cls, value: object) -> object:
        if isinstance(value, str):
            # Accept only exact ISO YYYY-MM-DD (metadata serialization shape).
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError(f"as_of {value!r} is not exact ISO YYYY-MM-DD")
            return parsed
        return value


class ChainCoverageManifest(BaseModel):
    """Coverage metadata stored alongside a root chain snapshot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    provider: Literal["eodhd_options"]
    root: str
    first_observation: date | None
    last_observation: date | None
    days: tuple[ChainDateCoverage, ...]
    retrieved_at: datetime
    requests_used: int
    conventions: dict[str, str]

    @field_validator("schema_version", mode="before")
    @classmethod
    def _literal_one(cls, value: object) -> object:
        # Reject True/1.0/"1": wire validation must not turn True into 1.
        if isinstance(value, bool) or not isinstance(value, int) or value != 1:
            raise ValueError("schema_version must be the integer 1")
        return value

    @field_validator("first_observation", "last_observation", mode="before")
    @classmethod
    def _optional_iso_date(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError(
                    f"observation bound {value!r} is not exact ISO YYYY-MM-DD"
                )
            return parsed
        return value

    @field_validator("requests_used", mode="before")
    @classmethod
    def _strict_nonneg_requests(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("requests_used must be a strict nonnegative integer")
        return value

    @field_validator("retrieved_at", mode="before")
    @classmethod
    def _utc_timestamp(cls, value: object) -> object:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError("retrieved_at must be offset-aware UTC")
            return parsed.astimezone(timezone.utc)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("retrieved_at must be offset-aware UTC")
            return value.astimezone(timezone.utc)
        raise ValueError("retrieved_at must be an ISO datetime")

    @field_validator("days")
    @classmethod
    def _sorted_unique_days(
        cls, value: tuple[ChainDateCoverage, ...]
    ) -> tuple[ChainDateCoverage, ...]:
        as_ofs = [entry.as_of for entry in value]
        if as_ofs != sorted(as_ofs):
            raise ValueError("coverage days must be sorted by as_of")
        if len(set(as_ofs)) != len(as_ofs):
            raise ValueError("coverage days must be unique by as_of")
        return value

    @field_validator("root")
    @classmethod
    def _root_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("root must be nonempty")
        return value


class ChainFetchReport(BaseModel):
    """Report of one provider extraction run; mandatory in frame attrs."""

    model_config = ConfigDict(extra="forbid")

    root: str
    start: date
    end: date
    scope: Literal["root", "contracts"]
    selected_contracts: tuple[str, ...] | None
    reference_contract_count: int
    extraction_complete: bool
    requests_used: int
    retrieved_at: datetime
    reason: str | None

    @field_validator("requests_used", mode="before")
    @classmethod
    def _strict_nonneg_requests(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("requests_used must be a strict nonnegative integer")
        return value

    @field_validator("retrieved_at", mode="before")
    @classmethod
    def _utc_timestamp(cls, value: object) -> object:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("retrieved_at must be offset-aware UTC")
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError("retrieved_at must be offset-aware UTC")
            return parsed.astimezone(timezone.utc)
        raise ValueError("retrieved_at must be an ISO datetime")


@dataclass(frozen=True)
class ChainResult:
    """Read result for one root/date; any nonempty data is ``observed``."""

    data: pd.DataFrame
    manifest: ChainCoverageManifest | None
    status: Literal["observed", "no_data"]
    reason: str | None


def decode_stored_chain_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Restore a stored chain frame to canonical schema before validation.

    ArcticDB's native writer preserves the UTC MultiIndex but strips the
    timezone from *value* columns: a stored ``datetime64[ns, UTC]`` column
    round-trips as naive ``datetime64[ns]`` retaining its UTC clock value.
    For ``bid_timestamp_utc``/``ask_timestamp_utc`` only, a naive native
    datetime column therefore represents **already-normalized UTC** and is
    re-localized to UTC here; an existing UTC dtype is retained. This is a
    storage-fidelity fix, never a vendor-timezone inference.

    Other nonempty dtype deviations are left untouched —
    :func:`fin3.utils.options_validation.validate_chain_frame` rejects them
    as schema errors. Genuinely empty frames are recast onto the declared
    empty schema (ArcticDB recasts empty-frame dtypes on round-trip) without
    inventing rows/columns or coercing nonempty values.
    """
    if not isinstance(frame.index, pd.MultiIndex) or tuple(
        frame.index.names
    ) != CHAIN_INDEX_NAMES:
        raise SchemaValidationError(
            "stored chain frame index must be a MultiIndex named "
            f"{CHAIN_INDEX_NAMES}"
        )
    if len(frame) == 0:
        return _recast_empty(frame)

    out = frame.copy()
    for column in _UTC_TIMESTAMP_COLUMNS:
        dtype = out[column].dtype
        if str(dtype) == "datetime64[ns, UTC]":
            continue
        if str(dtype) == "datetime64[ns]":
            out[column] = out[column].dt.tz_localize("UTC")
            continue
        raise SchemaValidationError(
            f"stored column {column!r} has non-datetime dtype {dtype}"
        )
    return out


def _recast_empty(frame: pd.DataFrame) -> pd.DataFrame:
    """Rebuild an empty stored frame onto the declared empty schema."""
    index = frame.index
    try:
        as_of = pd.DatetimeIndex(
            pd.to_datetime(index.get_level_values("as_of"), utc=True)
        ).normalize()
        contracts = pd.Index(index.get_level_values("contract")).astype(object)
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(
            "stored empty chain frame index is not (datetime, str)-shaped"
        ) from exc
    out = empty_chain_frame()
    out.index = pd.MultiIndex.from_arrays(
        [as_of, contracts], names=CHAIN_INDEX_NAMES
    )
    return out
