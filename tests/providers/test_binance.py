"""Tests for BinanceProvider with mocked HTTP layer."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from fin3.config.settings import BinanceConfig
from fin3.exceptions import ProviderError, ProviderRateLimitError
from fin3.providers.binance import (
    BinanceProvider,
    _normalise,
    _to_binance_symbol,
    _to_ms,
)
from fin3.schemas import OHLCV_COLUMNS, Resolution


def kline(
    open_time_ms: int, o: float, h: float, low: float, c: float, v: float
) -> list[object]:
    """Build a minimal Binance kline row (12 elements)."""
    return [
        open_time_ms,
        str(o),
        str(h),
        str(low),
        str(c),
        str(v),
        open_time_ms + 59_999,  # close time
        "0.0",  # quote asset volume
        0,  # number of trades
        "0.0",  # taker buy base volume
        "0.0",  # taker buy quote volume
        "0.0",  # ignore
    ]


@pytest.fixture
def provider() -> BinanceProvider:
    return BinanceProvider(BinanceConfig())


@pytest.fixture
def sample_rows() -> list[list[object]]:
    # Two 1m bars on 2024-01-02 00:00 and 00:01 UTC
    return [
        kline(1704153600000, 100.0, 100.5, 99.5, 100.2, 1000.0),
        kline(1704153660000, 100.2, 101.5, 100.0, 101.0, 1100.0),
    ]


class TestSymbolMapping:
    def test_btc_usd_to_btcusdt(self) -> None:
        assert _to_binance_symbol("BTC-USD") == "BTCUSDT"

    def test_eth_usd_to_ethusdt(self) -> None:
        assert _to_binance_symbol("ETH-USD") == "ETHUSDT"

    def test_already_usdt_passthrough(self) -> None:
        assert _to_binance_symbol("BTC-USDT") == "BTCUSDT"

    def test_plain_symbol_promoted(self) -> None:
        assert _to_binance_symbol("BTCUSD") == "BTCUSDT"

    def test_lowercase_normalised(self) -> None:
        assert _to_binance_symbol("btc-usd") == "BTCUSDT"


class TestToMs:
    def test_aware_utc(self) -> None:
        dt = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
        assert _to_ms(dt) == 1704153600000

    def test_naive_treated_as_utc(self) -> None:
        naive = datetime(2024, 1, 2, 0, 0)
        aware = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
        assert _to_ms(naive) == _to_ms(aware)


class TestNormalise:
    def test_columns_and_index(self, sample_rows: list[list[object]]) -> None:
        df = _normalise(sample_rows)
        assert list(df.columns) == list(OHLCV_COLUMNS)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert str(df.index.tz) == "UTC"
        assert df.index.name is None
        assert df["open"].tolist() == [100.0, 100.2]
        assert df["volume"].tolist() == [1000.0, 1100.0]

    def test_deduplicates_and_sorts(self) -> None:
        rows = [
            kline(1704153660000, 101.0, 101.5, 100.0, 101.0, 1100.0),
            kline(1704153600000, 100.0, 100.5, 99.5, 100.2, 1000.0),
            kline(1704153600000, 999.0, 999.0, 999.0, 999.0, 1.0),  # dup open time
        ]
        df = _normalise(rows)
        assert len(df) == 2
        assert df.index.is_monotonic_increasing
        # First occurrence kept on duplicate
        assert df["open"].iloc[0] == 100.0


class TestBinanceProviderFetch:
    @patch("fin3.providers.binance.BinanceProvider._request_with_retry")
    def test_fetch_returns_normalised_df(
        self,
        mock_request: MagicMock,
        provider: BinanceProvider,
        sample_rows: list[list[object]],
    ) -> None:
        mock_request.return_value = sample_rows

        result = provider.fetch(
            symbol="BTC-USD",
            start=datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 0, 1, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        assert len(result) == 2
        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert str(result.index.tz) == "UTC"
        # Request used the mapped symbol + 1m interval
        params = mock_request.call_args.args[0]
        assert params["symbol"] == "BTCUSDT"
        assert params["interval"] == "1m"

    @patch("fin3.providers.binance.BinanceProvider._request_with_retry")
    def test_fetch_empty_returns_canonical_empty(
        self, mock_request: MagicMock, provider: BinanceProvider
    ) -> None:
        mock_request.return_value = []

        result = provider.fetch(
            symbol="NONEXISTENT-USD",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )

        assert result.empty
        assert list(result.columns) == list(OHLCV_COLUMNS)

    @patch("fin3.providers.binance.BinanceProvider._request_with_retry")
    def test_resolution_mapping(
        self, mock_request: MagicMock, provider: BinanceProvider
    ) -> None:
        mock_request.return_value = []

        provider.fetch(
            symbol="BTC-USD",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 1, tzinfo=timezone.utc),
            resolution=Resolution.ONE_HOUR,
        )

        params = mock_request.call_args.args[0]
        assert params["interval"] == "1h"

    @patch("fin3.providers.binance.BinanceProvider._request_with_retry")
    def test_pagination_advances_start_time(self, mock_request: MagicMock) -> None:
        # Force small pages to exercise pagination without a huge fixture.
        provider = BinanceProvider(BinanceConfig(request_limit=3))
        # 1m bars at 00:00..00:09 UTC (10 bars), paged 3 + 3 + 3 + 1.
        t0 = 1704153600000
        batch1 = [
            kline(t0 + i * 60_000, 100.0, 100.0, 100.0, 100.0, 1.0) for i in range(3)
        ]
        batch2 = [
            kline(t0 + (3 + i) * 60_000, 100.0, 100.0, 100.0, 100.0, 1.0)
            for i in range(3)
        ]
        batch3 = [
            kline(t0 + (6 + i) * 60_000, 100.0, 100.0, 100.0, 100.0, 1.0)
            for i in range(3)
        ]
        batch4 = [kline(t0 + 9 * 60_000, 100.0, 100.0, 100.0, 100.0, 1.0)]
        mock_request.side_effect = [batch1, batch2, batch3, batch4]

        result = provider.fetch(
            symbol="BTC-USD",
            start=datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 0, 9, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        assert len(result) == 10
        assert mock_request.call_count == 4
        # Cursor advanced past each batch's last open time
        start_times = [
            call.args[0]["startTime"] for call in mock_request.call_args_list
        ]
        assert start_times[0] == t0
        assert start_times[1] == t0 + 2 * 60_000 + 1  # last_open + 1ms
        assert start_times[2] == t0 + 5 * 60_000 + 1

    @patch("fin3.providers.binance.BinanceProvider._request_with_retry")
    def test_fetch_filters_beyond_end(
        self, mock_request: MagicMock, provider: BinanceProvider
    ) -> None:
        t0 = 1704153600000  # 00:00
        # Batch returns bars at 00:00, 00:01, 00:02; end is 00:01.
        rows = [
            kline(t0, 100.0, 100.0, 100.0, 100.0, 1.0),
            kline(t0 + 60_000, 100.0, 100.0, 100.0, 100.0, 1.0),
            kline(t0 + 120_000, 100.0, 100.0, 100.0, 100.0, 1.0),
        ]
        mock_request.return_value = rows

        result = provider.fetch(
            symbol="BTC-USD",
            start=datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 0, 1, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        # Only bars with open_time <= end (00:01) are kept
        assert len(result) == 2

    def test_unsupported_resolution_raises(self, provider: BinanceProvider) -> None:
        # Construct a value outside the supported map to exercise the guard.
        bogus = MagicMock(spec=Resolution)
        bogus.value = "2m"
        with patch.dict(
            "fin3.providers.binance._RESOLUTION_TO_INTERVAL", {}, clear=True
        ):
            with pytest.raises(ProviderError, match="Unsupported resolution"):
                provider.fetch(
                    symbol="BTC-USD",
                    start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    resolution=Resolution.ONE_DAY,  # type: ignore[arg-type]
                )


class TestBinanceProviderRetry:
    @patch("fin3.providers.binance.BinanceProvider._request")
    def test_retry_on_rate_limit_with_backoff(
        self,
        mock_request: MagicMock,
        provider: BinanceProvider,
        sample_rows: list[list[object]],
    ) -> None:
        mock_request.side_effect = [
            ProviderRateLimitError("429"),
            sample_rows,
        ]

        with patch("time.sleep") as mock_sleep:
            result = provider._request_with_retry({"symbol": "BTCUSDT"})

        assert result == sample_rows
        assert mock_request.call_count == 2
        mock_sleep.assert_called_once()

    @patch("fin3.providers.binance.BinanceProvider._request")
    def test_raises_after_exhausting_retries(self, mock_request: MagicMock) -> None:
        provider = BinanceProvider(BinanceConfig(max_retries=2, initial_backoff=0.01))
        mock_request.side_effect = ProviderRateLimitError("429")

        with patch("time.sleep"):
            with pytest.raises(ProviderRateLimitError):
                provider._request_with_retry({"symbol": "BTCUSDT"})

        assert mock_request.call_count == 2

    @patch("fin3.providers.binance.BinanceProvider._request")
    def test_fatal_error_not_retried(self, mock_request: MagicMock) -> None:
        provider = BinanceProvider(BinanceConfig(max_retries=3))
        mock_request.side_effect = ProviderError("HTTP 400 invalid symbol")

        with pytest.raises(ProviderError, match="invalid symbol"):
            provider._request_with_retry({"symbol": "BADSYMBOL"})

        assert mock_request.call_count == 1


class TestBinanceProviderCost:
    def test_estimate_cost_is_zero(self, provider: BinanceProvider) -> None:
        cost = provider.estimate_cost(
            symbol="BTC-USD",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )
        assert cost == 0.0


class TestBinanceProviderInstrumentBounds:
    @patch("fin3.providers.binance.BinanceProvider._request_with_retry")
    def test_returns_listing_date(
        self, mock_request: MagicMock, provider: BinanceProvider
    ) -> None:
        # First 1d kline for BTCUSDT opened 2017-08-17 00:00 UTC
        mock_request.return_value = [
            kline(1502928000000, 4000.0, 4000.0, 4000.0, 4000.0, 1.0)
        ]

        bounds = provider.get_instrument_bounds("BTC-USD")

        params = mock_request.call_args.args[0]
        assert params["startTime"] == 0
        assert params["limit"] == 1
        assert params["interval"] == "1d"
        assert bounds["delist_date"] is None
        assert bounds["ipo_date"] == datetime(2017, 8, 17, tzinfo=timezone.utc)

    @patch("fin3.providers.binance.BinanceProvider._request_with_retry")
    def test_unknown_symbol_returns_none(
        self, mock_request: MagicMock, provider: BinanceProvider
    ) -> None:
        mock_request.return_value = []

        bounds = provider.get_instrument_bounds("NOPE-USD")

        assert bounds == {"ipo_date": None, "delist_date": None}

    @patch("fin3.providers.binance.BinanceProvider._request_with_retry")
    def test_api_error_returns_none(
        self, mock_request: MagicMock, provider: BinanceProvider
    ) -> None:
        mock_request.side_effect = ProviderError("HTTP 400")

        bounds = provider.get_instrument_bounds("BAD-USD")

        assert bounds == {"ipo_date": None, "delist_date": None}


class TestBinanceProviderConfig:
    def test_no_api_key_required(self) -> None:
        # klines is a public endpoint — config must construct keyless.
        config = BinanceConfig()
        assert config.api_key == ""
        assert config.base_url == "https://api.binance.com"

    def test_request_limit_capped_at_1000(self) -> None:
        provider = BinanceProvider(BinanceConfig(request_limit=5000))
        assert provider._limit == 1000

    def test_custom_retry_values(self) -> None:
        config = BinanceConfig(max_retries=5, initial_backoff=2.0, max_backoff=120.0)
        assert config.max_retries == 5
        assert config.initial_backoff == 2.0
        assert config.max_backoff == 120.0
