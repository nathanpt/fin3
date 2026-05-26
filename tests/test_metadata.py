"""Tests for metadata store."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from fin3.metadata.asset_profile import MetadataStore
from fin3.storage.arctic import ArcticStorage
from tests.conftest import make_ohlcv


class TestMetadataStore:
    def test_cache_miss_returns_none(self, storage: ArcticStorage) -> None:
        store = MetadataStore(storage)
        result = store.get_lifecycle_bounds("AAPL")
        assert result is None

    def test_set_and_get_lifecycle_bounds(self, storage: ArcticStorage) -> None:
        store = MetadataStore(storage)
        ipo = datetime(2020, 1, 1)
        store.set_lifecycle_bounds("AAPL", ipo_date=ipo)
        result = store.get_lifecycle_bounds("AAPL")
        assert result is not None
        assert result["ipo_date"] == ipo

    def test_overwrite_lifecycle_bounds(self, storage: ArcticStorage) -> None:
        store = MetadataStore(storage)
        store.set_lifecycle_bounds("AAPL", ipo_date=datetime(2020, 1, 1))
        store.set_lifecycle_bounds("AAPL", ipo_date=datetime(2019, 6, 15))
        result = store.get_lifecycle_bounds("AAPL")
        assert result is not None
        assert result["ipo_date"] == datetime(2019, 6, 15)


class TestBootstrapMetadata:
    def test_from_provider_ref(self, storage: ArcticStorage) -> None:
        """Provider ref API returns ipo_date, cached after first call."""
        store = MetadataStore(storage)
        provider = MagicMock()
        provider.get_instrument_bounds.return_value = {
            "ipo_date": datetime(2020, 1, 1),
            "delist_date": None,
        }

        ipo, delist = store.bootstrap_metadata(
            "AAPL", provider,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert ipo == datetime(2020, 1, 1)
        assert delist is None
        # Second call should be a cache hit (provider not called again)
        ipo2, _ = store.bootstrap_metadata(
            "AAPL", provider,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert ipo2 == datetime(2020, 1, 1)
        assert provider.get_instrument_bounds.call_count == 1

    def test_discovery_fallback(self, storage: ArcticStorage) -> None:
        """Provider ref fails, discovery fetch succeeds."""
        store = MetadataStore(storage)
        provider = MagicMock()
        provider.get_instrument_bounds.side_effect = Exception("ref failed")
        provider.fetch.return_value = make_ohlcv("2024-01-02", periods=3, freq="1h")

        ipo, _ = store.bootstrap_metadata(
            "NEWTICKER", provider,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert ipo is not None
        provider.fetch.assert_called_once()

    def test_all_fail_stores_none(self, storage: ArcticStorage) -> None:
        """Both ref and discovery fail — stores None ipo_date."""
        store = MetadataStore(storage)
        provider = MagicMock()
        provider.get_instrument_bounds.side_effect = Exception("ref failed")
        provider.fetch.side_effect = Exception("fetch failed")

        ipo, _ = store.bootstrap_metadata(
            "BADDATA", provider,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert ipo is None
        # Verify it was cached (no repeated attempts)
        cached = store.get_lifecycle_bounds("BADDATA")
        assert cached is not None
        assert cached["ipo_date"] is None
