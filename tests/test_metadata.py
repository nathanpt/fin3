"""Tests for metadata store."""

from datetime import datetime


from fin3.metadata.asset_profile import MetadataStore
from fin3.storage.arctic import ArcticStorage


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
