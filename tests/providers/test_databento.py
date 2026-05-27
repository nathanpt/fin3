"""Tests for DatabentoProvider with mocked SDK."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from fin3.config.settings import DatabentoConfig
from fin3.exceptions import ProviderError
from fin3.providers.databento import DatabentoProvider
from fin3.schemas import AssetType, Resolution


@pytest.fixture
def databento_config() -> DatabentoConfig:
    return DatabentoConfig(api_key="test_key", dataset="XNAS.ITCH")


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
    @patch("fin3.providers.databento.DatabentoProvider.__init__", return_value=None)
    def test_fetch_returns_normalised_df(
        self,
        mock_init: MagicMock,
        mock_store_df: pd.DataFrame,
        databento_config: DatabentoConfig,
    ) -> None:
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
        assert "open" in result.columns
        assert "volume" in result.columns

    @patch("fin3.providers.databento.DatabentoProvider.__init__", return_value=None)
    def test_fetch_empty_result(
        self, mock_init: MagicMock, databento_config: DatabentoConfig
    ) -> None:
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

    @patch("fin3.providers.databento.DatabentoProvider.__init__", return_value=None)
    def test_fetch_raises_provider_error(
        self, mock_init: MagicMock, databento_config: DatabentoConfig
    ) -> None:
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


class TestDatabentoDatasetSelection:
    """Tests for automatic dataset switching to ARCX.PILLAR."""

    @patch("fin3.providers.databento.DatabentoProvider.__init__", return_value=None)
    def test_fetch_uses_arcx_pillar_for_1m_equities(
        self, mock_init: MagicMock, mock_store_df: pd.DataFrame
    ) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.to_df.return_value = mock_store_df
        mock_client.timeseries.get_range.return_value = mock_store
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
            asset_type=AssetType.EQUITY_US,
        )

        call_kwargs = mock_client.timeseries.get_range.call_args
        assert call_kwargs.kwargs["dataset"] == "ARCX.PILLAR"

    @patch("fin3.providers.databento.DatabentoProvider.__init__", return_value=None)
    def test_fetch_uses_configured_dataset_for_non_1m(
        self, mock_init: MagicMock, mock_store_df: pd.DataFrame
    ) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.to_df.return_value = mock_store_df
        mock_client.timeseries.get_range.return_value = mock_store
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
            asset_type=AssetType.EQUITY_US,
        )

        call_kwargs = mock_client.timeseries.get_range.call_args
        assert call_kwargs.kwargs["dataset"] == "XNAS.ITCH"

    @patch("fin3.providers.databento.DatabentoProvider.__init__", return_value=None)
    def test_fetch_uses_configured_dataset_for_non_equity(
        self, mock_init: MagicMock, mock_store_df: pd.DataFrame
    ) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.to_df.return_value = mock_store_df
        mock_client.timeseries.get_range.return_value = mock_store
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        provider.fetch(
            symbol="AAPL",
            start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
            asset_type=AssetType.CRYPTO,
        )

        call_kwargs = mock_client.timeseries.get_range.call_args
        assert call_kwargs.kwargs["dataset"] == "XNAS.ITCH"

    @patch("fin3.providers.databento.DatabentoProvider.__init__", return_value=None)
    def test_symbol_cms_convention_for_arcx(
        self, mock_init: MagicMock, mock_store_df: pd.DataFrame
    ) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.to_df.return_value = mock_store_df
        mock_client.timeseries.get_range.return_value = mock_store
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        provider.fetch(
            symbol="BRK.B",
            start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
            asset_type=AssetType.EQUITY_US,
        )

        call_kwargs = mock_client.timeseries.get_range.call_args
        assert call_kwargs.kwargs["symbols"] == "BRK B"


class TestDatabentoEstimateCost:
    """Tests for DatabentoProvider.estimate_cost()."""

    @patch("fin3.providers.databento.DatabentoProvider.__init__", return_value=None)
    def test_estimate_cost_calls_get_cost(self, mock_init: MagicMock) -> None:
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

    @patch("fin3.providers.databento.DatabentoProvider.__init__", return_value=None)
    def test_estimate_cost_uses_arcx_for_1m_equities(
        self, mock_init: MagicMock
    ) -> None:
        provider = DatabentoProvider.__new__(DatabentoProvider)
        mock_client = MagicMock()
        mock_client.metadata.get_cost.return_value = 1.0
        provider._client = mock_client
        provider._dataset = "XNAS.ITCH"

        provider.estimate_cost(
            symbol="AAPL",
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 3, tzinfo=timezone.utc),
            resolution=Resolution.ONE_MINUTE,
            asset_type=AssetType.EQUITY_US,
        )

        call_kwargs = mock_client.metadata.get_cost.call_args
        assert call_kwargs.kwargs["dataset"] == "ARCX.PILLAR"

    @patch("fin3.providers.databento.DatabentoProvider.__init__", return_value=None)
    def test_estimate_cost_handles_api_error(self, mock_init: MagicMock) -> None:
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
