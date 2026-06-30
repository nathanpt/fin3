"""Massive (formerly Polygon.io) provider smoke tests against the real API.

Requires a Massive API key. The free tier is sufficient: it grants recent
history (~2 years) at 1m/1d resolution. Set one of:
  FIN3_PROVIDERS__MASSIVE__API_KEY   — fin3-native env key
  MASSIVE_API_KEY                    — shortcut

These tests call ``MassiveProvider.fetch()`` directly (no MinIO needed) and
use recent, dynamic date ranges (relative to now) so they stay within the
free-tier history window.

Rate limits: the free tier allows ~5 calls/min. The pagination test issues
several page requests and may exercise the provider's 429 retry/backoff —
that is expected.

Run with:
  uv run pytest tests/integration/test_provider_massive.py -m integration -v
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

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

SYMBOL_EQUITY = "AAPL"

# The free tier allows ~5 calls/min. Integration tests pace themselves by
# retrying 429s with a backoff long enough to wait out the per-minute window
# (max_backoff > 60s). This makes the suite slow but reliable on a rate-limited
# key; a paid key breezes through.
_RATE_TOLERANT = {
    "max_retries": 8,
    "initial_backoff": 2.0,
    "max_backoff": 70.0,
}


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
def massive_provider() -> MassiveProvider:
    return MassiveProvider(
        MassiveConfig(api_key=_API_KEY or "missing", **_RATE_TOLERANT)
    )


class TestMassiveProviderSmoke:
    """Verify MassiveProvider against the live Massive aggregates endpoint."""

    def test_fetch_ohlcv_1d(self, massive_provider: MassiveProvider) -> None:
        """Recent daily bars return a well-formed OHLCV DataFrame."""
        # 30-day window guarantees several trading days within the free tier.
        df = massive_provider.fetch(
            SYMBOL_EQUITY,
            _recent(30),
            _recent(1),
            Resolution.ONE_DAY,
        )
        _assert_valid_ohlcv(df, min_rows=3)

    def test_fetch_ohlcv_1m(self, massive_provider: MassiveProvider) -> None:
        """Minute bars are accessible on the free tier and normalize correctly."""
        # ~4-day window covers at least one full trading day even across a weekend.
        df = massive_provider.fetch(
            SYMBOL_EQUITY,
            _recent(4),
            _recent(1),
            Resolution.ONE_MINUTE,
        )
        _assert_valid_ohlcv(df, min_rows=100)

    def test_pagination_follows_next_url(
        self, massive_provider: MassiveProvider
    ) -> None:
        """A tiny page size forces multi-page cursor pagination."""
        small_page = MassiveProvider(
            MassiveConfig(
                api_key=_API_KEY or "missing", request_limit=2, **_RATE_TOLERANT
            )
        )
        # ~7 days -> ~5 trading days, paged 2-at-a-time (3 page requests).
        df = small_page.fetch(
            SYMBOL_EQUITY,
            _recent(7),
            _recent(1),
            Resolution.ONE_DAY,
        )
        # More rows than a single 2-bar page => pagination actually happened,
        # results are complete, ordered, and deduplicated across pages.
        _assert_valid_ohlcv(df, min_rows=3)

    def test_get_instrument_bounds(self, massive_provider: MassiveProvider) -> None:
        """The listing-date probe returns an effective-earliest-bar timestamp.

        On limited plans (incl. the free tier), Massive returns HTTP 200 with
        plan-truncated results rather than an error, so this resolves to the
        plan's history boundary (e.g. ~2024-07 on the free tier as of mid-2026),
        not the symbol's true listing date (AAPL IPO 1980-12-12). It is still a
        usable lower bound for gap detection (the effective first accessible bar).
        """
        bounds = massive_provider.get_instrument_bounds(SYMBOL_EQUITY)
        assert "ipo_date" in bounds
        assert "delist_date" in bounds
        assert bounds["delist_date"] is None
        ipo = bounds["ipo_date"]
        assert ipo is not None
        # Free-tier boundary (~2024-07); a paid key would resolve 1980 instead.
        assert ipo.year >= 2024
