"""Tests for MassiveProvider with mocked HTTP layer."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from fin3.config.settings import MassiveConfig
from fin3.exceptions import ProviderError, ProviderRateLimitError
from fin3.providers.massive import (
    MassiveProvider,
    _normalise,
    _to_ms,
)
from fin3.schemas import OHLCV_COLUMNS, Resolution


def agg(
    t_ms: int, o: float, h: float, low: float, c: float, v: float
) -> dict[str, object]:
    """Build a Massive aggregate result row."""
    return {"o": o, "h": h, "l": low, "c": c, "v": v, "t": t_ms, "n": 1, "vw": o}


@pytest.fixture
def provider() -> MassiveProvider:
    return MassiveProvider(MassiveConfig(api_key="test-key"))


@pytest.fixture
def sample_rows() -> list[dict[str, object]]:
    # Two 1m bars on 2024-01-02 00:00 and 00:01 UTC
    return [
        agg(1704153600000, 100.0, 100.5, 99.5, 100.2, 1000.0),
        agg(1704153660000, 100.2, 101.5, 100.0, 101.0, 1100.0),
    ]


class TestToMs:
    def test_aware_utc(self) -> None:
        dt = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
        assert _to_ms(dt) == 1704153600000

    def test_naive_treated_as_utc(self) -> None:
        naive = datetime(2024, 1, 2, 0, 0)
        aware = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
        assert _to_ms(naive) == _to_ms(aware)


class TestAddApiKey:
    def test_appends_key_when_absent(self) -> None:
        url = "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/x/y"
        out = MassiveProvider._add_api_key(url, "NEW")
        assert "apiKey=NEW" in out

    def test_replaces_stale_key(self) -> None:
        url = "https://api.massive.com/path?apiKey=OLD"
        out = MassiveProvider._add_api_key(url, "NEW")
        assert "apiKey=NEW" in out
        assert "apiKey=OLD" not in out

    def test_preserves_cursor_param(self) -> None:
        url = "https://api.massive.com/path?cursor=abc123&apiKey=OLD"
        out = MassiveProvider._add_api_key(url, "NEW")
        assert "cursor=abc123" in out
        assert "apiKey=NEW" in out


class TestNormalise:
    def test_columns_and_index(self, sample_rows: list[dict[str, object]]) -> None:
        df = _normalise(sample_rows)
        assert list(df.columns) == list(OHLCV_COLUMNS)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert str(df.index.tz) == "UTC"
        assert df.index.name is None
        assert df["open"].tolist() == [100.0, 100.2]
        assert df["volume"].tolist() == [1000.0, 1100.0]

    def test_timestamps_from_milliseconds(self) -> None:
        # 1704153600000 ms == 2024-01-02 00:00 UTC. Guards against the
        # seconds-vs-milliseconds bug (seconds would yield a 1970 date).
        rows = [agg(1704153600000, 1.0, 1.0, 1.0, 1.0, 1.0)]
        df = _normalise(rows)
        assert df.index[0] == pd.Timestamp("2024-01-02 00:00:00", tz="UTC")

    def test_deduplicates_and_sorts(self) -> None:
        rows = [
            agg(1704153660000, 101.0, 101.5, 100.0, 101.0, 1100.0),
            agg(1704153600000, 100.0, 100.5, 99.5, 100.2, 1000.0),
            agg(1704153600000, 999.0, 999.0, 999.0, 999.0, 1.0),  # dup open time
        ]
        df = _normalise(rows)
        assert len(df) == 2
        assert df.index.is_monotonic_increasing
        # First occurrence kept on duplicate
        assert df["open"].iloc[0] == 100.0

    def test_drops_extra_columns(self) -> None:
        rows = [agg(1704153600000, 1.0, 2.0, 0.5, 1.5, 10.0)]
        df = _normalise(rows)
        assert set(df.columns) == set(OHLCV_COLUMNS)


class TestMassiveProviderFetch:
    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_fetch_returns_normalised_df(
        self,
        mock_request: MagicMock,
        provider: MassiveProvider,
        sample_rows: list[dict[str, object]],
    ) -> None:
        mock_request.return_value = {"results": sample_rows, "next_url": None}

        result = provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 0, 1, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        assert len(result) == 2
        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert str(result.index.tz) == "UTC"
        # Request used the AAPL ticker + 1x minute range in the path
        url = mock_request.call_args.args[0]
        assert "/ticker/AAPL/range/1/minute/" in url

    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_fetch_empty_returns_canonical_empty(
        self, mock_request: MagicMock, provider: MassiveProvider
    ) -> None:
        mock_request.return_value = {"results": [], "next_url": None}

        result = provider.fetch(
            symbol="NONEXISTENT",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )

        assert result.empty
        assert list(result.columns) == list(OHLCV_COLUMNS)

    @pytest.mark.parametrize(
        ("resolution", "range_segment"),
        [
            (Resolution.ONE_MINUTE, "range/1/minute/"),
            (Resolution.FIVE_MINUTE, "range/5/minute/"),
            (Resolution.FIFTEEN_MINUTE, "range/15/minute/"),
            (Resolution.ONE_HOUR, "range/1/hour/"),
            (Resolution.FOUR_HOUR, "range/4/hour/"),
            (Resolution.ONE_DAY, "range/1/day/"),
        ],
    )
    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_resolution_mapping(
        self,
        mock_request: MagicMock,
        provider: MassiveProvider,
        resolution: Resolution,
        range_segment: str,
    ) -> None:
        mock_request.return_value = {"results": [], "next_url": None}

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=resolution,
        )

        url = mock_request.call_args.args[0]
        assert range_segment in url
        # 4h maps natively (no aggregation fallback)
        if resolution is Resolution.FOUR_HOUR:
            assert "range/4/hour/" in url

    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_pagination_follows_next_url(
        self,
        mock_request: MagicMock,
        provider: MassiveProvider,
    ) -> None:
        t0 = 1704153600000  # 00:00
        page1 = {
            "results": [agg(t0 + i * 60_000, 1.0, 1.0, 1.0, 1.0, 1.0) for i in range(2)],
            "next_url": "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/minute/x/y?cursor=page2",
        }
        page2 = {
            "results": [agg(t0 + (2 + i) * 60_000, 2.0, 2.0, 2.0, 2.0, 2.0) for i in range(2)],
            "next_url": None,
        }
        mock_request.side_effect = [page1, page2]

        result = provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 0, 9, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        assert len(result) == 4
        assert mock_request.call_count == 2
        # Second request followed the cursor and carries the apiKey
        second_url = mock_request.call_args_list[1].args[0]
        assert "cursor=page2" in second_url
        assert "apiKey=test-key" in second_url

    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_pagination_stops_on_non_advancing_cursor(
        self,
        mock_request: MagicMock,
        provider: MassiveProvider,
    ) -> None:
        # Same next_url returned twice — must not loop forever.
        same_url = "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/minute/x/y?cursor=stuck"
        page1 = {"results": [agg(1704153600000, 1.0, 1.0, 1.0, 1.0, 1.0)], "next_url": same_url}
        page2 = {"results": [agg(1704153660000, 2.0, 2.0, 2.0, 2.0, 2.0)], "next_url": same_url}
        mock_request.side_effect = [page1, page2]

        result = provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 0, 9, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        # Followed once, then broke on the repeated cursor — no infinite loop.
        assert mock_request.call_count == 2
        assert len(result) == 2

    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_fetch_filters_beyond_end(
        self,
        mock_request: MagicMock,
        provider: MassiveProvider,
    ) -> None:
        t0 = 1704153600000  # 00:00
        rows = [
            agg(t0, 100.0, 100.0, 100.0, 100.0, 1.0),
            agg(t0 + 60_000, 100.0, 100.0, 100.0, 100.0, 1.0),
            agg(t0 + 120_000, 100.0, 100.0, 100.0, 100.0, 1.0),
        ]
        mock_request.return_value = {"results": rows, "next_url": None}

        result = provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 0, 1, tzinfo=timezone.utc),  # end == 00:01
            resolution=Resolution.ONE_MINUTE,
        )

        # Only bars with open time <= end (00:01) are kept
        assert len(result) == 2

    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_adjusted_false_default_in_url(
        self,
        mock_request: MagicMock,
        provider: MassiveProvider,
    ) -> None:
        mock_request.return_value = {"results": [], "next_url": None}

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )

        url = mock_request.call_args.args[0]
        assert "adjusted=false" in url

    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_adjusted_true_in_url(self, mock_request: MagicMock) -> None:
        provider = MassiveProvider(MassiveConfig(api_key="test-key", adjusted=True))
        mock_request.return_value = {"results": [], "next_url": None}

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )

        url = mock_request.call_args.args[0]
        assert "adjusted=true" in url

    def test_unsupported_resolution_raises(self, provider: MassiveProvider) -> None:
        bogus = MagicMock(spec=Resolution)
        bogus.value = "2m"
        with patch.dict("fin3.providers.massive._RESOLUTION_TO_RANGE", {}, clear=True):
            with pytest.raises(ProviderError, match="Unsupported resolution"):
                provider.fetch(
                    symbol="AAPL",
                    start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    resolution=Resolution.ONE_DAY,  # type: ignore[arg-type]
                )


class TestMassiveProviderRetry:
    @patch("fin3.providers.massive.MassiveProvider._request")
    def test_retry_on_rate_limit_with_backoff(
        self,
        mock_request: MagicMock,
        provider: MassiveProvider,
    ) -> None:
        mock_request.side_effect = [ProviderRateLimitError("429"), {"results": []}]

        with patch("time.sleep") as mock_sleep:
            result = provider._request_with_retry("https://api.massive.com/x")

        assert result == {"results": []}
        assert mock_request.call_count == 2
        mock_sleep.assert_called_once()

    @patch("fin3.providers.massive.MassiveProvider._request")
    def test_raises_after_exhausting_retries(self, mock_request: MagicMock) -> None:
        provider = MassiveProvider(MassiveConfig(api_key="k", max_retries=2, initial_backoff=0.01))
        mock_request.side_effect = ProviderRateLimitError("429")

        with patch("time.sleep"):
            with pytest.raises(ProviderRateLimitError):
                provider._request_with_retry("https://api.massive.com/x")

        assert mock_request.call_count == 2

    @patch("fin3.providers.massive.MassiveProvider._request")
    def test_fatal_error_not_retried(self, mock_request: MagicMock, provider: MassiveProvider) -> None:
        mock_request.side_effect = ProviderError("HTTP 400 invalid symbol")

        with pytest.raises(ProviderError, match="invalid symbol"):
            provider._request_with_retry("https://api.massive.com/x")

        assert mock_request.call_count == 1


class TestMassiveProviderCost:
    def test_estimate_cost_is_zero(self, provider: MassiveProvider) -> None:
        cost = provider.estimate_cost(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )
        assert cost == 0.0


class TestMassiveProviderInstrumentBounds:
    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_returns_listing_date(
        self, mock_request: MagicMock, provider: MassiveProvider
    ) -> None:
        expected = datetime(1980, 12, 12, tzinfo=timezone.utc)  # AAPL IPO
        t_ms = int(expected.timestamp() * 1000)
        mock_request.return_value = {"results": [agg(t_ms, 22.0, 22.0, 22.0, 22.0, 1.0)]}

        bounds = provider.get_instrument_bounds("AAPL")

        url = mock_request.call_args.args[0]
        assert "/range/1/day/1970-01-01/" in url
        assert "sort=asc" in url
        assert "limit=1" in url
        assert bounds["delist_date"] is None
        assert bounds["ipo_date"] == expected

    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_unknown_symbol_returns_none(
        self, mock_request: MagicMock, provider: MassiveProvider
    ) -> None:
        mock_request.return_value = {"results": []}

        bounds = provider.get_instrument_bounds("NOPE")

        assert bounds == {"ipo_date": None, "delist_date": None}

    @patch("fin3.providers.massive.MassiveProvider._request_with_retry")
    def test_api_error_returns_none(
        self, mock_request: MagicMock, provider: MassiveProvider
    ) -> None:
        mock_request.side_effect = ProviderError("HTTP 403")

        bounds = provider.get_instrument_bounds("BAD")

        assert bounds == {"ipo_date": None, "delist_date": None}


class TestMassiveProviderConfig:
    def test_defaults(self) -> None:
        config = MassiveConfig(api_key="k")
        assert config.base_url == "https://api.massive.com"
        assert config.adjusted is False
        assert config.request_limit == 50000
        provider = MassiveProvider(config)
        assert provider._limit == 50000
        assert provider._adjusted is False

    def test_request_limit_capped_at_50000(self) -> None:
        provider = MassiveProvider(MassiveConfig(api_key="k", request_limit=100000))
        assert provider._limit == 50000

    def test_custom_retry_values(self) -> None:
        config = MassiveConfig(
            api_key="k", max_retries=5, initial_backoff=2.0, max_backoff=120.0
        )
        assert config.max_retries == 5
        assert config.initial_backoff == 2.0
        assert config.max_backoff == 120.0
