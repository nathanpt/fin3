"""Tests for ArcticStorage."""

from datetime import datetime, timezone

import pytest

from fin3.exceptions import StorageError
from fin3.storage.arctic import ArcticStorage
from tests.conftest import make_ohlcv


class TestArcticStorage:
    def test_write_and_read(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        result = storage.read("test-lib", "AAPL")
        assert result is not None
        assert len(result) == 10

    def test_read_nonexistent_symbol_returns_none(self, storage: ArcticStorage) -> None:
        result = storage.read("test-lib", "NONEXISTENT")
        assert result is None

    def test_update_overwrites_range(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)

        updated = make_ohlcv(
            "2024-01-02 09:30", periods=3, freq="1min", base_price=200.0
        )
        start = updated.index[0].to_pydatetime()
        end = updated.index[-1].to_pydatetime()
        storage.update("test-lib", "AAPL", updated, date_range=(start, end))

        result = storage.read("test-lib", "AAPL")
        assert result is not None
        assert result.iloc[0]["close"] != df.iloc[0]["close"]

    def test_list_symbols(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "TSLA", df)
        symbols = storage.list_symbols("test-lib")
        assert set(symbols) == {"AAPL", "TSLA"}

    def test_has_symbol(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        storage.write("test-lib", "AAPL", df)
        assert storage.has_symbol("test-lib", "AAPL") is True
        assert storage.has_symbol("test-lib", "TSLA") is False

    def test_read_with_date_range(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=100, freq="1min")
        storage.write("test-lib", "AAPL", df)

        start = datetime(2024, 1, 2, 9, 35, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 9, 44, tzinfo=timezone.utc)
        result = storage.read("test-lib", "AAPL", date_range=(start, end))
        assert result is not None
        assert len(result) == 10

    def test_write_metadata(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        meta = {"test_key": "test_value"}
        storage.write("test-lib", "AAPL", df, metadata=meta)

        lib = storage.arctic["test-lib"]
        item = lib.read("AAPL")
        assert item.metadata == meta


class TestArcticStorageEdgeCases:
    def test_arctic_property_empty_raises(self) -> None:
        storage = ArcticStorage.__new__(ArcticStorage)
        storage._arctic_cache = {}
        with pytest.raises(StorageError, match="No Arctic instances"):
            _ = storage.arctic

    def test_read_nonexistent_library_returns_none(self, storage: ArcticStorage) -> None:
        result = storage.read("brand-new-lib", "NOEXIST")
        assert result is None

    def test_write_and_update_cycle(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("cycle-lib", "AAPL", df)

        update = make_ohlcv("2024-01-02 09:35", periods=3, freq="1min", base_price=200.0)
        start = update.index[0].to_pydatetime()
        end = update.index[-1].to_pydatetime()
        storage.update("cycle-lib", "AAPL", update, date_range=(start, end))

        result = storage.read("cycle-lib", "AAPL")
        assert result is not None
        assert len(result) == 10
        # First 5 rows unchanged, rows 5-7 updated
        assert result.iloc[0]["close"] == pytest.approx(100.2)
        assert result.iloc[5]["close"] == pytest.approx(200.2)
