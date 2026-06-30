"""Massive (formerly Polygon.io) provider smoke tests against the real API.

Requires a Massive API key (paid subscription; a limited free tier exists).
Set one of:
  FIN3_PROVIDERS__MASSIVE__API_KEY   — fin3-native env key
  MASSIVE_API_KEY                    — shortcut

These tests call ``MassiveProvider.fetch()`` directly (no MinIO needed) and
use small recent date ranges to minimise API usage.

Run with:
  uv run pytest tests/integration/test_provider_massive.py -m integration -v
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from fin3.config.settings import MassiveConfig
from fin3.providers.massive import MassiveProvider
from fin3.schemas import OHLCV_COLUMNS, Resolution

_API_KEY = os.environ.get("FIN3_PROVIDERS__MASSIVE__API_KEY") or os.environ.get(
    "MASSIVE_API_KEY"
)

# The shared integration conftest collects only when Databento env is present;
# this module additionally skips when no Massive key is configured.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _API_KEY,
        reason="Set FIN3_PROVIDERS__MASSIVE__API_KEY or MASSIVE_API_KEY to run Massive smoke tests",
    ),
]

# A recent trading window. Massive's aggs endpoint serves consolidated US
# equity bars; AAPL daily is always available on the free tier.
SYMBOL_EQUITY = "AAPL"
RANGE_1D = (
    datetime(2024, 6, 3, 0, 0, tzinfo=timezone.utc),
    datetime(2024, 6, 7, 0, 0, tzinfo=timezone.utc),
)


@pytest.fixture(scope="module")
def massive_provider() -> MassiveProvider:
    return MassiveProvider(MassiveConfig(api_key=_API_KEY or "missing"))


class TestMassiveProviderSmoke:
    """Verify MassiveProvider.fetch() returns valid OHLCV DataFrames."""

    def test_fetch_ohlcv_1d(self, massive_provider: MassiveProvider) -> None:
        start, end = RANGE_1D
        df = massive_provider.fetch(SYMBOL_EQUITY, start, end, Resolution.ONE_DAY)

        assert not df.empty
        assert list(df.columns) == list(OHLCV_COLUMNS)
        assert df.index.is_monotonic_increasing
        assert str(df.index.tz) == "UTC"  # type: ignore[attr-defined]
        # OHLCV constraints
        assert (df["low"] <= df["open"]).all()
        assert (df["open"] <= df["high"]).all()
        assert (df["low"] <= df["close"]).all()
        assert (df["close"] <= df["high"]).all()
        assert (df["volume"] >= 0).all()
        # Timestamps must land on real dates, not 1970 — guards the
        # seconds-vs-milliseconds normalisation bug.
        assert df.index[0].year >= 2020  # type: ignore[attr-defined]

    def test_fetch_no_data_returns_empty(self, massive_provider: MassiveProvider) -> None:
        """Requesting a future date range returns an empty canonical DataFrame."""
        df = massive_provider.fetch(
            SYMBOL_EQUITY,
            datetime(2099, 1, 1, tzinfo=timezone.utc),
            datetime(2099, 1, 2, tzinfo=timezone.utc),
            Resolution.ONE_DAY,
        )
        assert df.empty
        assert list(df.columns) == list(OHLCV_COLUMNS)

    def test_get_instrument_bounds(self, massive_provider: MassiveProvider) -> None:
        bounds = massive_provider.get_instrument_bounds(SYMBOL_EQUITY)
        assert "ipo_date" in bounds
        assert "delist_date" in bounds
        # AAPL listed 1980-12-12; the earliest-daily-agg probe should resolve it.
        ipo = bounds["ipo_date"]
        assert ipo is not None
        assert ipo.year <= 1981
        assert bounds["delist_date"] is None
