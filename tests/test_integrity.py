"""Tests for the bar-level data integrity audit function."""

import numpy as np
import pandas as pd

from fin3.schemas import Resolution
from fin3.utils.integrity import check_integrity
from tests.conftest import make_ohlcv, make_empty_ohlcv


def _grid(start: str, periods: int, freq: str = "1min") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=periods, freq=freq, tz="UTC")


class TestCheckIntegrityClean:
    def test_clean_data_passes(self) -> None:
        grid = _grid("2024-01-02 09:30", 10)
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        assert report.is_clean
        assert report.total_bars_expected == 10
        assert report.total_bars_found == 10
        assert report.issues == []
        assert report.summary == {}

    def test_clean_with_zero_volume_bars(self) -> None:
        grid = _grid("2024-01-02 09:30", 5)
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        # Make last bar a valid zero-volume bar
        df.iloc[-1, df.columns.get_loc("volume")] = 0
        for col in ("open", "high", "low", "close"):
            df.iloc[-1, df.columns.get_loc(col)] = np.nan
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        assert report.is_clean

    def test_none_df_with_empty_grid(self) -> None:
        report = check_integrity(None, pd.DatetimeIndex([], tz="UTC"), Resolution.ONE_MINUTE)
        assert report.is_clean
        assert report.total_bars_expected == 0
        assert report.total_bars_found == 0

    def test_empty_df_with_empty_grid(self) -> None:
        report = check_integrity(
            make_empty_ohlcv(), pd.DatetimeIndex([], tz="UTC"), Resolution.ONE_MINUTE
        )
        assert report.is_clean


class TestCheckIntegrityMissingBars:
    def test_none_df_reports_all_missing(self) -> None:
        grid = _grid("2024-01-02 09:30", 10)
        report = check_integrity(None, grid, Resolution.ONE_MINUTE)
        assert not report.is_clean
        assert report.total_bars_found == 0
        assert report.total_bars_expected == 10
        cats = [i.category for i in report.issues]
        assert "missing_bar" in cats

    def test_partial_data_reports_missing(self) -> None:
        grid = _grid("2024-01-02 09:30", 10)
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        assert not report.is_clean
        cats = [i.category for i in report.issues]
        assert "missing_bar" in cats


class TestCheckIntegrityExtraBars:
    def test_extra_bars_reported_as_warning(self) -> None:
        grid = _grid("2024-01-02 09:30", 5)
        df = make_ohlcv("2024-01-02 09:30", periods=8, freq="1min")
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "extra_bar" in cats
        issue = next(i for i in report.issues if i.category == "extra_bar")
        assert issue.severity == "warning"


class TestCheckIntegrityDuplicates:
    def test_duplicate_timestamps(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = pd.concat([df, df.iloc[[0]]])
        grid = pd.DatetimeIndex(df.index)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "duplicate" in cats


class TestCheckIntegrityMonotonicity:
    def test_non_monotonic_timestamps(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.iloc[[1, 0, 2, 3, 4]]
        grid = pd.DatetimeIndex(sorted(df.index))
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "non_monotonic" in cats


class TestCheckIntegrityResolution:
    def test_resolution_mismatch(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_HOUR)
        cats = [i.category for i in report.issues]
        assert "resolution_mismatch" in cats


class TestCheckIntegrityNanVolume:
    def test_nan_volume_detected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = np.nan
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "nan_volume" in cats

    def test_missing_volume_column(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.drop(columns=["volume"])
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "missing_column" in cats


class TestCheckIntegrityNegativeVolume:
    def test_negative_volume_detected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = -100
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "negative_volume" in cats


class TestCheckIntegrityNanSemantics:
    def test_volume_positive_with_nan_ohlcv(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("open")] = np.nan
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "nan_semantics" in cats

    def test_volume_zero_with_non_nan_ohlcv(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = 0
        # OHLC still populated -> violation
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "nan_semantics" in cats


class TestCheckIntegrityOhlcvConstraints:
    def test_ohlcv_violation_detected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        # high < low -> violation
        df.iloc[0, df.columns.get_loc("high")] = 50.0
        df.iloc[0, df.columns.get_loc("low")] = 200.0
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "ohlcv_violation" in cats


class TestCheckIntegrityNegativePrices:
    def test_negative_price_warning(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("low")] = -1.0
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "negative_price" in cats
        issue = next(i for i in report.issues if i.category == "negative_price")
        assert issue.severity == "warning"


class TestCheckIntegritySummary:
    def test_summary_counts_multiple_categories(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        # Create two different violations
        df.iloc[0, df.columns.get_loc("volume")] = -10
        df.iloc[1, df.columns.get_loc("low")] = -5.0
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        assert not report.is_clean
        assert "negative_volume" in report.summary
        assert "negative_price" in report.summary

    def test_single_row_df_passes_monotonic_and_resolution(self) -> None:
        grid = _grid("2024-01-02 09:30", 1)
        df = make_ohlcv("2024-01-02 09:30", periods=1, freq="1min")
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        # Single row: monotonic and resolution checks are trivially satisfied
        assert report.is_clean
