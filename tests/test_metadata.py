"""Tests for metadata store."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd

from fin3.metadata.asset_profile import METADATA_SYMBOL_PREFIX, MetadataStore
from fin3.schemas import METADATA_LIBRARY, Resolution
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
            resolution=Resolution.ONE_DAY,
        )
        assert ipo == datetime(2020, 1, 1)
        assert delist is None
        # Second call should be a cache hit (provider not called again)
        ipo2, _ = store.bootstrap_metadata(
            "AAPL", provider,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
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
            resolution=Resolution.ONE_HOUR,
        )
        # Core semantic: the derived ipo is the FIRST bar of the discovery
        # fetch (index[0] -> to_pydatetime()), not merely "some timestamp".
        assert ipo is not None
        assert ipo == provider.fetch.return_value.index[0]
        provider.fetch.assert_called_once_with(
            symbol="NEWTICKER",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
            resolution=Resolution.ONE_HOUR,
        )

    def test_discovery_fallback_without_resolution(self, storage: ArcticStorage) -> None:
        """Discovery fetch without resolution still works (backward compat)."""
        store = MetadataStore(storage)
        provider = MagicMock()
        provider.get_instrument_bounds.side_effect = Exception("ref failed")
        provider.fetch.return_value = make_ohlcv("2024-01-02", periods=3, freq="1h")

        ipo, _ = store.bootstrap_metadata(
            "NEWTICKER2", provider,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        # Same core semantic as test_discovery_fallback: first discovered bar.
        assert ipo is not None
        assert ipo == provider.fetch.return_value.index[0]
        provider.fetch.assert_called_once_with(
            symbol="NEWTICKER2",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )

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
            resolution=Resolution.ONE_DAY,
        )
        assert ipo is None
        # Verify it was cached (no repeated attempts)
        cached = store.get_lifecycle_bounds("BADDATA")
        assert cached is not None
        assert cached["ipo_date"] is None


class TestCorruptCachePinnedBehaviour:
    def test_corrupt_cache_row_poisons_bootstrap(
        self, storage: ArcticStorage
    ) -> None:
        """PINNED-AS-KNOWN-ISSUE — negative-cache poisoning (feat-021/gates).

        ``get_lifecycle_bounds`` treats any non-empty ``__meta__<SYMBOL>``
        row as a cache hit. A row written with wrong/missing columns makes
        ``row.get("ipo_date")``/``row.get("delist_date")`` return None, so
        ``bootstrap_metadata`` takes the cache-hit path and returns
        ``(None, None)`` **without ever calling the provider** — no ref API
        attempt, no discovery fetch. Worse, the all-fail bootstrap path
        itself stores ``None`` bounds, so one bad run poisons every later
        bootstrap for that symbol.

        This test deliberately pins current behaviour; update it together
        with the source fix, not before.
        """
        store = MetadataStore(storage)
        storage.write(
            METADATA_LIBRARY,
            f"{METADATA_SYMBOL_PREFIX}POISON",
            pd.DataFrame([{"wrong_column": "junk"}]),
        )
        provider = MagicMock()

        ipo, delist = store.bootstrap_metadata(
            "POISON", provider,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )
        assert ipo is None
        assert delist is None
        # Poisoned: the provider is never consulted (ref API or discovery).
        provider.get_instrument_bounds.assert_not_called()
        provider.fetch.assert_not_called()

        # ...and the poisoning persists across repeated bootstraps.
        ipo2, delist2 = store.bootstrap_metadata(
            "POISON", provider,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
            resolution=Resolution.ONE_DAY,
        )
        assert (ipo2, delist2) == (None, None)
        provider.get_instrument_bounds.assert_not_called()
        provider.fetch.assert_not_called()
