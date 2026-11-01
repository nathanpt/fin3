"""Tests for the two-stage validation pipeline."""

import numpy as np
import pandas as pd
import pytest

from fin3.exceptions import DataValidationError, SchemaValidationError
from fin3.schemas import Resolution
from fin3.utils.integrity import check_integrity
from fin3.utils.validation import validate_raw_provider_data, validate_storage_artifact
from structlog.testing import capture_logs
from tests.conftest import make_ohlcv, make_empty_ohlcv


class TestStage1Validation:
    def test_valid_data_passes(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        validate_raw_provider_data(df, Resolution.ONE_MINUTE)

    def test_empty_df_passes(self) -> None:
        validate_raw_provider_data(make_empty_ohlcv(), Resolution.ONE_MINUTE)

    def test_duplicate_timestamps_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = pd.concat([df, df.iloc[[0]]])
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "duplicate"

    def test_nan_volume_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = np.nan
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "nan_volume"

    def test_ohlcv_constraint_violation_rejected(self) -> None:
        # Negative side of the constraint (high < low must fail). The
        # equality-passes boundary (flat bar l==o==h==c) is pinned by
        # TestSessionGapsAndBoundaries.test_flat_bar_equality_passes_both_stages_and_integrity_clean.
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("high")] = 50.0
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "ohlcv_violation"

    def test_partial_nan_with_valid_volume_accepted(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("open")] = np.nan
        validate_raw_provider_data(df, Resolution.ONE_MINUTE)

    def test_non_monotonic_timestamps_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.iloc[[1, 0, 2, 3, 4]]  # swap first two rows
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "non_monotonic"

    def test_resolution_mismatch_rejected(self) -> None:
        # Negative side: 1m bars must fail a 1h spacing check. The positive
        # subtlety (inter-session gaps pass the within-session spacing filter)
        # is pinned by TestSessionGapsAndBoundaries.test_inter_session_gap_accepted_and_integrity_clean.
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_raw_provider_data(df, Resolution.ONE_HOUR)
        assert excinfo.value.rule == "resolution_mismatch"

    def test_missing_volume_column_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.drop(columns=["volume"])
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "missing_column"


class TestStage2Validation:
    def test_valid_artifact_passes(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        validate_storage_artifact(df, Resolution.ONE_MINUTE)

    def test_empty_df_passes(self) -> None:
        validate_storage_artifact(make_empty_ohlcv(), Resolution.ONE_MINUTE)

    def test_volume_zero_with_non_nan_ohlcv_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = 0
        with pytest.raises(DataValidationError) as excinfo:
            validate_storage_artifact(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "nan_semantics"

    def test_volume_positive_with_nan_ohlcv_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("open")] = np.nan
        with pytest.raises(DataValidationError) as excinfo:
            validate_storage_artifact(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "nan_semantics"

    def test_volume_zero_with_all_nan_ohlcv_passes(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = 0
        for col in ("open", "high", "low", "close"):
            df.iloc[0, df.columns.get_loc(col)] = np.nan
        validate_storage_artifact(df, Resolution.ONE_MINUTE)

    def test_missing_column_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.drop(columns=["volume"])
        with pytest.raises(DataValidationError) as excinfo:
            validate_storage_artifact(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "missing_column"

    def test_nan_volume_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = np.nan
        with pytest.raises(DataValidationError) as excinfo:
            validate_storage_artifact(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "nan_volume"

    def test_non_monotonic_timestamps_rejected_stage2(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.iloc[[1, 0, 2, 3, 4]]
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_storage_artifact(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "non_monotonic"

    def test_duplicate_timestamps_rejected_stage2(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = pd.concat([df, df.iloc[[-1]]])
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_storage_artifact(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "duplicate"

    def test_resolution_mismatch_rejected_stage2(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_storage_artifact(df, Resolution.ONE_HOUR)
        assert excinfo.value.rule == "resolution_mismatch"

    def test_ohlcv_constraint_violation_rejected_stage2(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("high")] = 50.0
        with pytest.raises(SchemaValidationError) as excinfo:
            validate_storage_artifact(df, Resolution.ONE_MINUTE)
        assert excinfo.value.rule == "ohlcv_violation"


class TestSignGates:
    """Negative price/volume sign gates (shared detector, both stages)."""

    def test_valid_data_passes_both_stages(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        validate_storage_artifact(df, Resolution.ONE_MINUTE)

    def test_negative_price_rejected_stage1(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("low")] = -1.0
        with pytest.raises(DataValidationError) as excinfo:
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        assert type(excinfo.value) is DataValidationError
        assert excinfo.value.rule == "negative_price"

    def test_negative_price_rejected_stage2(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("low")] = -1.0
        with pytest.raises(DataValidationError) as excinfo:
            validate_storage_artifact(df, Resolution.ONE_MINUTE)
        assert type(excinfo.value) is DataValidationError
        assert excinfo.value.rule == "negative_price"

    def test_negative_volume_rejected_stage1(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = -100
        with pytest.raises(DataValidationError) as excinfo:
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        assert type(excinfo.value) is DataValidationError
        assert excinfo.value.rule == "negative_volume"

    def test_negative_volume_rejected_stage2(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = -100
        with pytest.raises(DataValidationError) as excinfo:
            validate_storage_artifact(df, Resolution.ONE_MINUTE)
        assert type(excinfo.value) is DataValidationError
        assert excinfo.value.rule == "negative_volume"

    def test_zero_volume_is_legal_stage1(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = 0
        validate_raw_provider_data(df, Resolution.ONE_MINUTE)

    def test_zero_volume_is_legal_stage2(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = 0
        for col in ("open", "high", "low", "close"):
            df.iloc[0, df.columns.get_loc(col)] = np.nan
        validate_storage_artifact(df, Resolution.ONE_MINUTE)


class TestSessionGapsAndBoundaries:
    """Within-session spacing filter and OHLCV constraint boundary cases."""

    def test_inter_session_gap_accepted_and_integrity_clean(self) -> None:
        day1 = make_ohlcv("2024-01-02 09:30", periods=391, freq="1min")  # 09:30–16:00 incl.
        day2 = make_ohlcv("2024-01-03 09:30", periods=391, freq="1min")
        df = pd.concat([day1, day2])
        # Sanity: the fixture really contains an overnight hole.
        assert df.index.to_series().diff().max() > pd.Timedelta(hours=12)
        validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        validate_storage_artifact(df, Resolution.ONE_MINUTE)
        report = check_integrity(df, df.index, Resolution.ONE_MINUTE)
        assert report.is_clean

    def test_flat_bar_equality_passes_both_stages_and_integrity_clean(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        for col in ("open", "high", "low", "close"):
            df.iloc[2, df.columns.get_loc(col)] = 100.0
        validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        validate_storage_artifact(df, Resolution.ONE_MINUTE)
        report = check_integrity(df, df.index, Resolution.ONE_MINUTE)
        assert report.is_clean


class TestPartialOhlcvWarning:
    """Stage-1 partial_ohlcv warning (structlog event capture)."""

    def test_partial_ohlcv_warning_fires_with_timestamp(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("open")] = np.nan
        with capture_logs() as cap:
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        events = [e for e in cap if e["event"] == "validation.partial_ohlcv"]
        assert len(events) == 1
        assert events[0]["timestamp"] == df.index[0]

    def test_no_partial_warning_when_ohlcv_complete(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        with capture_logs() as cap:
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)
        assert not any(e["event"] == "validation.partial_ohlcv" for e in cap)
