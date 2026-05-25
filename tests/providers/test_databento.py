"""Tests for DatabentoProvider with mocked SDK."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from fin3.config.settings import DatabentoConfig
from fin3.exceptions import ProviderError
from fin3.providers.databento import DatabentoProvider
from fin3.schemas import Resolution


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
