"""Tests for library inspection utilities."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fin3.calendar.exchange import ContinuousCalendarStrategy
from fin3.inspect import inspect_library
from fin3.schemas import Resolution
from fin3.storage.arctic import ArcticStorage
from tests.conftest import make_ohlcv


def _write_sym(storage: ArcticStorage, library: str, symbol: str, df: pd.DataFrame) -> None:
    storage.write(library, symbol, df)


class TestInspectEmptyLibrary:
    def test_empty_library_returns_empty_overview(self, storage: ArcticStorage) -> None:
        result = inspect_library(storage, "empty-lib", Resolution.ONE_MINUTE)
        assert result.library == "empty-lib"
        assert result.symbol_count == 0
        assert result.total_rows == 0
        assert result.date_range == (None, None)
        assert result.symbols == []

    def test_empty_library_to_dataframe(self, storage: ArcticStorage) -> None:
        result = inspect_library(storage, "empty-lib", Resolution.ONE_MINUTE)
        df = result.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_empty_library_summary(self, storage: ArcticStorage) -> None:
        result = inspect_library(storage, "empty-lib", Resolution.ONE_MINUTE)
        s = result.summary()
        assert s["symbol_count"] == 0
        assert s["total_rows"] == 0
        assert s["date_range"] == (None, None)
        assert s["symbols_with_issues"] == 0


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


class TestMissingSymbol:
    def test_symbol_not_found_returns_empty_profile(self, storage: ArcticStorage) -> None:
        # Library exists but symbol was deleted / never written
        df = make_ohlcv("2024-01-02 09:30", periods=3, freq="1min")
        _write_sym(storage, "test-lib", "AAPL", df)

        # Read AAPL successfully, but list_symbols only returns AAPL
        result = inspect_library(storage, "test-lib", Resolution.ONE_MINUTE)
        assert len(result.symbols) == 1
        assert result.symbols[0].symbol == "AAPL"


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

    def test_empty_symbol_has_zero_size(self, storage: ArcticStorage) -> None:
        # list_symbols returns nothing, so no profiles are created
        result = inspect_library(storage, "test-lib", Resolution.ONE_MINUTE)
        assert len(result.symbols) == 0

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
