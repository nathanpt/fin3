"""End-to-end tests for MarketDataFetcher."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from fin3.config.settings import ClientConfig, DatabentoConfig, MinioConfig
from fin3.core import MarketDataFetcher
from fin3.metadata.asset_profile import MetadataStore
from fin3.providers import ProviderRegistry
from fin3.schemas import AssetType, Resolution
from fin3.storage.arctic import ArcticStorage
from tests.conftest import make_ohlcv


def _make_fetcher(storage: ArcticStorage) -> MarketDataFetcher:
    """Build a MarketDataFetcher without connecting to real MinIO or providers."""
    fetcher = MarketDataFetcher.__new__(MarketDataFetcher)
    fetcher._config = ClientConfig(
        minio=MinioConfig(endpoint="unused", access_key="t", secret_key="t"),
        providers={"databento": DatabentoConfig(api_key="test_key")},
    )
    fetcher._storage = storage
    fetcher._providers = ProviderRegistry.__new__(ProviderRegistry)
    fetcher._providers._providers = {}
    fetcher._metadata = MetadataStore(storage)
    return fetcher


@pytest.fixture
def lmdb_storage(tmp_path: pytest.TempPathFactory) -> ArcticStorage:
    return ArcticStorage.from_lmdb(str(tmp_path / "e2e_lmdb"))


class TestMarketDataFetcherE2E:
    def test_get_data_fetches_and_stores(self, lmdb_storage: ArcticStorage) -> None:
        fetcher = _make_fetcher(lmdb_storage)

        mock_provider = MagicMock()
        mock_provider.fetch.return_value = make_ohlcv(
            "2024-01-01 00:00", periods=24, freq="1h"
        )
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": None, "delist_date": None}
        )
        fetcher._providers._providers = {"databento": mock_provider}

        result = fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        assert isinstance(result, pd.DataFrame)
        assert len(result) > 0
        assert isinstance(result.columns, pd.MultiIndex)

    def test_get_data_second_call_skips_fetch(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        fetcher = _make_fetcher(lmdb_storage)

        mock_provider = MagicMock()
        mock_provider.fetch.return_value = make_ohlcv(
            "2024-01-01 00:00", periods=24, freq="1h"
        )
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": None, "delist_date": None}
        )
        fetcher._providers._providers = {"databento": mock_provider}

        fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        call_count_after_first = mock_provider.fetch.call_count

        fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        assert mock_provider.fetch.call_count == call_count_after_first

    def test_get_data_empty_symbols_rejected(self, lmdb_storage: ArcticStorage) -> None:
        fetcher = _make_fetcher(lmdb_storage)

        with pytest.raises(ValueError, match="non-empty"):
            fetcher.get_data(
                asset_type=AssetType.CRYPTO,
                provider="databento",
                resolution=Resolution.ONE_HOUR,
                symbols=[],
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 1, 2, tzinfo=timezone.utc),
            )

    def test_get_data_start_after_end_rejected(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        fetcher = _make_fetcher(lmdb_storage)

        with pytest.raises(ValueError, match="must be before"):
            fetcher.get_data(
                asset_type=AssetType.CRYPTO,
                provider="databento",
                resolution=Resolution.ONE_HOUR,
                symbols=["BTC-USD"],
                start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                end=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
