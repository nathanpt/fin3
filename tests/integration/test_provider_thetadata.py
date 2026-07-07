"""ThetaData provider smoke tests against the real API.

Requires a ThetaData API key (SDK >=1.0.9 authenticates with an API key — no
Theta Terminal). The limited **free tier is EOD-only**, so this smoke covers
daily bars only (intraday needs a paid Value tier). Set one of:
  FIN3_PROVIDERS__THETADATA__API_KEY   — fin3-native env key
  THETADATA_API_KEY                    — shortcut

These tests call ``ThetaDataProvider.fetch()`` directly (no MinIO needed) and
use recent, dynamic date ranges (relative to now) so they stay within the
free-tier history window.

.. note::

   The shared ``tests/integration/conftest.py`` sets
   ``collect_ignore_glob = ['*.py']`` unless BOTH the MinIO env and
   ``FIN3_PROVIDERS__DATABENTO__API_KEY`` are set, so this file is not
   collected at all without that env (even though its body never touches
   MinIO). To run it, set the MinIO+Databento env vars (unused here, but
   required for collection) — or run the live probe as a standalone one-liner:
   ``uv run python -c "from thetadata import ThetaClient; ..."``.

Run with:
  uv run pytest tests/integration/test_provider_thetadata.py -m integration -v
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from fin3.config.settings import ThetaDataConfig
from fin3.providers.thetadata import ThetaDataProvider
from fin3.schemas import OHLCV_COLUMNS, Resolution

_API_KEY = os.environ.get("FIN3_PROVIDERS__THETADATA__API_KEY") or os.environ.get(
    "THETADATA_API_KEY"
)

# The shared integration conftest collects only when Databento env is present;
# this module additionally skips when no ThetaData key is configured.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _API_KEY,
        reason="Set FIN3_PROVIDERS__THETADATA__API_KEY or THETADATA_API_KEY to run ThetaData smoke tests",
    ),
]

SYMBOL_EQUITY = "AAPL"


def _recent(days_back: int) -> datetime:
    """Return a UTC datetime ``days_back`` days before now."""
    return datetime.now(timezone.utc) - timedelta(days=days_back)


def _assert_valid_ohlcv(df: pytest.fixture, *, min_rows: int = 1) -> None:
    """Assert ``df`` is a well-formed OHLCV DataFrame with valid bar geometry."""
    assert not df.empty
    assert list(df.columns) == list(OHLCV_COLUMNS)
    assert df.index.is_monotonic_increasing  # type: ignore[attr-defined]
    assert not df.index.has_duplicates  # type: ignore[attr-defined]
    assert str(df.index.tz) == "UTC"  # type: ignore[attr-defined]
    assert (df["low"] <= df["open"]).all()
    assert (df["open"] <= df["high"]).all()
    assert (df["low"] <= df["close"]).all()
    assert (df["close"] <= df["high"]).all()
    assert (df["volume"] >= 0).all()
    # Timestamps must land on real, recent dates — guards against the
    # seconds-vs-milliseconds normalisation bug (would yield 1970 dates).
    assert df.index[0].year >= 2020  # type: ignore[attr-defined]
    assert len(df) >= min_rows


@pytest.fixture(scope="module")
def theetadata_provider() -> ThetaDataProvider:
    return ThetaDataProvider(ThetaDataConfig(api_key=_API_KEY or "missing"))


class TestThetaDataProviderSmoke:
    """Verify ThetaDataProvider against the live ThetaData endpoint."""

    def test_fetch_ohlcv_1d(self, theetadata_provider: ThetaDataProvider) -> None:
        """Recent daily bars return a well-formed OHLCV DataFrame.

        The free tier serves EOD history; a 45-day window guarantees several
        trading days well inside the free-tier window.
        """
        df = theetadata_provider.fetch(
            SYMBOL_EQUITY,
            _recent(45),
            _recent(1),
            Resolution.ONE_DAY,
        )
        _assert_valid_ohlcv(df, min_rows=5)

    def test_get_instrument_bounds(
        self, theetadata_provider: ThetaDataProvider
    ) -> None:
        """The listing-date probe returns an effective-earliest-bar timestamp.

        On the limited free tier, the probe resolves to the plan's history
        boundary (~2 years EOD), not the symbol's true listing date (AAPL IPO
        1980-12-12). It is still a usable lower bound for gap detection (the
        effective first accessible bar).
        """
        bounds = theetadata_provider.get_instrument_bounds(SYMBOL_EQUITY)
        assert "ipo_date" in bounds
        assert "delist_date" in bounds
        assert bounds["delist_date"] is None
        ipo = bounds["ipo_date"]
        assert ipo is not None
        # Free-tier boundary (~2024); a paid key would resolve 1980 instead.
        assert ipo.year >= 2024
