"""Full end-to-end integration tests via MarketDataFetcher.

Tests the complete pipeline: get_data() -> gap detection -> fetch from
Databento -> validate -> store in MinIO -> return DataFrame.

Cost note:
  Tests are ordered so earlier tests populate ArcticDB. Later tests that
  request the same symbol/resolution/range are cache hits (no API call).
  Total unique Databento API calls across the full suite:
    - OHLCV:  4 fetches (1m x1, 1h x2 symbols, 1d x1) ~ 8 bars
    - Definitions: 3 symbols x1 each (narrow 2024 range, limit=1)
"""

import pandas as pd
import pytest

from fin3.core import MarketDataFetcher
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, library_name
from fin3.storage.arctic import ArcticStorage

from .conftest import RANGE_1D, RANGE_1H, RANGE_1M, SYMBOL_EQUITY

pytestmark = pytest.mark.integration

# test_01 stores its returned frame here so test_02 can prove persistence
# by full value comparison (tests run in declaration order).
_captured_1m: pd.DataFrame | None = None


class TestE2EMarketDataFetcher:
    """End-to-end tests through MarketDataFetcher.get_data().

    Tests run in declaration order. Earlier tests populate ArcticDB,
    so later tests hitting the same range are served from cache.
    """

    def test_01_get_data_equity_1m(
        self, fetcher: MarketDataFetcher, minio_storage: ArcticStorage
    ) -> None:
        """Fetch 5 minutes of 1m bars for AAPL — primes the cache."""
        start, end = RANGE_1M
        result = fetcher.get_data(
            asset_type=AssetType.EQUITY_US,
            provider="databento",
            resolution=Resolution.ONE_MINUTE,
            symbols=[SYMBOL_EQUITY],
            start=start,
            end=end,
        )

        assert isinstance(result, pd.DataFrame)
        assert not result.empty
        assert isinstance(result.columns, pd.MultiIndex)
        assert SYMBOL_EQUITY in result.columns.get_level_values(0)
        fields = result.columns.get_level_values(1).tolist()
        for col in ("open", "high", "low", "close", "volume"):
            assert col in fields
        assert str(result.index.tz) == "UTC"  # type: ignore[attr-defined]

        # Value closure: the returned wide frame must be exactly the stored
        # bars. get_data builds it by re-reading storage over (start, end).
        lib = library_name(AssetType.EQUITY_US, Resolution.ONE_MINUTE, "databento")
        stored = minio_storage.read(lib, SYMBOL_EQUITY)
        assert stored is not None
        assert not stored.empty
        # Stored bars stay inside the requested window (otherwise the full
        # read below would legitimately differ from the ranged re-read).
        assert stored.index.min() >= pd.Timestamp(start)
        assert stored.index.max() <= pd.Timestamp(end)
        # _align_symbols sorts symbol columns alphabetically; with a single
        # symbol the xs slice preserves the stored frame's field order as-is.
        pd.testing.assert_frame_equal(stored, result.xs(SYMBOL_EQUITY, axis=1, level=0))

        global _captured_1m
        _captured_1m = result

    def test_02_data_stored_in_arctic(
        self, minio_storage: ArcticStorage
    ) -> None:
        """The persisted 1m frame must equal what test_01 returned (no API call)."""
        assert _captured_1m is not None, "test_01 must run first and capture its result"
        lib = library_name(AssetType.EQUITY_US, Resolution.ONE_MINUTE, "databento")
        stored = minio_storage.read(lib, SYMBOL_EQUITY)
        assert stored is not None
        assert not stored.empty
        assert list(stored.columns) == list(OHLCV_COLUMNS)
        # Value closure across fixtures: a fresh read sees exactly the bars
        # get_data returned — a rewrite-with-garbage regression cannot hide.
        pd.testing.assert_frame_equal(stored, _captured_1m.xs(SYMBOL_EQUITY, axis=1, level=0))

    def test_03_ohlcv_constraints_cache_hit(
        self, fetcher: MarketDataFetcher
    ) -> None:
        """Verify OHLCV invariants on cached 1m data (no additional API call)."""
        start, end = RANGE_1M
        result = fetcher.get_data(
            asset_type=AssetType.EQUITY_US,
            provider="databento",
            resolution=Resolution.ONE_MINUTE,
            symbols=[SYMBOL_EQUITY],
            start=start,
            end=end,
        )

        df = result.xs(SYMBOL_EQUITY, axis=1, level=0)
        ohlc = ["open", "high", "low", "close"]
        valid_mask = df[ohlc].notna().all(axis=1)  # type: ignore[arg-type]
        valid = df[valid_mask]  # type: ignore[index]
        # The cache-hit path serves the real 1m bars already stored for this
        # window — the valid-bar slice must never be empty here, so these
        # assertions are unconditional.
        assert not valid.empty
        assert (valid["low"] <= valid["open"]).all()
        assert (valid["open"] <= valid["high"]).all()
        assert (valid["low"] <= valid["close"]).all()
        assert (valid["close"] <= valid["high"]).all()
        assert (valid["volume"] >= 0).all()

    def test_04_get_data_equity_1d(
        self, fetcher: MarketDataFetcher
    ) -> None:
        """Fetch 1 day of daily bars — new API call, exactly 1 trading bar."""
        start, end = RANGE_1D
        result = fetcher.get_data(
            asset_type=AssetType.EQUITY_US,
            provider="databento",
            resolution=Resolution.ONE_DAY,
            symbols=[SYMBOL_EQUITY],
            start=start,
            end=end,
        )

        assert isinstance(result, pd.DataFrame)
        assert not result.empty
        # 2024-06-03 is one full XNYS trading day: the 1-day grid holds one
        # bar per session stamped at session open (13:30 UTC in June). The
        # padded 06-04 grid row sits past the read window's end.
        assert len(result) == 1
        assert result.index[0] == pd.Timestamp("2024-06-03 13:30", tz="UTC")
        assert str(result.index.tz) == "UTC"  # type: ignore[attr-defined]
        fields = result.columns.get_level_values(1).unique().tolist()
        assert fields == list(OHLCV_COLUMNS)
        bar = result.xs(SYMBOL_EQUITY, axis=1, level=0).iloc[0]
        assert bar["volume"] > 0  # a real bar, not a volume=0/NaN padded row
        assert bar["low"] <= bar["open"] <= bar["high"]
        assert bar["low"] <= bar["close"] <= bar["high"]

    def test_05_get_data_multi_symbol_1h(
        self, fetcher: MarketDataFetcher
    ) -> None:
        """Fetch 1 hour of hourly bars for AAPL + MSFT — new API calls, 1 bar each."""
        start, end = RANGE_1H
        result = fetcher.get_data(
            asset_type=AssetType.EQUITY_US,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["AAPL", "MSFT"],
            start=start,
            end=end,
        )

        assert isinstance(result, pd.DataFrame)
        assert isinstance(result.columns, pd.MultiIndex)
        # _align_symbols sorts symbol columns alphabetically
        symbols_in_result = result.columns.get_level_values(0).unique().tolist()
        assert symbols_in_result == ["AAPL", "MSFT"]
        # 14:00–15:00 UTC inside the 06-03 session: exactly one hourly grid
        # bar (14:30, market-open offset), identical for both symbols.
        assert result.index[0] == pd.Timestamp("2024-06-03 14:30", tz="UTC")
        assert str(result.index.tz) == "UTC"  # type: ignore[attr-defined]
        for sym in symbols_in_result:
            df = result.xs(sym, axis=1, level=0)
            assert not df.empty, f"{sym} must have bars"
            assert len(df) == 1, f"{sym}: expected exactly 1 hourly bar"
            assert df["volume"].iloc[0] > 0

    def test_06_idempotent_second_call(
        self, fetcher: MarketDataFetcher
    ) -> None:
        """Second call for the same 1h AAPL range — cache hit, no API call."""
        start, end = RANGE_1H

        result1 = fetcher.get_data(
            asset_type=AssetType.EQUITY_US,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=[SYMBOL_EQUITY],
            start=start,
            end=end,
        )

        result2 = fetcher.get_data(
            asset_type=AssetType.EQUITY_US,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=[SYMBOL_EQUITY],
            start=start,
            end=end,
        )

        assert isinstance(result1, pd.DataFrame)
        assert isinstance(result2, pd.DataFrame)
        assert len(result1) == len(result2)
        pd.testing.assert_frame_equal(result1, result2)
