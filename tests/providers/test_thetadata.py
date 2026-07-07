"""Tests for ThetaDataProvider with mocked thetadata SDK."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from fin3.config.settings import ThetaDataConfig
from fin3.exceptions import ProviderError, ProviderRateLimitError
from fin3.providers.thetadata import (
    ThetaDataProvider,
    _classify_error,
    _normalise,
)
from fin3.schemas import OHLCV_COLUMNS, Resolution


def td_eod_df() -> pd.DataFrame:
    """A thetadata-shaped daily DataFrame: a ``date`` column + OHLCV + extras.

    Mirrors the SDK's pandas output: the timestamp is a *column* (not the
    index) named by the server header, on a plain RangeIndex. Includes an
    extra ``count`` column that ``_normalise`` must drop.
    """
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "open": [185.0, 186.0, 187.0],
            "high": [186.5, 187.5, 188.5],
            "low": [184.5, 185.5, 186.5],
            "close": [186.0, 187.0, 188.0],
            "volume": [50_000_000, 48_000_000, 52_000_000],
            "count": [1200, 1100, 1300],
        }
    )


def td_intraday_df() -> pd.DataFrame:
    """A thetadata-shaped intraday DataFrame: NY-tz ``ms_of_day`` column + OHLCV.

    The SDK converts the intraday timestamp to a Python ``datetime`` in
    America/New_York but keeps the ``ms_of_day`` header; ``_normalise`` must
    convert it to UTC (09:30 ET -> 14:30 UTC in winter EST).
    """
    return pd.DataFrame(
        {
            "ms_of_day": pd.DatetimeIndex(
                ["2024-01-02 09:30:00", "2024-01-02 09:31:00"]
            ).tz_localize("America/New_York"),
            "open": [185.0, 185.2],
            "high": [185.5, 185.6],
            "low": [184.9, 185.1],
            "close": [185.2, 185.4],
            "volume": [1000, 1100],
        }
    )


def make_provider(**overrides: object) -> ThetaDataProvider:
    """Build a ThetaDataProvider without triggering the thetadata import.

    Mirrors the yfinance test pattern: bypass ``__init__`` then set the
    attributes the code path actually reads.
    """
    cfg = ThetaDataConfig(api_key="test-key", **overrides)  # type: ignore[arg-type]
    provider = ThetaDataProvider.__new__(ThetaDataProvider)
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._max_retries = cfg.max_retries
    provider._initial_backoff = cfg.initial_backoff
    provider._max_backoff = cfg.max_backoff
    provider._timeout = cfg.timeout
    return provider


def _stub_eod(provider: ThetaDataProvider, df: pd.DataFrame) -> MagicMock:
    """Wire ``provider._client.stock_history_eod(...)`` to return *df*."""
    provider._client.stock_history_eod.return_value = df  # type: ignore[union-attr]
    return provider._client.stock_history_eod  # type: ignore[union-attr]


def _stub_ohlc(provider: ThetaDataProvider, df: pd.DataFrame) -> MagicMock:
    """Wire ``provider._client.stock_history_ohlc(...)`` to return *df*."""
    provider._client.stock_history_ohlc.return_value = df  # type: ignore[union-attr]
    return provider._client.stock_history_ohlc  # type: ignore[union-attr]


class TestClassifyError:
    def test_nodata_by_class_name(self) -> None:
        class NoDataFoundError(Exception):
            pass

        assert _classify_error(NoDataFoundError("throttled")) == "nodata"

    def test_nodata_by_message(self) -> None:
        assert _classify_error(Exception("No data found for: stock_history_eod")) == "nodata"

    def test_rate_by_message(self) -> None:
        assert _classify_error(Exception("429 rate limit exceeded")) == "rate"

    def test_rate_by_class_name(self) -> None:
        class RateLimitError(Exception):
            pass

        assert _classify_error(RateLimitError("throttled")) == "rate"

    def test_timeout(self) -> None:
        assert _classify_error(Exception("connection timed out")) == "timeout"

    def test_fatal(self) -> None:
        assert _classify_error(ValueError("unexpected payload")) == "fatal"


class TestNormalise:
    def test_eod_canonical_schema_and_utc_index(self) -> None:
        result = _normalise(td_eod_df())

        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert str(result.index.tz) == "UTC"
        assert len(result) == 3
        # The "count" column must be dropped.
        assert "count" not in result.columns
        # Date column becomes the index (UTC, naive localized).
        assert result.index[0] == pd.Timestamp("2024-01-02", tz="UTC")

    def test_intraday_converts_ny_to_utc(self) -> None:
        result = _normalise(td_intraday_df())

        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert str(result.index.tz) == "UTC"
        # 09:30 America/New_York (EST, UTC-5) -> 14:30 UTC.
        assert result.index[0] == pd.Timestamp("2024-01-02 14:30:00", tz="UTC")

    def test_dedupes_and_sorts(self) -> None:
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    ["2024-01-03", "2024-01-02", "2024-01-02"]
                ),
                "open": [1.0, 2.0, 3.0],
                "high": [1.0, 2.0, 3.0],
                "low": [1.0, 2.0, 3.0],
                "close": [1.0, 2.0, 3.0],
                "volume": [10, 20, 30],
            }
        )
        result = _normalise(df)

        assert len(result) == 2  # duplicate 2024-01-02 collapsed
        assert result.index.is_monotonic_increasing
        # First (kept) duplicate wins (keep="first").
        assert result["open"].iloc[0] == 2.0

    def test_drops_extra_columns(self) -> None:
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02"]),
                "open": [1.0],
                "high": [1.0],
                "low": [1.0],
                "close": [1.0],
                "volume": [1],
                "extra1": [9],
                "extra2": [8],
            }
        )
        result = _normalise(df)

        assert list(result.columns) == list(OHLCV_COLUMNS)


class TestThetaDataFetch:
    def test_daily_routes_to_stock_history_eod(self) -> None:
        provider = make_provider()
        eod = _stub_eod(provider, td_eod_df())

        result = provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 4, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )

        assert len(result) == 3
        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert str(result.index.tz) == "UTC"
        call = eod.call_args
        assert call.kwargs["symbol"] == "AAPL"
        assert call.kwargs["start_date"] == datetime(2024, 1, 2).date()
        assert call.kwargs["end_date"] == datetime(2024, 1, 4).date()

    @pytest.mark.parametrize(
        "resolution,expected",
        [
            (Resolution.ONE_MINUTE, "1m"),
            (Resolution.FIVE_MINUTE, "5m"),
            (Resolution.FIFTEEN_MINUTE, "15m"),
            (Resolution.ONE_HOUR, "1h"),
            (Resolution.FOUR_HOUR, "1h"),  # no native 4h -> fetches 1h
        ],
    )
    def test_intraday_routes_to_stock_history_ohlc(
        self, resolution: Resolution, expected: str
    ) -> None:
        provider = make_provider()
        ohlc = _stub_ohlc(provider, td_intraday_df())

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
            resolution=resolution,
        )

        assert ohlc.called
        assert ohlc.call_args.kwargs["interval"] == expected

    def test_intraday_calls_once_per_nyse_session(self) -> None:
        """A multi-day intraday range issues one OHLC call per trading day."""
        from exchange_calendars import get_calendar

        provider = make_provider()
        ohlc = _stub_ohlc(provider, td_intraday_df())

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 8, tzinfo=timezone.utc),
            resolution=Resolution.FIVE_MINUTE,
        )

        cal = get_calendar("XNYS")
        expected = len(
            cal.sessions_in_range(
                datetime(2024, 1, 2).date(), datetime(2024, 1, 8).date()
            )
        )
        # Skips the weekend (2024-01-06/07); only NYSE sessions are probed.
        assert ohlc.call_count == expected
        assert expected == 5  # 02, 03, 04, 05, 08

    def test_empty_eod_returns_canonical_empty(self) -> None:
        provider = make_provider()
        _stub_eod(provider, pd.DataFrame())

        result = provider.fetch(
            symbol="NOPE",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )

        assert result.empty
        assert list(result.columns) == list(OHLCV_COLUMNS)

    def test_empty_intraday_returns_canonical_empty(self) -> None:
        provider = make_provider()
        _stub_ohlc(provider, pd.DataFrame())

        result = provider.fetch(
            symbol="NOPE",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        assert result.empty
        assert list(result.columns) == list(OHLCV_COLUMNS)

    def test_unsupported_resolution_raises(self) -> None:
        # Clearing the intraday map makes a real intraday resolution
        # (ONE_MINUTE != ONE_DAY, so the daily branch is skipped) fall through
        # to the unsupported path. All six fin3 resolutions are covered in
        # production, so this is defensive only.
        provider = make_provider()
        with patch.dict(
            "fin3.providers.thetadata._INTRADAY_INTERVAL", {}, clear=True
        ):
            with pytest.raises(ProviderError, match="Unsupported resolution"):
                provider.fetch(
                    symbol="AAPL",
                    start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    resolution=Resolution.ONE_MINUTE,
                )


class TestThetaDataRetry:
    def test_retry_on_rate_then_succeeds(self) -> None:
        provider = make_provider(max_retries=3, initial_backoff=0.01)
        fn = MagicMock()
        fn.side_effect = [Exception("429 Too Many Requests"), td_eod_df()]

        with patch("time.sleep") as mock_sleep:
            result = provider._call(fn, symbol="AAPL")

        assert result is not None
        assert len(result) == 3
        assert fn.call_count == 2
        mock_sleep.assert_called_once()

    def test_raises_after_exhausting_retries(self) -> None:
        provider = make_provider(max_retries=2, initial_backoff=0.01)
        fn = MagicMock()
        fn.side_effect = Exception("rate limit hit")

        with patch("time.sleep"):
            with pytest.raises(ProviderRateLimitError):
                provider._call(fn, symbol="AAPL")

        assert fn.call_count == 2

    def test_fatal_error_not_retried(self) -> None:
        provider = make_provider(max_retries=3)
        fn = MagicMock()
        fn.side_effect = ValueError("unexpected payload")

        with pytest.raises(ProviderError, match="unexpected payload"):
            provider._call(fn, symbol="AAPL")

        assert fn.call_count == 1

    def test_nodata_returns_none_without_retry(self) -> None:
        class NoDataFoundError(Exception):
            pass

        provider = make_provider(max_retries=3)
        fn = MagicMock()
        fn.side_effect = NoDataFoundError("No data found for: stock_history_eod")

        result = provider._call(fn, symbol="AAPL")

        assert result is None
        assert fn.call_count == 1


class TestThetaDataCost:
    def test_estimate_cost_is_zero(self) -> None:
        provider = make_provider()
        cost = provider.estimate_cost(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )
        assert cost == 0.0


class TestThetaDataInstrumentBounds:
    def test_returns_listing_date_from_earliest_daily(self) -> None:
        provider = make_provider()
        # Earliest daily bar.
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(["1980-12-12"]),
                "open": [0.5],
                "high": [0.5],
                "low": [0.5],
                "close": [0.5],
                "volume": [1],
            }
        )
        eod = _stub_eod(provider, df)

        bounds = provider.get_instrument_bounds("AAPL")

        call = eod.call_args
        assert call.kwargs["symbol"] == "AAPL"
        assert bounds["delist_date"] is None
        assert bounds["ipo_date"] == datetime(1980, 12, 12, tzinfo=timezone.utc)

    def test_unknown_symbol_returns_none(self) -> None:
        provider = make_provider()
        _stub_eod(provider, pd.DataFrame())

        bounds = provider.get_instrument_bounds("NOPE")

        assert bounds == {"ipo_date": None, "delist_date": None}

    def test_sdk_error_returns_none(self) -> None:
        provider = make_provider()
        eod = _stub_eod(provider, td_eod_df())
        # A rate-limit-shaped error is a ProviderError subclass; the bounds
        # probe catches it and returns {None, None} (yfinance/massive precedent)
        # so the metadata bootstrap falls back to discovery.
        eod.side_effect = Exception("rate limited")

        with patch("time.sleep"):
            bounds = provider.get_instrument_bounds("AAPL")

        assert bounds == {"ipo_date": None, "delist_date": None}


class TestThetaDataInit:
    def test_missing_sdk_raises_provider_error(self) -> None:
        """If the thetadata extra isn't installed, init gives a clear error."""
        with patch.dict("sys.modules", {"thetadata": None}):
            # Force the import inside __init__ to fail.
            with patch("builtins.__import__", side_effect=ImportError("no module")):
                with pytest.raises(ProviderError, match="fin3\\[thetadata\\]"):
                    ThetaDataProvider(ThetaDataConfig(api_key="k"))

    def test_config_defaults(self) -> None:
        cfg = ThetaDataConfig(api_key="k")
        assert cfg.provider_type == "thetadata"
        assert cfg.max_retries == 3
        assert cfg.timeout == 30.0

    def test_config_custom(self) -> None:
        cfg = ThetaDataConfig(api_key="k", max_retries=5, timeout=10.0)
        assert cfg.max_retries == 5
        assert cfg.timeout == 10.0
