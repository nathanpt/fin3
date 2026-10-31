"""ArcticDB / MinIO storage round-trip integration tests.

Tests write -> read -> update -> read cycles against a real MinIO instance.
Each test uses a unique library name that is cleaned up afterward.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from fin3.storage.arctic import ArcticStorage
from tests.conftest import make_ohlcv

pytestmark = pytest.mark.integration


class TestStorageRoundTrip:
    """Write, read, update, and verify data on real MinIO/ArcticDB."""

    def test_write_and_read(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10)
        minio_storage.write(unique_library, "AAPL", df)

        result = minio_storage.read(unique_library, "AAPL")
        assert result is not None
        assert len(result) == 10
        pd.testing.assert_frame_equal(result, df, check_freq=False)

    def test_read_nonexistent_symbol_returns_none(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        result = minio_storage.read(unique_library, "NOEXIST")
        assert result is None

    def test_has_symbol(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5)
        assert not minio_storage.has_symbol(unique_library, "AAPL")

        minio_storage.write(unique_library, "AAPL", df)
        assert minio_storage.has_symbol(unique_library, "AAPL")

    def test_update_overwrites_range(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        # Write 10 bars
        original = make_ohlcv("2024-01-02 09:30", periods=10)
        minio_storage.write(unique_library, "AAPL", original)

        # Update bars 5-8 (overwrite with different values)
        updated_slice = make_ohlcv("2024-01-02 09:35", periods=4)
        updated_slice["close"] = [999.0] * 4

        minio_storage.update(
            unique_library,
            "AAPL",
            updated_slice,
            date_range=(
                datetime(2024, 1, 2, 9, 35, tzinfo=timezone.utc),
                datetime(2024, 1, 2, 9, 38, tzinfo=timezone.utc),
            ),
        )

        result = minio_storage.read(unique_library, "AAPL")
        assert result is not None
        assert len(result) == 10  # still 10 rows
        # Verify the updated range has new values
        assert result.loc[
            pd.Timestamp("2024-01-02 09:35", tz="UTC"), "close"
        ] == pytest.approx(999.0)
        # Verify the untouched rows are unchanged
        assert result.iloc[0]["close"] == pytest.approx(100.2)

    def test_read_columns_returns_only_close_with_intact_index(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10)
        minio_storage.write(unique_library, "AAPL", df)

        result = minio_storage.read(unique_library, "AAPL", columns=["close"])
        assert result is not None
        # Exactly the close column; the UTC index is always returned regardless.
        assert list(result.columns) == ["close"]
        assert result.index.tz is not None
        assert result.index.equals(df.index)
        pd.testing.assert_series_equal(
            result["close"], df["close"], check_exact=True, check_freq=False
        )

    def test_symbols_with_different_schemas_coexist(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        """dynamic_schema=True lets symbols with different column sets share a library."""
        base = make_ohlcv("2024-01-02 09:30", periods=5)
        minio_storage.write(unique_library, "AAPL", base)

        extended = make_ohlcv("2024-01-02 09:30", periods=4)
        extended["vwap"] = extended["close"] * 1.01
        minio_storage.write(unique_library, "MSFT", extended)

        # The first symbol still reads back exactly its 5 written columns.
        aapl = minio_storage.read(unique_library, "AAPL")
        assert aapl is not None
        assert list(aapl.columns) == ["open", "high", "low", "close", "volume"]
        pd.testing.assert_frame_equal(aapl, base, check_exact=True, check_freq=False)

        # The second symbol reads back with its extra column.
        msft = minio_storage.read(unique_library, "MSFT")
        assert msft is not None
        assert list(msft.columns) == ["open", "high", "low", "close", "volume", "vwap"]
        pd.testing.assert_frame_equal(msft, extended, check_exact=True, check_freq=False)

    def test_update_prunes_previous_versions(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        """write() and update() always prune_previous_versions, so exactly one
        version remains after a write + update cycle."""
        df = make_ohlcv("2024-01-02 09:30", periods=10)
        minio_storage.write(unique_library, "AAPL", df)

        updated = make_ohlcv("2024-01-02 09:35", periods=2)
        minio_storage.update(
            unique_library,
            "AAPL",
            updated,
            date_range=(
                updated.index[0].to_pydatetime(),
                updated.index[-1].to_pydatetime(),
            ),
        )

        lib = minio_storage._get_or_create_library(unique_library)
        assert len(lib.list_versions("AAPL")) == 1

    def test_read_with_date_range(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=20)
        minio_storage.write(unique_library, "AAPL", df)

        # Read a subset
        subset = minio_storage.read(
            unique_library,
            "AAPL",
            date_range=(
                datetime(2024, 1, 2, 9, 33, tzinfo=timezone.utc),
                datetime(2024, 1, 2, 9, 37, tzinfo=timezone.utc),
            ),
        )
        assert subset is not None
        assert len(subset) == 5
        assert subset.index[0] == pd.Timestamp("2024-01-02 09:33", tz="UTC")
        assert subset.index[-1] == pd.Timestamp("2024-01-02 09:37", tz="UTC")

    def test_list_symbols(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        minio_storage.write(unique_library, "AAPL", make_ohlcv("2024-01-02", periods=3))
        minio_storage.write(unique_library, "MSFT", make_ohlcv("2024-01-02", periods=3))

        symbols = minio_storage.list_symbols(unique_library)
        assert set(symbols) == {"AAPL", "MSFT"}

    def test_write_with_metadata(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5)
        meta = {"source": "databento", "version": "1.0"}
        minio_storage.write(unique_library, "AAPL", df, metadata=meta)

        # Read back via ArcticDB directly to check metadata
        lib = minio_storage._get_or_create_library(unique_library)
        item = lib.read("AAPL")
        assert item.metadata == meta

    def test_multiple_libraries(
        self, minio_storage: ArcticStorage, unique_library: str
    ) -> None:
        lib2 = unique_library + "-second"
        try:
            df = make_ohlcv("2024-01-02 09:30", periods=3)
            minio_storage.write(unique_library, "AAPL", df)
            minio_storage.write(lib2, "MSFT", df)

            assert "AAPL" in minio_storage.list_symbols(unique_library)
            assert "MSFT" in minio_storage.list_symbols(lib2)
            # Cross-contamination check
            assert "AAPL" not in minio_storage.list_symbols(lib2)
            assert "MSFT" not in minio_storage.list_symbols(unique_library)
        finally:
            # Clean up second library
            try:
                from tests.integration.conftest import TEST_BUCKET

                arctic2 = minio_storage.arctic_for(TEST_BUCKET)
                if lib2 in arctic2.list_libraries():
                    arctic2.delete_library(lib2)
                minio_storage._library_cache.pop(lib2, None)
            except Exception:
                pass
