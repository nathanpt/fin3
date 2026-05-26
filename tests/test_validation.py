"""Tests for the two-stage validation pipeline."""

import numpy as np
import pandas as pd
import pytest

from fin3.exceptions import DataValidationError, SchemaValidationError
from fin3.schemas import Resolution
from fin3.utils.validation import validate_raw_provider_data, validate_storage_artifact
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
        with pytest.raises(SchemaValidationError, match="Duplicate"):
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)

    def test_nan_volume_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = np.nan
        with pytest.raises(SchemaValidationError, match="volume"):
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)

    def test_ohlcv_constraint_violation_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("high")] = 50.0
        with pytest.raises(SchemaValidationError, match="OHLCV constraint"):
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)

    def test_partial_nan_with_valid_volume_accepted(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("open")] = np.nan
        validate_raw_provider_data(df, Resolution.ONE_MINUTE)

    def test_non_monotonic_timestamps_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.iloc[[1, 0, 2, 3, 4]]  # swap first two rows
        with pytest.raises(SchemaValidationError, match="monotonically"):
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)

    def test_resolution_mismatch_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        with pytest.raises(SchemaValidationError, match="spacing"):
            validate_raw_provider_data(df, Resolution.ONE_HOUR)

    def test_missing_volume_column_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.drop(columns=["volume"])
        with pytest.raises(SchemaValidationError, match="volume"):
            validate_raw_provider_data(df, Resolution.ONE_MINUTE)


class TestStage2Validation:
    def test_valid_artifact_passes(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        validate_storage_artifact(df, Resolution.ONE_MINUTE)

    def test_empty_df_passes(self) -> None:
        validate_storage_artifact(make_empty_ohlcv(), Resolution.ONE_MINUTE)

    def test_volume_zero_with_non_nan_ohlcv_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = 0
        with pytest.raises(DataValidationError, match="volume=0"):
            validate_storage_artifact(df, Resolution.ONE_MINUTE)

    def test_volume_positive_with_nan_ohlcv_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("open")] = np.nan
        with pytest.raises(DataValidationError, match="volume>0"):
            validate_storage_artifact(df, Resolution.ONE_MINUTE)

    def test_volume_zero_with_all_nan_ohlcv_passes(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = 0
        for col in ("open", "high", "low", "close"):
            df.iloc[0, df.columns.get_loc(col)] = np.nan
        validate_storage_artifact(df, Resolution.ONE_MINUTE)

    def test_missing_column_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.drop(columns=["volume"])
        with pytest.raises(DataValidationError, match="Missing columns"):
            validate_storage_artifact(df, Resolution.ONE_MINUTE)

    def test_nan_volume_rejected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = np.nan
        with pytest.raises(DataValidationError, match="volume must never be NaN"):
            validate_storage_artifact(df, Resolution.ONE_MINUTE)

    def test_non_monotonic_timestamps_rejected_stage2(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.iloc[[1, 0, 2, 3, 4]]
        with pytest.raises(SchemaValidationError, match="monotonically"):
            validate_storage_artifact(df, Resolution.ONE_MINUTE)
