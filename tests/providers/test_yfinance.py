"""Tests for YahooProvider with mocked yfinance."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from fin3.config.settings import YahooConfig
from fin3.exceptions import ProviderError, ProviderRateLimitError
from fin3.providers.yfinance import YahooProvider, _classify_error, _normalise
from fin3.schemas import OHLCV_COLUMNS, Resolution


def yf_daily_df() -> pd.DataFrame:
    """A yfinance-shaped daily DataFrame: capitalised cols, tz-naive date index."""
    return pd.DataFrame(
        {
            "Open": [185.0, 186.0, 187.0],
            "High": [186.5, 187.5, 188.5],
            "Low": [184.5, 185.5, 186.5],
            "Close": [186.0, 187.0, 188.0],
            "Adj Close": [185.9, 186.9, 187.9],
            "Volume": [50_000_000, 48_000_000, 52_000_000],
        },
        index=pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )


def yf_intraday_df() -> pd.DataFrame:
    """A yfinance-shaped intraday DataFrame: tz-aware (exchange-local) index."""
    return pd.DataFrame(
        {
            "Open": [185.0, 185.2],
            "High": [185.5, 185.6],
            "Low": [184.9, 185.1],
            "Close": [185.2, 185.4],
            "Volume": [1_000, 1_100],
        },
        index=pd.DatetimeIndex(
            ["2024-01-02 09:30:00", "2024-01-02 09:31:00"], tz="America/New_York"
        ),
    )


def make_provider(**overrides: object) -> YahooProvider:
    """Build a YahooProvider without triggering the yfinance import.

    Mirrors the databento test pattern: bypass ``__init__`` then set the
    attributes the code path actually reads.
    """
    cfg = YahooConfig(**overrides)  # type: ignore[arg-type]
    provider = YahooProvider.__new__(YahooProvider)
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._auto_adjust = cfg.auto_adjust
    provider._max_retries = cfg.max_retries
    provider._initial_backoff = cfg.initial_backoff
    provider._max_backoff = cfg.max_backoff
    provider._timeout = cfg.timeout
    return provider


def _stub_history(provider: YahooProvider, df: pd.DataFrame) -> MagicMock:
    """Wire ``provider._client.Ticker(symbol).history(...)`` to return *df*."""
    ticker = MagicMock()
    ticker.history.return_value = df
    provider._client.Ticker.return_value = ticker  # type: ignore[union-attr]
    return ticker


class TestClassifyError:
    def test_rate_by_message(self) -> None:
        assert _classify_error(Exception("429 rate limit exceeded")) == "rate"

    def test_rate_by_class_name(self) -> None:
        class YFRateLimitError(Exception):
            pass

        assert _classify_error(YFRateLimitError("throttled")) == "rate"

    def test_timeout(self) -> None:
        assert _classify_error(Exception("connection timed out")) == "timeout"

    def test_fatal(self) -> None:
        assert _classify_error(ValueError("no data found")) == "fatal"


class TestNormalise:
    def test_daily_lowercases_drops_adj_close_localises_utc(self) -> None:
        df = _normalise(yf_daily_df())
        assert list(df.columns) == list(OHLCV_COLUMNS)
        assert "Adj Close" not in df.columns
        assert isinstance(df.index, pd.DatetimeIndex)
        assert str(df.index.tz) == "UTC"
        assert df.index.name is None
        assert df["close"].tolist() == [186.0, 187.0, 188.0]

    def test_intraday_converts_exchange_tz_to_utc(self) -> None:
        df = _normalise(yf_intraday_df())
        assert str(df.index.tz) == "UTC"
        # 09:30 America/New_York (EST, UTC-5) == 14:30 UTC
        assert df.index[0] == pd.Timestamp("2024-01-02 14:30:00", tz="UTC")

    def test_deduplicates_and_sorts(self) -> None:
        df = pd.DataFrame(
            {
                "Open": [101.0, 100.0, 999.0],
                "High": [101.5, 100.5, 999.0],
                "Low": [100.5, 99.5, 999.0],
                "Close": [101.0, 100.0, 999.0],
                "Volume": [100, 50, 1],
            },
            index=pd.DatetimeIndex(["2024-01-03", "2024-01-02", "2024-01-02"]),
        )
        result = _normalise(df)
        assert len(result) == 2
        assert result.index.is_monotonic_increasing
        # First occurrence kept on duplicate open date
        assert result["open"].iloc[0] == 100.0


class TestYahooProviderFetch:
    def test_fetch_returns_normalised_df(self) -> None:
        provider = make_provider()
        ticker = _stub_history(provider, yf_daily_df())

        result = provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 4, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )

        assert len(result) == 3
        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert str(result.index.tz) == "UTC"
        call = ticker.history.call_args
        assert call.kwargs["interval"] == "1d"
        assert call.kwargs["auto_adjust"] is False

    def test_fetch_empty_returns_canonical_empty(self) -> None:
        provider = make_provider()
        _stub_history(provider, pd.DataFrame())

        result = provider.fetch(
            symbol="NOPE",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )

        assert result.empty
        assert list(result.columns) == list(OHLCV_COLUMNS)

    @pytest.mark.parametrize(
        "resolution,expected",
        [
            (Resolution.ONE_MINUTE, "1m"),
            (Resolution.FIVE_MINUTE, "5m"),
            (Resolution.FIFTEEN_MINUTE, "15m"),
            (Resolution.ONE_HOUR, "1h"),
            (Resolution.FOUR_HOUR, "1h"),
            (Resolution.ONE_DAY, "1d"),
        ],
    )
    def test_resolution_mapping(self, resolution: Resolution, expected: str) -> None:
        provider = make_provider()
        ticker = _stub_history(provider, pd.DataFrame())

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=resolution,
        )

        assert ticker.history.call_args.kwargs["interval"] == expected

    def test_auto_adjust_toggle_passed_through(self) -> None:
        provider = make_provider(auto_adjust=True)
        ticker = _stub_history(provider, yf_daily_df())

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )

        assert ticker.history.call_args.kwargs["auto_adjust"] is True

    def test_actions_and_prepost_disabled(self) -> None:
        provider = make_provider()
        ticker = _stub_history(provider, yf_daily_df())

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )

        kwargs = ticker.history.call_args.kwargs
        assert kwargs["actions"] is False
        assert kwargs["prepost"] is False

    def test_unsupported_resolution_raises(self) -> None:
        provider = make_provider()
        bogus = MagicMock(spec=Resolution)
        bogus.value = "2m"
        with patch.dict(
            "fin3.providers.yfinance._RESOLUTION_TO_INTERVAL", {}, clear=True
        ):
            with pytest.raises(ProviderError, match="Unsupported resolution"):
                provider.fetch(
                    symbol="AAPL",
                    start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    resolution=Resolution.ONE_DAY,  # type: ignore[arg-type]
                )


class TestYahooProviderRetry:
    def test_retry_on_rate_limit_with_backoff(self) -> None:
        provider = make_provider(max_retries=3, initial_backoff=0.01)
        ticker = MagicMock()
        ticker.history.side_effect = [
            Exception("429 Too Many Requests"),
            yf_daily_df(),
        ]
        provider._client.Ticker.return_value = ticker  # type: ignore[union-attr]

        with patch("time.sleep") as mock_sleep:
            result = provider.fetch(
                symbol="AAPL",
                start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                end=datetime(2024, 1, 4, tzinfo=timezone.utc),
                resolution=Resolution.ONE_DAY,
            )

        assert len(result) == 3
        assert ticker.history.call_count == 2
        mock_sleep.assert_called_once()

    def test_raises_after_exhausting_retries(self) -> None:
        provider = make_provider(max_retries=2, initial_backoff=0.01)
        ticker = MagicMock()
        ticker.history.side_effect = Exception("rate limit hit")
        provider._client.Ticker.return_value = ticker  # type: ignore[union-attr]

        with patch("time.sleep"):
            with pytest.raises(ProviderRateLimitError):
                provider.fetch(
                    symbol="AAPL",
                    start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 4, tzinfo=timezone.utc),
                    resolution=Resolution.ONE_DAY,
                )

        assert ticker.history.call_count == 2

    def test_fatal_error_not_retried(self) -> None:
        provider = make_provider(max_retries=3)
        ticker = MagicMock()
        ticker.history.side_effect = ValueError("unexpected payload")
        provider._client.Ticker.return_value = ticker  # type: ignore[union-attr]

        with pytest.raises(ProviderError, match="unexpected payload"):
            provider.fetch(
                symbol="AAPL",
                start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                end=datetime(2024, 1, 4, tzinfo=timezone.utc),
                resolution=Resolution.ONE_DAY,
            )

        assert ticker.history.call_count == 1


class TestYahooProviderCost:
    def test_estimate_cost_is_zero(self) -> None:
        provider = make_provider()
        cost = provider.estimate_cost(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )
        assert cost == 0.0


class TestYahooProviderInstrumentBounds:
    def test_returns_listing_date_from_earliest_daily(self) -> None:
        provider = make_provider()
        # AAPL's earliest Yahoo daily bar (1980-12-12)
        df = pd.DataFrame(
            {"Open": [0.5], "High": [0.5], "Low": [0.5], "Close": [0.5], "Volume": [1]},
            index=pd.DatetimeIndex(["1980-12-12"]),
        )
        ticker = _stub_history(provider, df)

        bounds = provider.get_instrument_bounds("AAPL")

        call = ticker.history.call_args
        assert call.kwargs["period"] == "max"
        assert call.kwargs["interval"] == "1d"
        assert bounds["delist_date"] is None
        assert bounds["ipo_date"] == datetime(1980, 12, 12, tzinfo=timezone.utc)

    def test_unknown_symbol_returns_none(self) -> None:
        provider = make_provider()
        _stub_history(provider, pd.DataFrame())

        bounds = provider.get_instrument_bounds("NOPE")

        assert bounds == {"ipo_date": None, "delist_date": None}

    def test_api_error_returns_none(self) -> None:
        provider = make_provider()
        ticker = MagicMock()
        ticker.history.side_effect = Exception("rate limited")
        provider._client.Ticker.return_value = ticker  # type: ignore[union-attr]

        with patch("time.sleep"):
            bounds = provider.get_instrument_bounds("AAPL")

        assert bounds == {"ipo_date": None, "delist_date": None}


class TestYahooProviderInit:
    def test_missing_yfinance_raises_provider_error(self) -> None:
        """If the yfinance extra isn't installed, init gives a clear error."""
        with patch.dict("sys.modules", {"yfinance": None}):
            # Force the import inside __init__ to fail
            with patch("builtins.__import__", side_effect=ImportError("no module")):
                with pytest.raises(ProviderError, match="fin3\\[yfinance\\]"):
                    YahooProvider(YahooConfig())

    def test_config_defaults(self) -> None:
        cfg = YahooConfig()
        assert cfg.auto_adjust is False
        assert cfg.provider_type == "yahoo"
        assert cfg.max_retries == 3

    def test_config_custom(self) -> None:
        cfg = YahooConfig(auto_adjust=True, max_retries=5, timeout=10.0)
        assert cfg.auto_adjust is True
        assert cfg.max_retries == 5
        assert cfg.timeout == 10.0
