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

from fin3.core import MarketDataFetcher
from fin3.schemas import AssetType, Resolution, library_name
from fin3.storage.arctic import ArcticStorage

from .conftest import RANGE_1D, RANGE_1H, RANGE_1M, SYMBOL_EQUITY


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

    def test_02_data_stored_in_arctic(
        self, minio_storage: ArcticStorage
    ) -> None:
        """Verify the 1m data from test_01 is persisted (no additional API call)."""
        lib = library_name(AssetType.EQUITY_US, Resolution.ONE_MINUTE, "databento")
        stored = minio_storage.read(lib, SYMBOL_EQUITY)
        assert stored is not None
        assert not stored.empty
        assert "open" in stored.columns

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
        if not valid.empty:
            assert (valid["low"] <= valid["open"]).all()
            assert (valid["open"] <= valid["high"]).all()
            assert (valid["low"] <= valid["close"]).all()
            assert (valid["close"] <= valid["high"]).all()

    def test_04_get_data_equity_1d(
        self, fetcher: MarketDataFetcher
    ) -> None:
        """Fetch 1 day of daily bars — new API call, 1 bar."""
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
        assert len(result) >= 1

    def test_05_get_data_multi_symbol_1h(
        self, fetcher: MarketDataFetcher
    ) -> None:
        """Fetch 1 hour of hourly bars for AAPL + MSFT — new API calls, 2 bars."""
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
        symbols_in_result = result.columns.get_level_values(0).unique().tolist()
        assert "AAPL" in symbols_in_result
        assert "MSFT" in symbols_in_result

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
