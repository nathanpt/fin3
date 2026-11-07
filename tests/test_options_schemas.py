"""Tests for options chain schemas, models, wire validation, and decoding."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from fin3.exceptions import SchemaValidationError
from fin3.options_schemas import (
    CHAIN_COLUMNS,
    CHAIN_CONVENTIONS,
    ChainCoverageManifest,
    ChainDateCoverage,
    ChainFetchReport,
    ChainResult,
    decode_stored_chain_frame,
    empty_chain_frame,
    parse_eod_id,
    parse_osi_contract,
)
from fin3.utils.options_validation import (
    compute_quote_flags,
    standard_contract_heuristic,
    validate_chain_frame,
)


def make_chain_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Build a schema-exact chain frame from minimal row dicts.

    Each dict must carry index keys ``as_of`` (date) and ``contract`` (str)
    plus any CHAIN_COLUMNS to override; identity columns (osi_root, expiry,
    right, strike) are derived from the contract when omitted.
    """
    from fin3.options_schemas import OSIComponents

    records: list[dict[str, object]] = []
    as_ofs: list[pd.Timestamp] = []
    contracts: list[str] = []
    for row in rows:
        contract = str(row["contract"])
        as_of = row["as_of"]
        assert isinstance(as_of, date)
        parsed: OSIComponents = parse_osi_contract(contract)
        record: dict[str, object] = {
            "root": row.get("root", parsed.root),
            "osi_root": row.get("osi_root", parsed.root),
            "expiry": row.get("expiry", parsed.expiry.isoformat()),
            "right": row.get("right", parsed.right),
            "expiration_type": row.get("expiration_type", "monthly"),
            "currency": row.get("currency", "USD"),
            "strike": row.get("strike", parsed.strike),
            "multiplier": row.get("multiplier", float("nan")),
            "is_standard_contract": row.get(
                "is_standard_contract",
                standard_contract_heuristic(parsed.root, str(row.get("root", parsed.root)), "monthly"),
            ),
            "bid_timestamp_raw": row.get("bid_timestamp_raw", None),
            "ask_timestamp_raw": row.get("ask_timestamp_raw", None),
            "tradetime": row.get("tradetime", None),
        }
        for column in (
            "open", "high", "low", "last", "bid", "ask", "bid_size", "ask_size",
            "volume", "open_interest", "implied_volatility", "midpoint",
            "theoretical", "delta", "gamma", "theta", "vega", "rho",
        ):
            record[column] = row.get(column, float("nan"))
        bid_raw = record["bid_timestamp_raw"]
        record["bid_timestamp_utc"] = row.get(
            "bid_timestamp_utc",
            pd.Timestamp(bid_raw).tz_convert("UTC") if bid_raw else pd.Timestamp("NaT"),
        )
        record["ask_timestamp_utc"] = row.get(
            "ask_timestamp_utc", pd.Timestamp("NaT")
        )
        bid = float(record["bid"])  # type: ignore[arg-type]
        ask = float(record["ask"])  # type: ignore[arg-type]
        bid_utc = record["bid_timestamp_utc"]
        ask_utc = record["ask_timestamp_utc"]
        flags = compute_quote_flags(
            pd.Series([bid]), pd.Series([ask]),
            pd.Series([pd.Timestamp(bid_utc) if bid_utc is not None else pd.NaT]),
            pd.Series([pd.Timestamp(ask_utc) if ask_utc is not None else pd.NaT]),
        )
        record["has_zero_bid"] = bool(flags["has_zero_bid"].iloc[0])
        record["has_zero_ask"] = bool(flags["has_zero_ask"].iloc[0])
        record["has_crossed_quote"] = bool(flags["has_crossed_quote"].iloc[0])
        record["has_unresolved_quote_time"] = bool(
            flags["has_unresolved_quote_time"].iloc[0]
        )
        record["has_non_session_label"] = bool(row.get("has_non_session_label", False))
        for flag in (
            "has_zero_bid", "has_zero_ask", "has_crossed_quote",
            "has_unresolved_quote_time", "has_non_session_label",
        ):
            record[flag] = row.get(flag, record[flag])
        records.append(record)
        as_ofs.append(pd.Timestamp(as_of, tz="UTC"))
        contracts.append(contract)
    index = pd.MultiIndex.from_arrays(
        [pd.DatetimeIndex(as_ofs), pd.Index(contracts, dtype=object)],
        names=("as_of", "contract"),
    )
    frame = pd.DataFrame(records, index=index)
    frame = frame[list(CHAIN_COLUMNS)].sort_index()
    for column in ("bid_timestamp_utc", "ask_timestamp_utc"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame


class TestEmptyChainFrame:
    def test_columns_and_index(self) -> None:
        frame = empty_chain_frame()
        assert list(frame.columns) == list(CHAIN_COLUMNS)
        assert list(frame.index.names) == ["as_of", "contract"]
        assert len(frame) == 0
        validate_chain_frame(frame)  # empty frames are structurally valid

    def test_dtypes(self) -> None:
        frame = empty_chain_frame()
        assert str(frame["strike"].dtype) == "float64"
        assert str(frame["root"].dtype) == "object"
        assert str(frame["has_zero_bid"].dtype) == "bool"
        assert str(frame["bid_timestamp_utc"].dtype) == "datetime64[ns, UTC]"


class TestOsiParsing:
    def test_compact_contract(self) -> None:
        parsed = parse_osi_contract("SPY261218C00675000")
        assert parsed.root == "SPY"
        assert parsed.expiry == date(2026, 12, 18)
        assert parsed.right == "C"
        assert parsed.strike == 675.0

    def test_fractional_strike_and_put(self) -> None:
        parsed = parse_osi_contract("F261113P00002500")
        assert parsed.strike == 2.5
        assert parsed.right == "P"

    def test_adjusted_root_with_dot(self) -> None:
        parsed = parse_osi_contract("BRK.B260918C00010000")
        assert parsed.root == "BRK.B"

    def test_yy_interpreted_as_2000_plus(self) -> None:
        assert parse_osi_contract("A991231C00000001").expiry == date(2099, 12, 31)
        assert parse_osi_contract("A260101P00000001").expiry == date(2026, 1, 1)

    @pytest.mark.parametrize(
        "bad",
        ["", "toolongroot261218C00675000", "spy261218c00675000",
         "SPY61218C0067500", "SPY261218X00675000", "SPY261218C675"],
    )
    def test_invalid_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="not a compact OSI"):
            parse_osi_contract(bad)

    def test_eod_id_parse(self) -> None:
        parsed = parse_eod_id("SPY261218C00675000-2026-11-06")
        assert parsed.contract == "SPY261218C00675000"
        assert parsed.as_of == date(2026, 11, 6)

    @pytest.mark.parametrize("bad", ["SPY261218C00675000", "X-2026-11-06", "SPY-20261340"])
    def test_eod_id_invalid_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_eod_id(bad)


class TestManifestWireValidation:
    def base_manifest(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "provider": "eodhd_options",
            "root": "SHY",
            "first_observation": None,
            "last_observation": None,
            "days": [],
            "retrieved_at": "2026-11-06T21:00:00+00:00",
            "requests_used": 3,
            "conventions": dict(CHAIN_CONVENTIONS),
        }
        payload.update(overrides)
        return payload

    def test_valid_and_json_roundtrip(self) -> None:
        manifest = ChainCoverageManifest.model_validate(self.base_manifest())
        dumped = manifest.model_dump(mode="json")
        assert dumped["schema_version"] == 1
        assert dumped["retrieved_at"] == "2026-11-06T21:00:00Z"
        rehydrated = ChainCoverageManifest.model_validate(dumped)
        assert rehydrated == manifest

    def test_schema_version_true_rejected(self) -> None:
        with pytest.raises(ValidationError, match="schema_version"):
            ChainCoverageManifest.model_validate(self.base_manifest(schema_version=True))

    def test_schema_version_string_rejected(self) -> None:
        with pytest.raises(ValidationError, match="schema_version"):
            ChainCoverageManifest.model_validate(self.base_manifest(schema_version="1"))

    def test_requests_used_bool_and_fractional_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChainCoverageManifest.model_validate(self.base_manifest(requests_used=True))
        with pytest.raises(ValidationError):
            ChainCoverageManifest.model_validate(self.base_manifest(requests_used=2.5))
        with pytest.raises(ValidationError):
            ChainCoverageManifest.model_validate(self.base_manifest(requests_used=-1))

    def test_naive_retrieved_at_rejected(self) -> None:
        with pytest.raises(ValidationError, match="offset-aware UTC"):
            ChainCoverageManifest.model_validate(
                self.base_manifest(retrieved_at="2026-11-06T21:00:00")
            )

    def test_numeric_epoch_retrieved_at_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChainCoverageManifest.model_validate(self.base_manifest(retrieved_at=1730000000))

    def test_date_objects_accepted(self) -> None:
        manifest = ChainCoverageManifest(
            schema_version=1,
            provider="eodhd_options",
            root="SHY",
            first_observation=date(2026, 11, 6),
            last_observation=date(2026, 11, 6),
            days=(
                ChainDateCoverage(
                    as_of=date(2026, 11, 6),
                    contract_count=0,
                    scope="root",
                    selected_contracts=None,
                    reference_contract_count=416,
                    extraction_complete=True,
                    reason="non_session",
                ),
            ),
            retrieved_at=datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc),
            requests_used=3,
            conventions=dict(CHAIN_CONVENTIONS),
        )
        assert manifest.first_observation == date(2026, 11, 6)

    def test_days_must_be_sorted_and_unique(self) -> None:
        day_a = {
            "as_of": "2026-11-06", "contract_count": 0, "scope": "root",
            "selected_contracts": None, "reference_contract_count": 1,
            "extraction_complete": True, "reason": "non_session",
        }
        day_b = {**day_a, "as_of": "2026-11-05"}
        with pytest.raises(ValidationError, match="unique"):
            ChainCoverageManifest.model_validate(
                self.base_manifest(days=[day_a, day_a])
            )
        with pytest.raises(ValidationError, match="sorted"):
            ChainCoverageManifest.model_validate(
                self.base_manifest(days=[day_a, day_b])
            )

    def test_conventions_dict_roundtrip(self) -> None:
        manifest = ChainCoverageManifest.model_validate(self.base_manifest())
        assert manifest.conventions == CHAIN_CONVENTIONS
        assert manifest.model_dump(mode="json")["conventions"] == CHAIN_CONVENTIONS

    def test_extraction_complete_must_be_actual_bool(self) -> None:
        day = {
            "as_of": "2026-11-06", "contract_count": 0, "scope": "root",
            "selected_contracts": None, "reference_contract_count": 1,
            "extraction_complete": 1, "reason": "non_session",
        }
        with pytest.raises(ValidationError):
            ChainDateCoverage.model_validate(day)


class TestFetchReportWireValidation:
    def test_roundtrip(self) -> None:
        report = ChainFetchReport(
            root="SHY", start=date(2026, 11, 6), end=date(2026, 11, 6),
            scope="root", selected_contracts=None,
            reference_contract_count=416, extraction_complete=True,
            requests_used=25, retrieved_at=datetime.now(timezone.utc), reason=None,
        )
        dumped = report.model_dump(mode="json")
        assert ChainFetchReport.model_validate(dumped) == report

    def test_naive_retrieved_at_rejected(self) -> None:
        with pytest.raises(ValidationError, match="offset-aware UTC"):
            ChainFetchReport.model_validate({
                "root": "SHY", "start": "2026-11-06", "end": "2026-11-06",
                "scope": "root", "selected_contracts": None,
                "reference_contract_count": 0, "extraction_complete": True,
                "requests_used": 0, "retrieved_at": "2026-11-06T00:00:00",
                "reason": None,
            })

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ChainFetchReport.model_validate({
                "root": "SHY", "start": "2026-11-06", "end": "2026-11-06",
                "scope": "root", "selected_contracts": None,
                "reference_contract_count": 0, "extraction_complete": True,
                "requests_used": 0, "retrieved_at": "2026-11-06T00:00:00+00:00",
                "reason": None, "bonus": 1,
            })


class TestStoredFrameDecoder:
    def test_naive_utc_value_column_relocalized(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6, "bid_timestamp_raw": "2026-11-06T21:14:59.000000Z"},
        ])
        naive = frame.copy()
        naive["bid_timestamp_utc"] = naive["bid_timestamp_utc"].dt.tz_localize(None)
        restored = decode_stored_chain_frame(naive)
        assert str(restored["bid_timestamp_utc"].dtype) == "datetime64[ns, UTC]"
        assert restored["bid_timestamp_utc"].iloc[0] == pd.Timestamp(
            "2026-11-06T21:14:59Z"
        )

    def test_existing_utc_retained_and_bad_dtype_rejected(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6},
        ])
        assert decode_stored_chain_frame(frame)["bid_timestamp_utc"].equals(
            frame["bid_timestamp_utc"]
        )
        broken = frame.copy()
        broken["bid_timestamp_utc"] = broken["bid_timestamp_utc"].astype(object)
        with pytest.raises(SchemaValidationError, match="non-datetime dtype"):
            decode_stored_chain_frame(broken)

    def test_empty_frame_recast_preserves_index(self) -> None:
        empty = empty_chain_frame()
        empty.index = pd.MultiIndex.from_arrays(
            [
                pd.DatetimeIndex([pd.Timestamp("2026-11-06", tz="UTC")])[:0],
                pd.Index([], dtype=object),
            ],
            names=("as_of", "contract"),
        )
        # Simulate ArcticDB's empty recast: object-dtype everything.
        mangled = pd.DataFrame(
            {c: pd.Series([], dtype=object) for c in CHAIN_COLUMNS},
            index=empty.index,
        )
        out = decode_stored_chain_frame(mangled)
        assert len(out) == 0
        assert str(out["bid_timestamp_utc"].dtype) == "datetime64[ns, UTC]"
        assert list(out.index.names) == ["as_of", "contract"]
        validate_chain_frame(out)

    def test_wrong_index_names_rejected(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6},
        ])
        mangled = frame.copy()
        mangled.index = mangled.index.set_names(["date", "c"])
        with pytest.raises(SchemaValidationError, match="MultiIndex"):
            decode_stored_chain_frame(mangled)


class TestChainFrameValidation:
    def test_valid_frame_passes(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6,
             "bid_timestamp_raw": "2026-11-06T21:14:59.000000Z",
             "volume": 0.0, "open_interest": 25.0, "implied_volatility": 2.0228},
        ])
        validate_chain_frame(frame)

    def test_iv_above_one_retained(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2025, 3, 20), "contract": "SPY250321C00800000",
             "bid": 0.0, "ask": 0.01, "implied_volatility": 2.0228,
             "bid_timestamp_raw": "2025-03-20T20:14:59Z"},
        ])
        assert frame["has_zero_bid"].iloc[0] is np.bool_(True) or bool(
            frame["has_zero_bid"].iloc[0]
        )
        validate_chain_frame(frame)  # no IV ceiling is imposed

    def test_wrong_columns_rejected(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6},
        ]).drop(columns=["multiplier"])
        with pytest.raises(SchemaValidationError, match="columns mismatch"):
            validate_chain_frame(frame)

    def test_unsorted_rejected(self) -> None:
        late = make_chain_frame([
            {"as_of": date(2026, 11, 7), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6},
        ])
        early = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6},
        ])
        frame = pd.concat([late, early])  # deliberately unsorted
        with pytest.raises(SchemaValidationError, match="sorted"):
            validate_chain_frame(frame)

    def test_duplicate_rejected(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6},
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6},
        ])
        with pytest.raises(SchemaValidationError, match="duplicate"):
            validate_chain_frame(frame)

    def test_identity_disagreement_rejected(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6, "strike": 95.0},
        ])
        with pytest.raises(SchemaValidationError, match="strike"):
            validate_chain_frame(frame)

    def test_negative_price_rejected(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": -0.5, "ask": 0.6},
        ])
        with pytest.raises(SchemaValidationError, match="negative"):
            validate_chain_frame(frame)

    def test_non_integral_size_rejected(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6, "bid_size": 1.5},
        ])
        with pytest.raises(SchemaValidationError, match="integral"):
            validate_chain_frame(frame)

    def test_signed_greeks_legal(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6, "delta": -0.97, "theta": -0.99,
             "vega": 0.019},
        ])
        validate_chain_frame(frame)

    def test_flag_inconsistency_rejected(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6, "has_zero_bid": True},
        ])
        with pytest.raises(SchemaValidationError, match="flag"):
            validate_chain_frame(frame)

    def test_naive_as_of_rejected(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6},
        ])
        naive = frame.copy()
        naive.index = pd.MultiIndex.from_arrays(
            [
                pd.DatetimeIndex(naive.index.get_level_values("as_of")).tz_localize(None),
                naive.index.get_level_values("contract"),
            ],
            names=("as_of", "contract"),
        )
        with pytest.raises(SchemaValidationError, match="UTC"):
            validate_chain_frame(naive)

    def test_intraday_as_of_rejected(self) -> None:
        frame = make_chain_frame([
            {"as_of": date(2026, 11, 6), "contract": "SHY261120C00091000",
             "bid": 0.5, "ask": 0.6},
        ])
        intraday = frame.copy()
        intraday.index = pd.MultiIndex.from_arrays(
            [
                pd.DatetimeIndex(intraday.index.get_level_values("as_of")) + pd.Timedelta(hours=3),
                intraday.index.get_level_values("contract"),
            ],
            names=("as_of", "contract"),
        )
        with pytest.raises(SchemaValidationError, match="midnight"):
            validate_chain_frame(intraday)


class TestStandardHeuristic:
    def test_true_only_for_matching_letters_root_and_labeled(self) -> None:
        assert standard_contract_heuristic("SPY", "SPY", "monthly")
        assert standard_contract_heuristic("SPY", "SPY", "weekly")

    def test_false_cases(self) -> None:
        assert not standard_contract_heuristic("SPY", "SPY", None)
        assert not standard_contract_heuristic("SPY", "SPY", "quarterly")
        assert not standard_contract_heuristic("SPY1", "SPY", "monthly")
        assert not standard_contract_heuristic("BRK.B", "BRK.B", "monthly")
        assert not standard_contract_heuristic("XSP", "SPY", "monthly")


class TestChainResult:
    def test_frozen(self) -> None:
        result = ChainResult(
            data=empty_chain_frame(), manifest=None, status="no_data", reason="x"
        )
        with pytest.raises(FrozenInstanceError):
            result.status = "observed"  # type: ignore[misc]
