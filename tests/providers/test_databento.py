"""Tests for DatabentoProvider with mocked SDK."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from fin3.exceptions import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from fin3.providers.databento import DatabentoProvider, _normalise
from fin3.schemas import OHLCV_COLUMNS, Resolution


@pytest.fixture
def mock_store_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [100.5, 101.5],
            "low": [99.5, 100.5],
            "close": [100.2, 101.2],
            "volume": [1000, 1100],
        },
        index=pd.DatetimeIndex(
            ["2024-01-02 09:30:00", "2024-01-02 09:31:00"], tz="UTC"
        ),
    )


class TestDatabentoProvider:
    def test_fetch_returns_normalised_df(self, mock_store_df: pd.DataFrame) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.to_df.return_value = mock_store_df
        mock_client.timeseries.get_range.return_value = mock_store
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        result = provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )
        assert len(result) == 2
        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert isinstance(result.index, pd.DatetimeIndex)
        assert str(result.index.tz) == "UTC"
        assert result.index.name is None
        assert result["open"].tolist() == [100.0, 101.0]
        assert result["high"].tolist() == [100.5, 101.5]
        assert result["low"].tolist() == [99.5, 100.5]
        assert result["close"].tolist() == [100.2, 101.2]
        assert result["volume"].tolist() == [1000.0, 1100.0]

    def test_fetch_empty_result(self) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.to_df.return_value = pd.DataFrame()
        mock_client.timeseries.get_range.return_value = mock_store
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        result = provider.fetch(
            symbol="NONEXISTENT",
            start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )
        assert result.empty
        assert list(result.columns) == list(OHLCV_COLUMNS)

    def test_fetch_raises_provider_error(self) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_client.timeseries.get_range.side_effect = Exception("network error")
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        with pytest.raises(ProviderError, match="network error"):
            provider.fetch(
                symbol="AAPL",
                start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
                end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
                resolution=Resolution.ONE_MINUTE,
            )


class TestDatabentoDatasetConfig:
    """Tests for configurable dataset selection and symbol convention."""

    def test_fetch_uses_configured_dataset(
        self, mock_store_df: pd.DataFrame
    ) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.to_df.return_value = mock_store_df
        mock_client.timeseries.get_range.return_value = mock_store
        provider._client = mock_client
        provider._dataset = "ARCX.PILLAR"

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        call_kwargs = mock_client.timeseries.get_range.call_args
        assert call_kwargs.kwargs["dataset"] == "ARCX.PILLAR"

    def test_symbol_cms_convention_for_arcx(
        self, mock_store_df: pd.DataFrame
    ) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.to_df.return_value = mock_store_df
        mock_client.timeseries.get_range.return_value = mock_store
        provider._client = mock_client
        provider._dataset = "ARCX.PILLAR"

        provider.fetch(
            symbol="BRK.B",
            start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        call_kwargs = mock_client.timeseries.get_range.call_args
        assert call_kwargs.kwargs["symbols"] == "BRK B"

    def test_estimate_cost_uses_configured_dataset(self) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_client.metadata.get_cost.return_value = 1.0
        provider._client = mock_client
        provider._dataset = "ARCX.PILLAR"

        provider.estimate_cost(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        call_kwargs = mock_client.metadata.get_cost.call_args
        assert call_kwargs.kwargs["dataset"] == "ARCX.PILLAR"


class TestDatabentoEstimateCost:
    """Tests for DatabentoProvider.estimate_cost()."""

    def test_estimate_cost_calls_get_cost(self) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_client.metadata.get_cost.return_value = 0.54
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        cost = provider.estimate_cost(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        assert cost == 0.54
        mock_client.metadata.get_cost.assert_called_once_with(
            dataset="XNAS.ITCH",
            symbols="AAPL",
            schema="ohlcv-1m",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
        )

    def test_estimate_cost_handles_api_error(self) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_client.metadata.get_cost.side_effect = Exception("auth failed")
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        with pytest.raises(ProviderError, match="cost estimate failed"):
            provider.estimate_cost(
                symbol="AAPL",
                start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                end=datetime(2024, 1, 3, tzinfo=timezone.utc),
                resolution=Resolution.ONE_MINUTE,
            )

    def test_estimate_cost_unsupported_resolution_returns_zero(self) -> None:
        """A resolution absent from _RESOLUTION_TO_SCHEMA short-circuits to
        0.0 without touching the cost API (fetch, by contrast, raises
        ProviderError for the same situation)."""
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        with patch.dict(
            "fin3.providers.databento._RESOLUTION_TO_SCHEMA", {}, clear=True
        ):
            cost = provider.estimate_cost(
                symbol="AAPL",
                start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                end=datetime(2024, 1, 3, tzinfo=timezone.utc),
                resolution=Resolution.ONE_MINUTE,
            )

        assert cost == 0.0
        mock_client.metadata.get_cost.assert_not_called()


class TestDatabentoRetryConfig:
    """Tests for configurable retry policy on DatabentoConfig."""

    def test_retry_on_rate_limit_with_backoff(
        self, mock_store_df: pd.DataFrame
    ) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        provider._client = MagicMock()
        provider._dataset = "XNAS.ITCH"
        provider._max_retries = 3
        provider._initial_backoff = 0.01
        provider._max_backoff = 1.0

        mock_store = MagicMock()
        mock_store.to_df.return_value = mock_store_df
        provider._client.timeseries.get_range.side_effect = [
            Exception("429 rate limit exceeded"),
            mock_store,
        ]

        with patch("time.sleep") as mock_sleep:
            result = provider.fetch(
                symbol="AAPL",
                start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
                end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
                resolution=Resolution.ONE_MINUTE,
            )

        assert len(result) == 2
        mock_sleep.assert_called_once_with(0.01)

    def test_raises_after_exhausting_retries(self) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        provider._client = MagicMock()
        provider._dataset = "XNAS.ITCH"
        provider._max_retries = 2
        provider._initial_backoff = 0.01
        provider._max_backoff = 1.0

        provider._client.timeseries.get_range.side_effect = Exception(
            "429 rate limit exceeded"
        )

        with patch("time.sleep"):
            with pytest.raises(ProviderRateLimitError):
                provider.fetch(
                    symbol="AAPL",
                    start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
                    resolution=Resolution.ONE_MINUTE,
                )


def make_provider(
    *,
    max_retries: int = 3,
    initial_backoff: float = 1.0,
    max_backoff: float = 30.0,
) -> tuple[DatabentoProvider, MagicMock]:
    """Build a provider via ``__new__`` with a mocked SDK client (no import).

    Mirrors the yfinance test pattern: bypass ``__init__`` then set the
    attributes the fetch path actually reads.
    """
    provider = DatabentoProvider.__new__(DatabentoProvider)
    provider._client = MagicMock()  # type: ignore[attr-defined]
    provider._dataset = "XNAS.ITCH"  # type: ignore[attr-defined]
    provider._max_retries = max_retries  # type: ignore[attr-defined]
    provider._initial_backoff = initial_backoff  # type: ignore[attr-defined]
    provider._max_backoff = max_backoff  # type: ignore[attr-defined]
    return provider, provider._client


def stub_empty_store(client: MagicMock) -> None:
    """Wire ``client.timeseries.get_range`` to return an empty DataFrame."""
    store = MagicMock()
    store.to_df.return_value = pd.DataFrame()
    client.timeseries.get_range.return_value = store


def sdk_store_df() -> pd.DataFrame:
    """A faithful Databento ``to_df()`` shape: ``ts_event``-named index.

    The real SDK returns OHLCV columns plus metadata columns (``instrument_id``,
    ``rtype``, ``publisher_id``) and a datetime index named ``ts_event``.
    """
    return pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [100.5, 101.5],
            "low": [99.5, 100.5],
            "close": [100.2, 101.2],
            "volume": [1000, 1100],
            "instrument_id": [1234, 1234],
            "rtype": [101, 101],
            "publisher_id": [1, 1],
        },
        index=pd.DatetimeIndex(
            ["2024-01-02 09:30:00", "2024-01-02 09:31:00"], name="ts_event"
        ),
    )


class TestDatabentoSchemaMapping:
    @pytest.mark.parametrize(
        ("resolution", "expected_schema"),
        [
            (Resolution.ONE_MINUTE, "ohlcv-1m"),
            (Resolution.FIVE_MINUTE, "ohlcv-1m"),
            (Resolution.FIFTEEN_MINUTE, "ohlcv-1m"),
            (Resolution.ONE_HOUR, "ohlcv-1h"),
            (Resolution.FOUR_HOUR, "ohlcv-1h"),
            (Resolution.ONE_DAY, "ohlcv-1d"),
        ],
    )
    def test_resolution_maps_to_schema(
        self, resolution: Resolution, expected_schema: str
    ) -> None:
        provider, client = make_provider()
        stub_empty_store(client)

        result = provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
            resolution=resolution,
        )

        assert (
            client.timeseries.get_range.call_args.kwargs["schema"] == expected_schema
        )
        assert result.empty

    def test_unsupported_resolution_raises(self) -> None:
        provider, _ = make_provider()
        bogus = MagicMock(spec=Resolution)
        bogus.value = "2s"
        with patch.dict(
            "fin3.providers.databento._RESOLUTION_TO_SCHEMA", {}, clear=True
        ):
            with pytest.raises(ProviderError, match="Unsupported resolution"):
                provider.fetch(
                    symbol="AAPL",
                    start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
                    resolution=Resolution.ONE_DAY,  # type: ignore[arg-type]
                )


class TestDatabentoDataStartAfterAvailableEnd:
    def test_returns_empty_ohlcv_when_range_beyond_available_end(self) -> None:
        # Databento raises (not returns empty) when start is after the
        # dataset's last available date; fetch must translate that to an
        # empty canonical frame instead of raising.
        provider, client = make_provider()
        client.timeseries.get_range.side_effect = Exception(
            "DATA_START_AFTER_AVAILABLE_END: data start is after the available end"
        )

        result = provider.fetch(
            symbol="AAPL",
            start=datetime(2030, 1, 1, tzinfo=timezone.utc),
            end=datetime(2030, 1, 2, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
        )

        assert result.empty
        assert list(result.columns) == list(OHLCV_COLUMNS)


class TestDatabentoTimeoutRetry:
    def test_timeout_then_success_sleeps_between_attempts(
        self, mock_store_df: pd.DataFrame
    ) -> None:
        provider, client = make_provider(
            max_retries=3, initial_backoff=0.5, max_backoff=1.0
        )
        store = MagicMock()
        store.to_df.return_value = mock_store_df
        client.timeseries.get_range.side_effect = [
            Exception("connection timeout"),
            Exception("connection timeout"),
            store,
        ]

        with patch("time.sleep") as mock_sleep:
            result = provider.fetch(
                symbol="AAPL",
                start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
                end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
                resolution=Resolution.ONE_MINUTE,
            )

        assert len(result) == 2
        assert client.timeseries.get_range.call_count == 3
        # Timeout retries back off exactly like rate-limit retries:
        # min(0.5 * 2**0, 1.0) == 0.5, min(0.5 * 2**1, 1.0) == 1.0.
        assert [call.args[0] for call in mock_sleep.call_args_list] == [0.5, 1.0]

    def test_timeouts_exhausted_raises_timeout_error(self) -> None:
        provider, client = make_provider(
            max_retries=2, initial_backoff=0.01, max_backoff=1.0
        )
        client.timeseries.get_range.side_effect = Exception("connection timeout")

        with patch("time.sleep"):
            with pytest.raises(ProviderTimeoutError, match="timeout"):
                provider.fetch(
                    symbol="AAPL",
                    start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
                    resolution=Resolution.ONE_MINUTE,
                )

        assert client.timeseries.get_range.call_count == 2


class TestDatabentoBackoffCap:
    def test_rate_limit_sleep_calls_capped_at_max_backoff(self) -> None:
        # Always-429 with max_retries=3 sleeps on attempts 0 and 1 only:
        # min(10 * 2**0, 12) == 10, min(10 * 2**1, 12) == 12.
        provider, client = make_provider(
            max_retries=3, initial_backoff=10.0, max_backoff=12.0
        )
        client.timeseries.get_range.side_effect = Exception("429 rate limit exceeded")

        with patch("time.sleep") as mock_sleep:
            with pytest.raises(ProviderRateLimitError):
                provider.fetch(
                    symbol="AAPL",
                    start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
                    resolution=Resolution.ONE_MINUTE,
                )

        assert [call.args[0] for call in mock_sleep.call_args_list] == [10.0, 12.0]


class TestDatabentoNormalise:
    def test_ts_event_index_drops_extras_keeps_values(self) -> None:
        result = _normalise(sdk_store_df())

        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert "instrument_id" not in result.columns
        assert "rtype" not in result.columns
        assert "publisher_id" not in result.columns
        assert result.index.name is None
        assert str(result.index.tz) == "UTC"
        assert result["close"].tolist() == [100.2, 101.2]
        assert result["volume"].tolist() == [1000, 1100]

    def test_ts_event_column_is_stripped_by_keep_filter(self) -> None:
        # reset_index() demotes ts_event to a plain column — but result[keep]
        # (OHLCV-only) runs BEFORE the index branches in _normalise, so the
        # `elif "ts_event" in result.columns` branch is unreachable; the
        # column is dropped and the incoming RangeIndex survives untouched.
        df = sdk_store_df().reset_index()
        result = _normalise(df)

        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert "ts_event" not in result.columns
        assert result.index.name is None
        assert list(result.index) == [0, 1]

    def test_ts_recv_column_is_stripped_no_fallback_index(self) -> None:
        # Same dead-branch reality: `elif "ts_recv" in result.columns` is
        # unreachable — ts_recv is stripped by the keep filter before the
        # branch point, and the frame keeps its positional integer index.
        df = pd.DataFrame(
            {
                "open": [42.0],
                "high": [43.0],
                "low": [41.0],
                "close": [42.5],
                "volume": [10],
                "ts_recv": [1_704_153_600_000_000_000],
            }
        )

        result = _normalise(df)

        assert list(result.columns) == list(OHLCV_COLUMNS)
        assert "ts_recv" not in result.columns
        assert result.index.name is None
        assert list(result.index) == [0]

    def test_index_named_ts_event_takes_precedence_over_columns(self) -> None:
        df = sdk_store_df()
        df["ts_recv"] = [999, 999]  # must be ignored: index is already ts_event

        result = _normalise(df)

        # Index-name branch wins; naive index is localised to UTC.
        assert list(result.index) == [
            pd.Timestamp("2024-01-02 09:30:00", tz="UTC"),
            pd.Timestamp("2024-01-02 09:31:00", tz="UTC"),
        ]
        assert "ts_recv" not in result.columns
        assert result.index.name is None
