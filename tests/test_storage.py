"""Tests for ArcticStorage."""

import warnings
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from fin3.config.settings import LockConfig, MinioConfig
from fin3.exceptions import StorageError
from fin3.storage.arctic import ArcticStorage, _suppress_blockmanager_warning
from tests.conftest import make_ohlcv
from tests.test_defrag import fragment_symbol_with_updates


class TestArcticStorage:
    def test_write_and_read(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        result = storage.read("test-lib", "AAPL")
        assert result is not None
        assert len(result) == 10
        # MinIO-twin standard: the whole frame round-trips exactly.
        pd.testing.assert_frame_equal(result, df, check_exact=True, check_freq=False)

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
        # Length preserved: exactly the rows inside date_range are replaced.
        assert len(result) == 10
        # Updated rows carry the new values (exact, not merely different)...
        pd.testing.assert_frame_equal(
            result.iloc[0:3], updated, check_exact=True, check_freq=False
        )
        # ...and untouched rows keep their exact original values.
        pd.testing.assert_frame_equal(
            result.iloc[3:], df.iloc[3:], check_exact=True, check_freq=False
        )

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

        # Closed range: both bounds inclusive, pinned to exact timestamps.
        result = storage.read("test-lib", "AAPL", date_range=(start, end))
        assert result is not None
        assert len(result) == 10
        assert result.index[0] == pd.Timestamp("2024-01-02 09:35", tz=timezone.utc)
        assert result.index[-1] == pd.Timestamp("2024-01-02 09:44", tz=timezone.utc)

        # Half-open: start pinned, end unbounded runs to the last stored row.
        result = storage.read("test-lib", "AAPL", date_range=(start, None))
        assert result is not None
        assert len(result) == 95
        assert result.index[0] == pd.Timestamp("2024-01-02 09:35", tz=timezone.utc)
        assert result.index[-1] == df.index[-1]

        # Half-open: start unbounded runs from the first stored row.
        result = storage.read("test-lib", "AAPL", date_range=(None, end))
        assert result is not None
        assert len(result) == 15
        assert result.index[0] == df.index[0]
        assert result.index[-1] == pd.Timestamp("2024-01-02 09:44", tz=timezone.utc)

    def test_write_metadata(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        meta = {"test_key": "test_value"}
        storage.write("test-lib", "AAPL", df, metadata=meta)

        lib = storage.arctic["test-lib"]
        item = lib.read("AAPL")
        assert item.metadata == meta


class TestArcticStorageEdgeCases:
    def test_arctic_property_empty_raises(self, minio_config: MinioConfig) -> None:
        """The documented test-backdoor property raises before any connection.

        The plain constructor performs no I/O, so a fresh instance has no
        cached Arctic yet — no private-state poking required.
        """
        storage = ArcticStorage(minio_config)
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

    def test_update_nonexistent_symbol_raises(self, storage: ArcticStorage) -> None:
        """update() on a symbol that was never written raises StorageError.

        ArcticDB refuses with 'Cannot update non-existent symbol'; the wrapper
        must surface that instead of silently creating the symbol.
        """
        df = make_ohlcv("2024-01-02 09:30", periods=3)
        start = df.index[0].to_pydatetime()
        end = df.index[-1].to_pydatetime()

        with pytest.raises(StorageError, match="Failed to update test-lib/GHOST"):
            storage.update("test-lib", "GHOST", df, date_range=(start, end))

        # ...and it must not have been silently created by the failed update.
        assert storage.list_symbols("test-lib") == []


class TestReadColumnSelection:
    """read(columns=[...]) returns only the requested columns, index always intact."""

    def test_read_columns_returns_only_close_with_intact_index(
        self, storage: ArcticStorage
    ) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10)
        storage.write("cols-lib", "AAPL", df)

        result = storage.read("cols-lib", "AAPL", columns=["close"])
        assert result is not None
        # Exactly the close column; the UTC index is always returned regardless.
        assert list(result.columns) == ["close"]
        assert result.index.tz is not None
        assert result.index.equals(df.index)
        pd.testing.assert_series_equal(
            result["close"], df["close"], check_exact=True, check_freq=False
        )


class TestDynamicSchema:
    """Libraries are created with LibraryOptions(dynamic_schema=True), so
    symbols with different schemas can coexist without corrupting each other."""

    def test_symbols_with_different_schemas_coexist(self, storage: ArcticStorage) -> None:
        base = make_ohlcv("2024-01-02 09:30", periods=5)
        storage.write("schema-lib", "AAPL", base)

        extended = make_ohlcv("2024-01-02 09:30", periods=4)
        extended["vwap"] = extended["close"] * 1.01
        storage.write("schema-lib", "MSFT", extended)

        # The first symbol still reads back exactly its 5 written columns.
        aapl = storage.read("schema-lib", "AAPL")
        assert aapl is not None
        assert list(aapl.columns) == ["open", "high", "low", "close", "volume"]
        pd.testing.assert_frame_equal(aapl, base, check_exact=True, check_freq=False)

        # The second symbol reads back with its extra column.
        msft = storage.read("schema-lib", "MSFT")
        assert msft is not None
        assert list(msft.columns) == ["open", "high", "low", "close", "volume", "vwap"]
        pd.testing.assert_frame_equal(msft, extended, check_exact=True, check_freq=False)


class TestWarningSuppression:
    """The BlockManagerUnconsolidated deprecation is suppressed on reads.

    A module-level global filter proved fragile (arcticdb and other libs
    re-prepend filters at import, landing ahead of ours and winning). The
    suppression is now scoped to arcticdb read calls via a context manager.
    """

    def test_context_manager_suppresses_target_only(self) -> None:
        """_suppress_blockmanager_warning hides the target message, lets others through."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with _suppress_blockmanager_warning():
                warnings.warn(
                    "Passing a BlockManagerUnconsolidated to DataFrame is deprecated",
                    DeprecationWarning,
                )
                warnings.warn("some unrelated deprecation", DeprecationWarning)
        msgs = [str(w.message) for w in caught]
        assert any("some unrelated deprecation" in m for m in msgs)
        assert not any("BlockManagerUnconsolidated" in m for m in msgs)

    def test_context_manager_is_scoped(self) -> None:
        """After the context exits, the target warning is visible again."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with _suppress_blockmanager_warning():
                pass
            warnings.warn(
                "Passing a BlockManagerUnconsolidated to DataFrame is deprecated",
                DeprecationWarning,
            )
        assert any(
            "BlockManagerUnconsolidated" in str(w.message) for w in caught
        )

    def test_read_does_not_emit_blockmanager_warning(
        self, storage: ArcticStorage
    ) -> None:
        """A real arcticdb read through ArcticStorage.read suppresses the warning.

        Reproduces the original bug: arcticdb installs a global
        'always | DeprecationWarning' filter that overrides any module-level
        ignore filter. We intercept showwarning (without touching filters) to
        detect whether the warning actually reaches display during a read.
        """
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        storage.write("warn-lib", "AAPL", df)

        real = warnings.showwarning
        hits: list[str] = []

        def _hook(*args: object, **kwargs: object) -> None:
            hits.append(str(args[0]))

        warnings.showwarning = _hook
        try:
            result = storage.read("warn-lib", "AAPL")
        finally:
            warnings.showwarning = real

        assert result is not None
        assert not any("BlockManagerUnconsolidated" in h for h in hits)


class TestSymbolLocking:
    """Per-(library, symbol) advisory locking around mutating operations."""

    def test_lock_symbol_is_noop_when_locking_disabled(self, tmp_path) -> None:
        """With LockConfig(enabled=False), lock_symbol is usable as a context
        manager around mutations but never creates a lock file on disk."""
        lock_dir = tmp_path / "locks"
        storage = ArcticStorage.from_lmdb(
            str(tmp_path / "lmdb"),
            lock=LockConfig(enabled=False, lock_dir=str(lock_dir)),
        )

        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        with storage.lock_symbol("test-lib", "AAPL"):
            storage.write("test-lib", "AAPL", df)

        assert storage.has_symbol("test-lib", "AAPL")
        assert not any(lock_dir.glob("*.lock"))

    def test_delete_symbol_acquires_lock(self, tmp_path, monkeypatch) -> None:
        storage = ArcticStorage.from_lmdb(
            str(tmp_path / "lmdb"),
            lock=LockConfig(enabled=True, lock_dir=str(tmp_path / "locks")),
        )
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        storage.write("test-lib", "AAPL", df)

        # Wrap the real lock_symbol so the call is recorded and still performed.
        spy = MagicMock(wraps=storage.lock_symbol)
        monkeypatch.setattr(storage, "lock_symbol", spy)

        storage.delete_symbol("test-lib", "AAPL")

        spy.assert_called_once_with("test-lib", "AAPL")
        assert not storage.has_symbol("test-lib", "AAPL")

    def test_defragment_symbol_acquires_lock(self, tmp_path, monkeypatch) -> None:
        storage = ArcticStorage.from_lmdb(
            str(tmp_path / "lmdb"),
            lock=LockConfig(enabled=True, lock_dir=str(tmp_path / "locks")),
        )
        storage.write(
            "test-lib", "AAPL", make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        )

        # Wrap the real lock_symbol so the call is recorded and still performed.
        spy = MagicMock(wraps=storage.lock_symbol)
        monkeypatch.setattr(storage, "lock_symbol", spy)

        # Genuinely fragmented fixture, so the compaction actually mutates:
        # mutation-under-lock is the contract, not the early-return path.
        fragment_symbol_with_updates(
            storage, "test-lib", "AAPL", first_minute_of_day=9 * 60 + 40
        )
        segments_before = storage.get_segment_count("test-lib", "AAPL")
        assert storage.is_symbol_fragmented("test-lib", "AAPL") is True

        storage.defragment_symbol("test-lib", "AAPL", prune_previous_versions=True)

        spy.assert_called_once_with("test-lib", "AAPL")
        assert storage.get_segment_count("test-lib", "AAPL") < segments_before

    def test_lock_symbol_is_real_lock_when_enabled(self, tmp_path) -> None:
        """When locking is enabled, lock_symbol actually creates a lock file."""
        lock_dir = tmp_path / "locks"
        storage = ArcticStorage.from_lmdb(
            str(tmp_path / "lmdb"),
            lock=LockConfig(enabled=True, lock_dir=str(lock_dir)),
        )
        with storage.lock_symbol("test-lib", "AAPL"):
            pass
        # The lock file is left in place after release (see locking.py docs).
        assert any(lock_dir.glob("*.lock"))
