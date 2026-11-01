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
        issue = next(i for i in report.issues if i.category == "missing_bar")
        assert issue.severity == "error"
        # Aggregate finding: no single bar timestamp to attribute.
        assert issue.timestamp is None
        assert report.summary == {"missing_bar": 1}

    def test_partial_data_reports_missing(self) -> None:
        grid = _grid("2024-01-02 09:30", 10)
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        assert not report.is_clean
        cats = [i.category for i in report.issues]
        assert "missing_bar" in cats
        assert report.total_bars_expected == 10
        assert report.total_bars_found == 5
        issue = next(i for i in report.issues if i.category == "missing_bar")
        assert issue.severity == "error"
        assert issue.timestamp is None
        assert report.summary == {"missing_bar": 1}


class TestCheckIntegrityExtraBars:
    def test_extra_bars_reported_as_warning(self) -> None:
        grid = _grid("2024-01-02 09:30", 5)
        df = make_ohlcv("2024-01-02 09:30", periods=8, freq="1min")
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "extra_bar" in cats
        issue = next(i for i in report.issues if i.category == "extra_bar")
        assert issue.severity == "warning"
        # Aggregate finding: no single bar timestamp to attribute.
        assert issue.timestamp is None
        assert report.summary == {"extra_bar": 1}


class TestCheckIntegrityDuplicates:
    def test_duplicate_timestamps(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = pd.concat([df, df.iloc[[0]]])
        grid = pd.DatetimeIndex(df.index)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "duplicate" in cats
        issue = next(i for i in report.issues if i.category == "duplicate")
        assert issue.severity == "error"
        assert issue.timestamp == pd.Timestamp("2024-01-02 09:30", tz="UTC")
        # Appending the 09:30 duplicate after 09:34 also breaks monotonicity,
        # and its -4min spacing is <= the resolution so it counts as within-
        # session and trips the resolution check too.
        assert report.summary == {
            "duplicate": 1,
            "non_monotonic": 1,
            "resolution_mismatch": 1,
        }


class TestCheckIntegrityMonotonicity:
    def test_non_monotonic_timestamps(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.iloc[[1, 0, 2, 3, 4]]
        grid = pd.DatetimeIndex(sorted(df.index))
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "non_monotonic" in cats
        issue = next(i for i in report.issues if i.category == "non_monotonic")
        assert issue.severity == "error"
        assert issue.timestamp is None
        # Compound case: the swapped pair (09:31 before 09:30) yields a -1min
        # spacing, which is <= the resolution so it trips resolution_mismatch
        # alongside non_monotonic — and nothing else.
        assert report.summary == {"non_monotonic": 1, "resolution_mismatch": 1}


class TestCheckIntegrityResolution:
    def test_resolution_mismatch(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_HOUR)
        cats = [i.category for i in report.issues]
        assert "resolution_mismatch" in cats
        issue = next(i for i in report.issues if i.category == "resolution_mismatch")
        assert issue.severity == "error"
        # One aggregated issue despite four bad spacings.
        assert issue.timestamp is None
        assert report.summary == {"resolution_mismatch": 1}


class TestCheckIntegrityNanVolume:
    def test_nan_volume_detected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[2, df.columns.get_loc("volume")] = np.nan
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "nan_volume" in cats
        issue = next(i for i in report.issues if i.category == "nan_volume")
        assert issue.severity == "error"
        # Points at the first bar with NaN volume (the injected row).
        assert issue.timestamp == pd.Timestamp("2024-01-02 09:32", tz="UTC")
        assert report.summary == {"nan_volume": 1}

    def test_missing_volume_column(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df = df.drop(columns=["volume"])
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "missing_column" in cats
        issue = next(i for i in report.issues if i.category == "missing_column")
        assert issue.severity == "error"
        assert issue.timestamp is None
        assert report.summary == {"missing_column": 1}


class TestCheckIntegrityNegativeVolume:
    def test_negative_volume_detected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[3, df.columns.get_loc("volume")] = -100
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "negative_volume" in cats
        issue = next(i for i in report.issues if i.category == "negative_volume")
        assert issue.severity == "error"
        # Points at the first bar with negative volume (the injected row).
        assert issue.timestamp == pd.Timestamp("2024-01-02 09:33", tz="UTC")
        assert report.summary == {"negative_volume": 1}


class TestCheckIntegrityNanSemantics:
    def test_volume_positive_with_nan_ohlcv(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[1, df.columns.get_loc("open")] = np.nan
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "nan_semantics" in cats
        issue = next(i for i in report.issues if i.category == "nan_semantics")
        assert issue.severity == "error"
        # Points at the first volume>0 bar with NaN OHLC (the injected row).
        assert issue.timestamp == pd.Timestamp("2024-01-02 09:31", tz="UTC")
        assert report.summary == {"nan_semantics": 1}

    def test_volume_zero_with_non_nan_ohlcv(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[2, df.columns.get_loc("volume")] = 0
        # OHLC still populated -> violation
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "nan_semantics" in cats
        issue = next(i for i in report.issues if i.category == "nan_semantics")
        assert issue.severity == "error"
        # Points at the first volume=0 bar with populated OHLC (injected row).
        assert issue.timestamp == pd.Timestamp("2024-01-02 09:32", tz="UTC")
        assert report.summary == {"nan_semantics": 1}


class TestCheckIntegrityOhlcvConstraints:
    def test_ohlcv_violation_detected(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        # high < low -> violation
        df.iloc[2, df.columns.get_loc("high")] = 50.0
        df.iloc[2, df.columns.get_loc("low")] = 200.0
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "ohlcv_violation" in cats
        issue = next(i for i in report.issues if i.category == "ohlcv_violation")
        assert issue.severity == "error"
        # Points at the first constraint-violating bar (the injected row).
        assert issue.timestamp == pd.Timestamp("2024-01-02 09:32", tz="UTC")
        assert report.summary == {"ohlcv_violation": 1}


class TestCheckIntegrityNegativePrices:
    def test_negative_price_error(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[4, df.columns.get_loc("low")] = -1.0
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        cats = [i.category for i in report.issues]
        assert "negative_price" in cats
        issue = next(i for i in report.issues if i.category == "negative_price")
        assert issue.severity == "error"
        # Points at the first bar with a negative price (the injected row).
        assert issue.timestamp == pd.Timestamp("2024-01-02 09:34", tz="UTC")
        assert report.summary == {"negative_price": 1}


class TestCheckIntegritySummary:
    def test_summary_counts_multiple_categories(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        # Create two different violations in different rows
        df.iloc[1, df.columns.get_loc("volume")] = -10
        df.iloc[3, df.columns.get_loc("low")] = -5.0
        grid = _grid("2024-01-02 09:30", 5)
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        assert not report.is_clean
        assert report.summary == {"negative_volume": 1, "negative_price": 1}
        neg_vol = next(i for i in report.issues if i.category == "negative_volume")
        assert neg_vol.severity == "error"
        assert neg_vol.timestamp == pd.Timestamp("2024-01-02 09:31", tz="UTC")
        neg_price = next(i for i in report.issues if i.category == "negative_price")
        assert neg_price.severity == "error"
        assert neg_price.timestamp == pd.Timestamp("2024-01-02 09:33", tz="UTC")

    def test_single_row_df_passes_monotonic_and_resolution(self) -> None:
        grid = _grid("2024-01-02 09:30", 1)
        df = make_ohlcv("2024-01-02 09:30", periods=1, freq="1min")
        report = check_integrity(df, grid, Resolution.ONE_MINUTE)
        # Single row: monotonic and resolution checks are trivially satisfied
        assert report.is_clean
