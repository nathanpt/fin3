"""Direct unit tests for core.py helper functions."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from fin3.core import (
    _aggregate_bars,
    _align_symbols,
    _assert_boundary,
    _reindex,
    _snap_to_grid,
    _write_metadata,
)
from fin3.exceptions import BoundaryMismatchError
from fin3.schemas import Resolution
from tests.conftest import make_ohlcv


class TestReindex:
    def test_empty_df_produces_volume_zero(self) -> None:
        grid = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
        result = _reindex(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]), grid)
        assert len(result) == 3
        assert (result["volume"] == 0).all()
        assert result["open"].isna().all()

    def test_fills_missing_bars(self) -> None:
        df = make_ohlcv("2024-01-01 00:00", periods=1, freq="1h")
        grid = pd.date_range("2024-01-01 00:00", periods=3, freq="1h", tz="UTC")
        result = _reindex(df, grid)
        assert len(result) == 3
        assert result.iloc[0]["volume"] > 0
        assert result.iloc[1]["volume"] == 0
        assert result.iloc[2]["volume"] == 0


class TestAlignSymbols:
    def test_single_symbol(self) -> None:
        df = make_ohlcv("2024-01-01", periods=3, freq="1h")
        result = _align_symbols({"BTC": df})
        assert isinstance(result.columns, pd.MultiIndex)

    def test_multi_symbol_union_index(self) -> None:
        df1 = make_ohlcv("2024-01-01 00:00", periods=2, freq="1h")
        df2 = make_ohlcv("2024-01-01 01:00", periods=2, freq="1h")
        result = _align_symbols({"BTC": df1, "ETH": df2})
        assert len(result) == 3  # union of [00, 01] and [01, 02]
        symbols = result.columns.get_level_values(0).unique().tolist()
        assert "BTC" in symbols
        assert "ETH" in symbols

    def test_empty_dict_returns_empty(self) -> None:
        result = _align_symbols({})
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestAssertBoundary:
    def test_mismatch_raises(self) -> None:
        df = make_ohlcv("2024-01-01 01:00", periods=2, freq="1h")
        grid = pd.date_range("2024-01-01 00:00", periods=3, freq="1h", tz="UTC")
        with pytest.raises(BoundaryMismatchError):
            _assert_boundary(df, grid)

    def test_empty_passes(self) -> None:
        _assert_boundary(pd.DataFrame(), pd.DatetimeIndex([], tz="UTC"))


class TestWriteMetadata:
    def test_metadata_keys(self) -> None:
        meta = _write_metadata("AAPL", MagicMock(), pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02"))
        assert "downloaded_at" in meta
        assert "provider" in meta
        assert "symbol" in meta
        assert meta["symbol"] == "AAPL"


class TestSnapToGridDaily:
    """Tests for _snap_to_grid with daily resolution (date-based mapping)."""

    def test_midnight_snapped_to_market_open(self) -> None:
        """Provider returns midnight UTC, grid uses market-open (14:30 UTC)."""
        df = pd.DataFrame(
            {"open": [100], "high": [101], "low": [99], "close": [100], "volume": [1000]},
            index=pd.DatetimeIndex(["2024-01-02 00:00"], tz="UTC"),
        )
        grid = pd.DatetimeIndex(["2024-01-02 14:30"], tz="UTC")

        result = _snap_to_grid(df, grid, Resolution.ONE_DAY)
        assert result.index[0] == pd.Timestamp("2024-01-02 14:30", tz="UTC")

    def test_multiple_days_mapped(self) -> None:
        """Each midnight maps to its corresponding market-open timestamp."""
        df = pd.DataFrame(
            {"open": [100, 101], "high": [101, 102], "low": [99, 100], "close": [100, 101], "volume": [1000, 1100]},
            index=pd.DatetimeIndex(["2024-01-02 00:00", "2024-01-03 00:00"], tz="UTC"),
        )
        grid = pd.DatetimeIndex(["2024-01-02 14:30", "2024-01-03 14:30"], tz="UTC")

        result = _snap_to_grid(df, grid, Resolution.ONE_DAY)
        assert result.index[0] == pd.Timestamp("2024-01-02 14:30", tz="UTC")
        assert result.index[1] == pd.Timestamp("2024-01-03 14:30", tz="UTC")

    def test_unknown_date_kept_as_is(self) -> None:
        """Provider timestamp with no matching grid date is kept unchanged."""
        df = pd.DataFrame(
            {"open": [100], "high": [101], "low": [99], "close": [100], "volume": [1000]},
            index=pd.DatetimeIndex(["2024-03-15 00:00"], tz="UTC"),
        )
        grid = pd.DatetimeIndex(["2024-01-02 14:30"], tz="UTC")

        result = _snap_to_grid(df, grid, Resolution.ONE_DAY)
        assert result.index[0] == pd.Timestamp("2024-03-15 00:00", tz="UTC")


class TestSnapToGridIntraday:
    """Tests for _snap_to_grid with intraday resolution (nearest-neighbor)."""

    def test_whole_hour_snapped_to_market_offsets(self) -> None:
        """Provider returns 14:00, grid has 14:30 -> snapped to 14:30."""
        df = pd.DataFrame(
            {"open": [100], "high": [101], "low": [99], "close": [100], "volume": [1000]},
            index=pd.DatetimeIndex(["2024-01-02 14:00"], tz="UTC"),
        )
        grid = pd.DatetimeIndex(["2024-01-02 14:30", "2024-01-02 15:30"], tz="UTC")

        result = _snap_to_grid(df, grid, Resolution.ONE_HOUR)
        assert result.index[0] == pd.Timestamp("2024-01-02 14:30", tz="UTC")

    def test_out_of_tolerance_kept_original(self) -> None:
        """Provider timestamp too far from any grid point stays unchanged."""
        df = pd.DataFrame(
            {"open": [100], "high": [101], "low": [99], "close": [100], "volume": [1000]},
            index=pd.DatetimeIndex(["2024-01-02 06:00"], tz="UTC"),
        )
        grid = pd.DatetimeIndex(["2024-01-02 14:30", "2024-01-02 15:30"], tz="UTC")

        result = _snap_to_grid(df, grid, Resolution.ONE_HOUR)
        # 06:00 is 8.5h away from 14:30 — way beyond tolerance
        assert result.index[0] == pd.Timestamp("2024-01-02 06:00", tz="UTC")

    def test_collision_keeps_first(self) -> None:
        """Two provider timestamps mapping to same grid point: first gets the grid slot."""
        df = pd.DataFrame(
            {"open": [100, 101], "high": [101, 102], "low": [99, 100], "close": [100, 101], "volume": [1000, 1100]},
            index=pd.DatetimeIndex(["2024-01-02 14:20", "2024-01-02 14:25"], tz="UTC"),
        )
        grid = pd.DatetimeIndex(["2024-01-02 14:30", "2024-01-02 15:30"], tz="UTC")

        result = _snap_to_grid(df, grid, Resolution.FIFTEEN_MINUTE)
        # First row (14:20) snaps to 14:30 and gets the grid slot
        assert result.index[0] == pd.Timestamp("2024-01-02 14:30", tz="UTC")
        assert result.iloc[0]["open"] == 100
        # Second row (14:25) collides — keeps original timestamp (will be
        # dropped or become null after reindex since it's not on the grid)
        assert result.index[1] == pd.Timestamp("2024-01-02 14:25", tz="UTC")


class TestSnapToGridEdgeCases:
    def test_empty_df_passes_through(self) -> None:
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([], tz="UTC"))
        grid = pd.DatetimeIndex(["2024-01-02 14:30"], tz="UTC")
        result = _snap_to_grid(df, grid, Resolution.ONE_DAY)
        assert len(result) == 0

    def test_empty_grid_passes_through(self) -> None:
        df = make_ohlcv("2024-01-02", periods=3, freq="1h")
        grid = pd.DatetimeIndex([], tz="UTC")
        result = _snap_to_grid(df, grid, Resolution.ONE_HOUR)
        # Should return data unchanged (copy) when grid is empty
        pd.testing.assert_frame_equal(result, df, check_freq=False)


class TestAggregateBars:
    """Tests for _aggregate_bars: resample finer bars to coarser resolution."""

    def _make_1m_bars(self) -> pd.DataFrame:
        """10 one-minute bars with distinct OHLCV values for aggregation verification."""
        index = pd.date_range("2024-01-02 14:30", periods=10, freq="1min", tz="UTC")
        return pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0],
                "high": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.5, 108.5, 109.5],
                "low": [99.5, 100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.5, 108.5],
                "close": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0],
                "volume": [1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900],
            },
            index=index,
        )

    def test_1m_to_5m_aggregation(self) -> None:
        """1m bars aggregated to 5m: correct OHLCV values."""
        df = self._make_1m_bars()
        result = _aggregate_bars(df, Resolution.FIVE_MINUTE)

        assert len(result) == 2  # 10 mins / 5 min = 2 bars
        # First 5m bar (14:30-14:34): open=100, high=104.5, low=99.5, close=105, vol=6000
        bar1 = result.iloc[0]
        assert bar1["open"] == 100.0
        assert bar1["high"] == 104.5
        assert bar1["low"] == 99.5
        assert bar1["close"] == 105.0
        assert bar1["volume"] == 6000.0

        # Second 5m bar (14:35-14:39): open=105, high=109.5, low=104.5, close=110, vol=8500
        bar2 = result.iloc[1]
        assert bar2["open"] == 105.0
        assert bar2["high"] == 109.5
        assert bar2["low"] == 104.5
        assert bar2["close"] == 110.0
        assert bar2["volume"] == 8500.0

    def test_1m_to_15m_aggregation(self) -> None:
        """1m bars aggregated to 15m: single bar with all values."""
        df = self._make_1m_bars()
        result = _aggregate_bars(df, Resolution.FIFTEEN_MINUTE)

        assert len(result) == 1  # 10 mins fits in one 15m bar
        bar = result.iloc[0]
        assert bar["open"] == 100.0
        assert bar["high"] == 109.5
        assert bar["low"] == 99.5
        assert bar["close"] == 110.0
        assert bar["volume"] == 14500.0

    def test_no_aggregation_when_spacing_matches(self) -> None:
        """1m bars at 1m resolution: no aggregation, returned as-is."""
        df = self._make_1m_bars()
        result = _aggregate_bars(df, Resolution.ONE_MINUTE)
        assert len(result) == 10
        pd.testing.assert_frame_equal(result, df)

    def test_1h_to_4h_aggregation(self) -> None:
        """1h bars aggregated to 4h (resample aligns to wall-clock boundaries)."""
        # Start at 08:00 so all 4 bars land in one 4h bin (08:00-11:00)
        index = pd.date_range("2024-01-02 08:00", periods=4, freq="1h", tz="UTC")
        df = pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0, 103.0],
                "high": [100.5, 101.5, 102.5, 103.5],
                "low": [99.5, 100.5, 101.5, 102.5],
                "close": [101.0, 102.0, 103.0, 104.0],
                "volume": [1000, 1100, 1200, 1300],
            },
            index=index,
        )
        result = _aggregate_bars(df, Resolution.FOUR_HOUR)
        assert len(result) == 1
        bar = result.iloc[0]
        assert bar["open"] == 100.0
        assert bar["high"] == 103.5
        assert bar["low"] == 99.5
        assert bar["close"] == 104.0
        assert bar["volume"] == 4600.0

    def test_empty_df_passes_through(self) -> None:
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([], tz="UTC"))
        result = _aggregate_bars(df, Resolution.FIVE_MINUTE)
        assert len(result) == 0
