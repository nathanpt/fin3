"""Direct unit tests for core.py helper functions."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from fin3.core import (
    MarketDataFetcher,
    _aggregate_bars,
    _align_symbols,
    _assert_boundary,
    _reindex,
    _snap_to_grid,
    _write_metadata,
)
from fin3.exceptions import BoundaryMismatchError
from fin3.schemas import AssetType, OHLCV_COLUMNS, Resolution
from fin3.storage.arctic import ArcticStorage
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
        # Exact column contract: every OHLCV field under the one symbol,
        # and the index is the symbol's own timestamps, unchanged.
        assert list(result.columns) == [("BTC", col) for col in OHLCV_COLUMNS]
        assert result.index.equals(df.index)
        assert result.index.tz is not None

    def test_multi_symbol_union_index(self) -> None:
        df1 = make_ohlcv("2024-01-01 00:00", periods=2, freq="1h")
        df2 = make_ohlcv("2024-01-01 01:00", periods=2, freq="1h")
        result = _align_symbols({"BTC": df1, "ETH": df2})
        assert len(result) == 3  # union of [00, 01] and [01, 02]
        symbols = result.columns.get_level_values(0).unique().tolist()
        assert "BTC" in symbols
        assert "ETH" in symbols
        # Exact union index, not just the right length.
        assert result.index.equals(
            pd.date_range("2024-01-01 00:00", periods=3, freq="1h", tz="UTC")
        )
        # Exact (symbol, field) columns: pd.concat(axis=1).sort_index(axis=1)
        # sorts symbols, then fields alphabetically within each symbol.
        assert list(result.columns) == [
            (sym, col) for sym in ["BTC", "ETH"] for col in ["close", "high", "low", "open", "volume"]
        ]
        # NaN padding outside each symbol's own span; real values inside it.
        assert pd.isna(result.loc[pd.Timestamp("2024-01-01 02:00", tz="UTC"), ("BTC", "close")])
        assert result.loc[pd.Timestamp("2024-01-01 00:00", tz="UTC"), ("BTC", "close")] == df1["close"].iloc[0]
        assert pd.isna(result.loc[pd.Timestamp("2024-01-01 00:00", tz="UTC"), ("ETH", "close")])
        assert result.loc[pd.Timestamp("2024-01-01 02:00", tz="UTC"), ("ETH", "close")] == df2["close"].iloc[1]

    def test_empty_dict_returns_empty(self) -> None:
        result = _align_symbols({})
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_empty_union_keeps_column_contract(self) -> None:
        result = _align_symbols({"AAPL": pd.DataFrame(columns=list(OHLCV_COLUMNS))})
        assert len(result) == 0
        assert list(result.columns) == [
            (sym, col) for sym in ["AAPL"] for col in OHLCV_COLUMNS
        ]
        assert isinstance(result.index, pd.DatetimeIndex)
        assert result.index.tz is not None


class TestAssertBoundary:
    def test_mismatch_raises(self) -> None:
        df = make_ohlcv("2024-01-01 01:00", periods=2, freq="1h")
        grid = pd.date_range("2024-01-01 00:00", periods=3, freq="1h", tz="UTC")
        with pytest.raises(BoundaryMismatchError):
            _assert_boundary(df, grid)

    def test_end_boundary_only_mismatch_raises(self) -> None:
        """Start matches the grid but the end falls short — must still raise."""
        # df spans [00:00, 01:00]; grid ends at 02:00.
        df = make_ohlcv("2024-01-01 00:00", periods=2, freq="1h")
        grid = pd.date_range("2024-01-01 00:00", periods=3, freq="1h", tz="UTC")
        with pytest.raises(BoundaryMismatchError) as exc_info:
            _assert_boundary(df, grid)
        assert exc_info.value.actual_start == exc_info.value.expected_start
        assert exc_info.value.actual_end == pd.Timestamp("2024-01-01 01:00", tz="UTC")
        assert exc_info.value.expected_end == pd.Timestamp("2024-01-01 02:00", tz="UTC")

    def test_empty_passes(self) -> None:
        _assert_boundary(pd.DataFrame(), pd.DatetimeIndex([], tz="UTC"))


class TestWriteMetadata:
    def test_metadata_keys(self) -> None:
        before = datetime.now(timezone.utc)
        meta = _write_metadata(
            "AAPL",
            MagicMock(),
            pd.Timestamp("2024-01-01"),
            pd.Timestamp("2024-01-02"),
        )
        after = datetime.now(timezone.utc)
        # Exact keys — no extras, no renames.
        assert set(meta) == {"downloaded_at", "provider", "symbol", "date_range"}
        assert meta["symbol"] == "AAPL"
        # provider is the concrete class name of the provider object.
        assert meta["provider"] == "MagicMock"
        # date_range is the 'start/end' pair as ISO-8601 strings.
        assert meta["date_range"] == "2024-01-01T00:00:00/2024-01-02T00:00:00"
        # downloaded_at is an ISO-parseable, tz-aware UTC timestamp taken
        # between the two clock reads.
        downloaded = datetime.fromisoformat(meta["downloaded_at"])
        assert downloaded.tzinfo is not None
        assert before <= downloaded <= after


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

    def test_non_utc_provider_index_snaps(self) -> None:
        """Provider bars in a non-UTC timezone take the tz_convert branch:
        they are converted to UTC before the date mapping, so NY midnight
        (= 05:00 UTC) matches its own trading day and snaps to market-open."""
        df = pd.DataFrame(
            {"open": [100, 101], "high": [101, 102], "low": [99, 100], "close": [100, 101], "volume": [1000, 1100]},
            index=pd.DatetimeIndex(
                ["2024-01-02 00:00", "2024-01-03 00:00"], tz="America/New_York"
            ),
        )
        grid = pd.DatetimeIndex(
            ["2024-01-02 14:30", "2024-01-03 14:30"], tz="UTC"
        )

        result = _snap_to_grid(df, grid, Resolution.ONE_DAY)
        assert result.index[0] == pd.Timestamp("2024-01-02 14:30", tz="UTC")
        assert result.index[1] == pd.Timestamp("2024-01-03 14:30", tz="UTC")


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

    def test_exactly_half_spacing_snaps(self) -> None:
        """A bar EXACTLY median_spacing/2 away from a grid point snaps:
        ``Index.get_indexer`` tolerance is an inclusive (<=) bound, verified
        empirically. Grid spacing is 1h, so tolerance is 30min and 14:00 is
        exactly 30min from the 14:30 grid point."""
        df = pd.DataFrame(
            {"open": [100], "high": [101], "low": [99], "close": [100], "volume": [1000]},
            index=pd.DatetimeIndex(["2024-01-02 14:00"], tz="UTC"),
        )
        grid = pd.DatetimeIndex(["2024-01-02 14:30", "2024-01-02 15:30"], tz="UTC")

        result = _snap_to_grid(df, grid, Resolution.ONE_HOUR)
        assert result.index[0] == pd.Timestamp("2024-01-02 14:30", tz="UTC")

    def test_just_beyond_half_spacing_kept_original(self) -> None:
        """One minute beyond the half-spacing tolerance does not snap:
        13:59 is 31min from the 14:30 grid point (> 30min tolerance)."""
        df = pd.DataFrame(
            {"open": [100], "high": [101], "low": [99], "close": [100], "volume": [1000]},
            index=pd.DatetimeIndex(["2024-01-02 13:59"], tz="UTC"),
        )
        grid = pd.DatetimeIndex(["2024-01-02 14:30", "2024-01-02 15:30"], tz="UTC")

        result = _snap_to_grid(df, grid, Resolution.ONE_HOUR)
        assert result.index[0] == pd.Timestamp("2024-01-02 13:59", tz="UTC")

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
        # Unchanged content AND a copy: callers may mutate the result.
        pd.testing.assert_frame_equal(result, df, check_freq=False)
        assert result is not df

    def test_empty_grid_passes_through(self) -> None:
        df = make_ohlcv("2024-01-02", periods=3, freq="1h")
        grid = pd.DatetimeIndex([], tz="UTC")
        result = _snap_to_grid(df, grid, Resolution.ONE_HOUR)
        # Should return data unchanged (copy) when grid is empty
        pd.testing.assert_frame_equal(result, df, check_freq=False)
        assert result is not df


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
        # Empty is not column-less: the resampled frame keeps the OHLCV
        # column contract and an empty UTC DatetimeIndex.
        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert isinstance(result.index, pd.DatetimeIndex)
        assert result.index.tz is not None

    def test_all_nan_bin_dropped_across_session_gap(self) -> None:
        """1m bars spanning a session gap, resampled to 5m: the empty
        session-gap bins (no bars -> NaN close) are dropped via
        ``dropna(subset=["close"])``, leaving only bins that contain bars."""
        morning = pd.date_range("2024-01-02 14:30", periods=5, freq="1min", tz="UTC")
        afternoon = pd.date_range("2024-01-02 14:50", periods=5, freq="1min", tz="UTC")
        df = pd.DataFrame(
            {
                "open": [100.0] * 5 + [200.0] * 5,
                "high": [100.5] * 5 + [200.5] * 5,
                "low": [99.5] * 5 + [199.5] * 5,
                "close": [101.0] * 5 + [201.0] * 5,
                "volume": [1000.0] * 10,
            },
            index=morning.append(afternoon),
        )

        result = _aggregate_bars(df, Resolution.FIVE_MINUTE)

        # Bins 14:35..14:45 have no bars -> all-NaN close -> dropped.
        assert list(result.index) == [
            pd.Timestamp("2024-01-02 14:30", tz="UTC"),
            pd.Timestamp("2024-01-02 14:50", tz="UTC"),
        ]
        assert result.iloc[0]["close"] == 101.0
        assert result.iloc[0]["volume"] == 5000.0
        assert result.iloc[1]["close"] == 201.0
        assert result.iloc[1]["volume"] == 5000.0


class TestFillGapSingleBarGate:
    """_fill_gap only aggregates when ``len(raw_df) > 1`` (core.py): a single
    fine-grained bar yields no median spacing, so it must skip _aggregate_bars
    and pass through to storage unchanged."""

    def test_single_1m_bar_for_5m_request_passes_through(self, tmp_path) -> None:
        fetcher = MarketDataFetcher.__new__(MarketDataFetcher)
        fetcher._storage = ArcticStorage.from_lmdb(str(tmp_path / "lmdb"))

        bar = make_ohlcv("2024-01-02 14:30", periods=1, freq="1min")
        provider = MagicMock()
        provider.fetch.return_value = bar

        fetcher._fill_gap(
            "crypto-5m-databento",
            "BTC-USD",
            provider,
            AssetType.CRYPTO.calendar_strategy,
            Resolution.FIVE_MINUTE,
            datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 14, 35, tzinfo=timezone.utc),
            asset_type=AssetType.CRYPTO,
        )

        provider.fetch.assert_called_once()
        stored = fetcher._storage.read("crypto-5m-databento", "BTC-USD")
        assert stored is not None
        # The bar's own values survive verbatim at its grid slot — not
        # resampled (which would at minimum rebuild the index) and not dropped.
        on_grid = stored.loc[pd.Timestamp("2024-01-02 14:30", tz="UTC")]
        assert on_grid["close"] == bar["close"].iloc[0]
        assert on_grid["volume"] == bar["volume"].iloc[0]
        # The rest of the 5m grid is calendar-padded, not aggregated away.
        tail = stored.loc[pd.Timestamp("2024-01-02 14:35", tz="UTC")]
        assert tail["volume"] == 0.0
        assert pd.isna(tail["close"])
