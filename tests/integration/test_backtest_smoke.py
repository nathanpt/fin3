"""Smoke test: run a minimal SMA crossover backtest against stored 1d data.

Tests the full read path that a consumer would use:
  ArcticStorage.read() -> pandas DataFrame -> compute signals -> verify output.

Requires MinIO with data already stored in ``equities-1d-databento``
(downloaded via scripts/download_symbols.py). No Databento API calls.
"""

from __future__ import annotations

import pandas as pd
import pytest

from fin3.schemas import AssetType, Resolution, library_name
from fin3.storage.arctic import ArcticStorage


LIBRARY_1D = library_name(AssetType.EQUITY_US, Resolution.ONE_DAY, "databento")


@pytest.fixture(scope="module")
def aapl_daily(minio_storage: ArcticStorage) -> pd.DataFrame:
    """Read AAPL daily bars from MinIO. Skip if not present."""
    df = minio_storage.read(LIBRARY_1D, "AAPL")
    if df is None or df.empty:
        pytest.skip("AAPL 1d data not in MinIO — run download_symbols.py first")
    return df


class TestSmaCrossoverBacktest:
    """Minimal backtest: 20/50 SMA crossover on AAPL daily data."""

    def test_compute_sma_signals(self, aapl_daily: pd.DataFrame) -> None:
        close = aapl_daily["close"].dropna()
        assert len(close) > 50, "Need at least 50 non-null bars for SMA50"

        sma20 = close.rolling(20).mean()
        sma50 = close.rolling(50).mean()

        # Signals: +1 when SMA20 crosses above SMA50, -1 when below
        cross = (sma20 > sma50).astype(int) - (sma20 < sma50).astype(int)
        signals = cross.diff().fillna(0).astype(int)

        # At least some non-zero signals over ~2000 bars
        nonzero = signals[signals != 0]
        assert len(nonzero) > 0, "Expected at least one crossover signal"

        # SMA values should be valid where we have enough data
        assert sma20.iloc[19:].notna().all(), "SMA20 has unexpected NaN"
        assert sma50.iloc[49:].notna().all(), "SMA50 has unexpected NaN"

        # Basic sanity: close prices positive
        assert (close > 0).all(), "Close prices should be positive"

    def test_daily_returns_distribution(self, aapl_daily: pd.DataFrame) -> None:
        """Verify daily returns have reasonable statistical properties."""
        close = aapl_daily["close"].dropna()
        returns = close.pct_change().dropna()

        assert len(returns) > 100
        # Mean daily return should be within ±0.5% for a large-cap stock
        assert abs(returns.mean()) < 0.01
        # Daily volatility typically 1-3% for equities
        assert 0.005 < returns.std() < 0.10
        # No infinite or NaN returns
        assert returns.notna().all()
        assert (returns.abs() < 1.0).all(), "No 100%+ daily moves expected"

    def test_volume_not_all_zero(self, aapl_daily: pd.DataFrame) -> None:
        """Volume should be positive on trading days."""
        valid = aapl_daily[aapl_daily["close"].notna()]
        assert (valid["volume"] > 0).all(), "Volume should be positive on trading days"

    def test_ohlcv_schema(self, aapl_daily: pd.DataFrame) -> None:
        """Verify stored data has the expected schema."""
        for col in ("open", "high", "low", "close", "volume"):
            assert col in aapl_daily.columns, f"Missing column: {col}"
        assert isinstance(aapl_daily.index, pd.DatetimeIndex)
        assert aapl_daily.index.tz is not None, "Index should be timezone-aware"
