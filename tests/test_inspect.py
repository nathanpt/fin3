"""Tests for library inspection utilities."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from fin3.calendar.exchange import ContinuousCalendarStrategy
from fin3.inspect import SymbolProfile, _profile_symbol, inspect_library
from fin3.schemas import Resolution
from fin3.storage.arctic import ArcticStorage
from fin3.utils.integrity import check_integrity
from tests.conftest import make_empty_ohlcv, make_ohlcv


def _write_sym(storage: ArcticStorage, library: str, symbol: str, df: pd.DataFrame) -> None:
    storage.write(library, symbol, df)


def _ghost_storage(df: pd.DataFrame | None) -> ArcticStorage:
    """Storage double: ``GHOST`` is listed, ``read()`` returns *df* (or None)."""
    storage = MagicMock()
    storage.list_symbols.return_value = ["GHOST"]
    storage.read.return_value = df
    storage.get_symbol_size.return_value = 1234
    return storage


def _assert_empty_ghost_profile(profile: SymbolProfile) -> None:
    """Pin every field of the df is None / empty branch's profile."""
    assert isinstance(profile, SymbolProfile)
    assert profile.symbol == "GHOST"
    assert profile.first_bar is None
    assert profile.last_bar is None
    assert profile.total_bars == 0
    assert profile.null_bars == 0
    assert profile.total_volume == 0.0
    assert profile.size_bytes == 1234
    assert profile.issue_count == 0
    assert profile.issues_summary == {}


class TestInspectEmptyLibrary:
    def test_empty_library_returns_empty_overview(self, storage: ArcticStorage) -> None:
        result = inspect_library(storage, "empty-lib", Resolution.ONE_MINUTE)
        assert result.library == "empty-lib"
        assert result.symbol_count == 0
        assert result.total_rows == 0
        assert result.date_range == (None, None)
        assert result.symbols == []

    def test_empty_library_summary(self, storage: ArcticStorage) -> None:
        result = inspect_library(storage, "empty-lib", Resolution.ONE_MINUTE)
        s = result.summary()
        assert s["symbol_count"] == 0
        assert s["total_rows"] == 0
        assert s["date_range"] == (None, None)
        assert s["symbols_with_issues"] == 0


class TestProfileSymbolWithoutData:
    """Unit tests for the ``df is None`` / empty branch of ``_profile_symbol``.

    A symbol ``list_symbols`` reports but with no readable frame must profile
    as empty — not crash and not fabricate bars.
    """

    def test_read_none_returns_empty_profile(self) -> None:
        profile = _profile_symbol(
            _ghost_storage(None),
            "test-lib",
            "GHOST",
            Resolution.ONE_MINUTE,
            include_integrity=False,
            calendar_strategy=None,
        )
        _assert_empty_ghost_profile(profile)

    def test_empty_frame_returns_empty_profile(self) -> None:
        profile = _profile_symbol(
            _ghost_storage(make_empty_ohlcv()),
            "test-lib",
            "GHOST",
            Resolution.ONE_MINUTE,
            include_integrity=False,
            calendar_strategy=None,
        )
        _assert_empty_ghost_profile(profile)

    def test_read_none_with_integrity_enabled_skips_checks(self) -> None:
        # The empty branch returns before any grid/integrity work runs, so no
        # calendar grid is needed and no issues can be raised.
        profile = _profile_symbol(
            _ghost_storage(None),
            "test-lib",
            "GHOST",
            Resolution.ONE_MINUTE,
            include_integrity=True,
            calendar_strategy=ContinuousCalendarStrategy(),
        )
        _assert_empty_ghost_profile(profile)

    def test_inspect_library_profiles_listed_symbol_without_data(self) -> None:
        storage = MagicMock()
        storage.list_symbols.return_value = ["GHOST"]
        storage.read.return_value = None
        storage.get_symbol_size.return_value = 1234

        result = inspect_library(
            storage,
            "ghost-lib",
            Resolution.ONE_MINUTE,
        )
        assert result.library == "ghost-lib"
        assert result.symbol_count == 1
        assert result.total_rows == 0
        assert result.date_range == (None, None)
        assert len(result.symbols) == 1
        _assert_empty_ghost_profile(result.symbols[0])
        # Integrity not requested: the read is column-pruned to volume only.
        storage.read.assert_called_once_with("ghost-lib", "GHOST", columns=["volume"])


class TestInspectSingleSymbol:
    def test_single_symbol_profile(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        _write_sym(storage, "test-lib", "AAPL", df)

        result = inspect_library(storage, "test-lib", Resolution.ONE_MINUTE)
        assert result.symbol_count == 1
        assert result.total_rows == 10

        sym = result.symbols[0]
        assert sym.symbol == "AAPL"
        assert sym.total_bars == 10
        assert sym.null_bars == 0
        assert sym.total_volume == pytest.approx(10000.0)
        assert sym.issue_count == 0
        assert sym.first_bar == pd.Timestamp("2024-01-02 09:30", tz="UTC")
        assert sym.last_bar == pd.Timestamp("2024-01-02 09:39", tz="UTC")

    def test_symbol_with_null_bars(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[2, df.columns.get_loc("volume")] = 0
        for col in ("open", "high", "low", "close"):
            df.iloc[2, df.columns.get_loc(col)] = np.nan
        _write_sym(storage, "test-lib", "AAPL", df)

        result = inspect_library(storage, "test-lib", Resolution.ONE_MINUTE)
        sym = result.symbols[0]
        assert sym.null_bars == 1
        assert sym.total_bars == 5


class TestInspectMultipleSymbols:
    def test_multiple_symbols(self, storage: ArcticStorage) -> None:
        df1 = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        df2 = make_ohlcv("2024-01-02 10:00", periods=5, freq="1min")
        _write_sym(storage, "test-lib", "AAPL", df1)
        _write_sym(storage, "test-lib", "TSLA", df2)

        result = inspect_library(storage, "test-lib", Resolution.ONE_MINUTE)
        assert result.symbol_count == 2
        assert result.total_rows == 15
        assert result.date_range[0] == pd.Timestamp("2024-01-02 09:30", tz="UTC")
        assert result.date_range[1] == pd.Timestamp("2024-01-02 10:04", tz="UTC")

        symbols = {s.symbol: s for s in result.symbols}
        assert symbols["AAPL"].total_bars == 10
        assert symbols["TSLA"].total_bars == 5

    def test_summary_stats(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        _write_sym(storage, "test-lib", "AAPL", df)
        _write_sym(storage, "test-lib", "TSLA", df)

        result = inspect_library(storage, "test-lib", Resolution.ONE_MINUTE)
        s = result.summary()
        assert s["library"] == "test-lib"
        assert s["symbol_count"] == 2
        assert s["total_rows"] == 10
        assert s["symbols_with_issues"] == 0


class TestInspectWithIntegrity:
    def test_integrity_issues_populated(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        # Corrupt: high < low
        df.iloc[0, df.columns.get_loc("high")] = 50.0
        df.iloc[0, df.columns.get_loc("low")] = 200.0
        _write_sym(storage, "test-lib", "AAPL", df)

        strategy = ContinuousCalendarStrategy()
        result = inspect_library(
            storage,
            "test-lib",
            Resolution.ONE_MINUTE,
            include_integrity=True,
            calendar_strategy=strategy,
        )

        sym = result.symbols[0]
        assert sym.issue_count > 0
        assert "ohlcv_violation" in sym.issues_summary

    def test_integrity_clean_symbol(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        _write_sym(storage, "test-lib", "AAPL", df)

        strategy = ContinuousCalendarStrategy()
        result = inspect_library(
            storage,
            "test-lib",
            Resolution.ONE_MINUTE,
            include_integrity=True,
            calendar_strategy=strategy,
        )

        sym = result.symbols[0]
        assert sym.issue_count == 0
        assert sym.issues_summary == {}

    def test_integrity_summary_with_issues(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df.iloc[0, df.columns.get_loc("volume")] = -10
        _write_sym(storage, "test-lib", "AAPL", df)

        strategy = ContinuousCalendarStrategy()
        result = inspect_library(
            storage,
            "test-lib",
            Resolution.ONE_MINUTE,
            include_integrity=True,
            calendar_strategy=strategy,
        )

        s = result.summary()
        assert s["symbols_with_issues"] == 1


class TestToDataframe:
    def test_to_dataframe_columns_and_shape(self, storage: ArcticStorage) -> None:
        df1 = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        df2 = make_ohlcv("2024-01-02 10:00", periods=5, freq="1min")
        _write_sym(storage, "test-lib", "AAPL", df1)
        _write_sym(storage, "test-lib", "TSLA", df2)

        result = inspect_library(storage, "test-lib", Resolution.ONE_MINUTE)
        out = result.to_dataframe()
        assert isinstance(out, pd.DataFrame)
        assert len(out) == 2
        expected_cols = {
            "symbol", "first_bar", "last_bar", "total_bars",
            "null_bars", "data_bars", "total_volume", "size_bytes",
            "issue_count",
        }
        assert set(out.columns) == expected_cols
        symbols = set(out["symbol"])
        assert symbols == {"AAPL", "TSLA"}

    def test_to_dataframe_sortable_by_issue_count(self, storage: ArcticStorage) -> None:
        df_clean = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df_dirty = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        df_dirty.iloc[0, df_dirty.columns.get_loc("high")] = 50.0
        df_dirty.iloc[0, df_dirty.columns.get_loc("low")] = 200.0

        _write_sym(storage, "test-lib", "CLEAN", df_clean)
        _write_sym(storage, "test-lib", "DIRTY", df_dirty)

        strategy = ContinuousCalendarStrategy()
        result = inspect_library(
            storage,
            "test-lib",
            Resolution.ONE_MINUTE,
            include_integrity=True,
            calendar_strategy=strategy,
        )

        out = result.to_dataframe().sort_values("issue_count", ascending=False)
        assert out.iloc[0]["symbol"] == "DIRTY"
        assert out.iloc[1]["symbol"] == "CLEAN"


class TestInspectParameterValidation:
    def test_include_integrity_without_calendar_strategy_raises(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        _write_sym(storage, "test-lib", "AAPL", df)

        with pytest.raises(ValueError, match="calendar_strategy is required"):
            inspect_library(
                storage,
                "test-lib",
                Resolution.ONE_MINUTE,
                include_integrity=True,
                calendar_strategy=None,
            )


class TestInspectSizeBytes:
    def test_written_symbol_has_nonzero_size(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=100, freq="1min")
        _write_sym(storage, "test-lib", "AAPL", df)

        result = inspect_library(storage, "test-lib", Resolution.ONE_MINUTE)
        sym = result.symbols[0]
        assert sym.size_bytes > 0
        # Delegation: the profile must report exactly what storage reports.
        assert sym.size_bytes == storage.get_symbol_size("test-lib", "AAPL")

    def test_summary_includes_total_size(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        _write_sym(storage, "test-lib", "AAPL", df)
        _write_sym(storage, "test-lib", "TSLA", df)

        result = inspect_library(storage, "test-lib", Resolution.ONE_MINUTE)
        s = result.summary()
        assert s["total_size_bytes"] > 0
        # Total should equal sum of individual sizes
        expected_total = sum(sym.size_bytes for sym in result.symbols)
        assert s["total_size_bytes"] == expected_total

    def test_larger_symbol_has_larger_size(self, storage: ArcticStorage) -> None:
        df_small = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        df_large = make_ohlcv("2024-01-02 09:30", periods=1000, freq="1min")
        _write_sym(storage, "test-lib", "SMALL", df_small)
        _write_sym(storage, "test-lib", "LARGE", df_large)

        result = inspect_library(storage, "test-lib", Resolution.ONE_MINUTE)
        symbols = {s.symbol: s for s in result.symbols}
        assert symbols["LARGE"].size_bytes > symbols["SMALL"].size_bytes


class TestInspectStoredQualityIssues:
    """Historical corruption and advisory outliers visible through inspection."""

    def test_negative_price_error_and_statistical_warning_reported(
        self, storage: ArcticStorage
    ) -> None:
        bad = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        bad.iloc[0, bad.columns.get_loc("low")] = -1.0
        _write_sym(storage, "test-lib", "BAD", bad)

        returns = np.random.default_rng(0).normal(0.0, 0.001, 239)
        closes = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(returns)]))
        closes[120] *= 10.0
        spike_index = pd.date_range("2024-01-01", periods=len(closes), freq="1min", tz="UTC")
        spike = pd.DataFrame(
            {
                "open": closes,
                "high": closes + 0.5,
                "low": closes - 0.5,
                "close": closes,
                "volume": 10.0,
            },
            index=spike_index,
        )
        _write_sym(storage, "test-lib", "SPIKE", spike)

        result = inspect_library(
            storage,
            "test-lib",
            Resolution.ONE_MINUTE,
            include_integrity=True,
            calendar_strategy=ContinuousCalendarStrategy(),
        )
        profiles = {s.symbol: s for s in result.symbols}
        assert profiles["BAD"].issues_summary == {"negative_price": 1}
        assert profiles["BAD"].issue_count == 1
        assert profiles["SPIKE"].issues_summary == {"statistical_outlier": 1}
        assert profiles["SPIKE"].issue_count == 1

        # Direct audit supplies the severities behind those counts.
        bad_audit = check_integrity(bad, bad.index, Resolution.ONE_MINUTE)
        bad_issue = next(i for i in bad_audit.issues if i.category == "negative_price")
        assert bad_issue.severity == "error"
        assert bad_issue.timestamp == bad.index[0]

        spike_audit = check_integrity(spike, spike_index, Resolution.ONE_MINUTE)
        spike_issue = next(i for i in spike_audit.issues if i.category == "statistical_outlier")
        assert spike_issue.severity == "warning"
        assert spike_issue.timestamp == spike_index[120]
