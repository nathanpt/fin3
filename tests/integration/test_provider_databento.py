"""Databento provider smoke tests against the real API.

Uses minimal date ranges (5-min windows) to keep costs negligible.
Total API calls: 3 OHLCV fetches (~7 bars) + 1 definition fetch (limit=1).

Run with:
  uv run pytest tests/integration/test_provider_databento.py -m integration -v
"""

from datetime import datetime, timezone

from fin3.providers.databento import DatabentoProvider
from fin3.schemas import Resolution

from .conftest import RANGE_1D, RANGE_1H, RANGE_1M, SYMBOL_EQUITY


class TestDatabentoProviderSmoke:
    """Verify DatabentoProvider.fetch() returns valid DataFrames for each schema."""

    def test_fetch_ohlcv_1m(self, databento_provider: DatabentoProvider) -> None:
        start, end = RANGE_1M
        df = databento_provider.fetch(SYMBOL_EQUITY, start, end, Resolution.ONE_MINUTE)

        assert not df.empty
        for col in ("open", "high", "low", "close", "volume"):
            assert col in df.columns
        assert df.index.is_monotonic_increasing
        assert str(df.index.tz) == "UTC"  # type: ignore[attr-defined]
        # OHLCV constraints
        assert (df["low"] <= df["open"]).all()
        assert (df["open"] <= df["high"]).all()
        assert (df["low"] <= df["close"]).all()
        assert (df["close"] <= df["high"]).all()
        assert (df["volume"] >= 0).all()

    def test_fetch_ohlcv_1h(self, databento_provider: DatabentoProvider) -> None:
        start, end = RANGE_1H
        df = databento_provider.fetch(SYMBOL_EQUITY, start, end, Resolution.ONE_HOUR)

        assert not df.empty
        for col in ("open", "high", "low", "close", "volume"):
            assert col in df.columns
        assert str(df.index.tz) == "UTC"  # type: ignore[attr-defined]

    def test_fetch_ohlcv_1d(self, databento_provider: DatabentoProvider) -> None:
        start, end = RANGE_1D
        df = databento_provider.fetch(SYMBOL_EQUITY, start, end, Resolution.ONE_DAY)

        assert not df.empty
        for col in ("open", "high", "low", "close", "volume"):
            assert col in df.columns
        assert len(df) >= 1

    def test_fetch_no_data_returns_empty(self, databento_provider: DatabentoProvider) -> None:
        """Requesting a future date range should return an empty DataFrame (free)."""
        df = databento_provider.fetch(
            SYMBOL_EQUITY,
            datetime(2099, 1, 1, tzinfo=timezone.utc),
            datetime(2099, 1, 2, tzinfo=timezone.utc),
            Resolution.ONE_MINUTE,
        )
        assert df.empty

    def test_get_instrument_bounds(self, databento_provider: DatabentoProvider) -> None:
        bounds = databento_provider.get_instrument_bounds(SYMBOL_EQUITY)
        assert "ipo_date" in bounds
        assert "delist_date" in bounds
        # Databento's equity definition records do not reliably expose IPO/listing
        # dates for raw symbols. fin3 intentionally leaves ipo_date nullable and
        # relies on MetadataStore's discovery fallback when it needs an effective
        # first available bar.
        assert bounds["ipo_date"] is None
