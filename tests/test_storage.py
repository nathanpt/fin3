"""Tests for ArcticStorage."""

import contextlib
import warnings
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from fin3.config.settings import LockConfig
from fin3.exceptions import StorageError
from fin3.storage.arctic import ArcticStorage, _suppress_blockmanager_warning
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

    def test_lock_symbol_is_noop_when_locking_disabled(
        self, storage: ArcticStorage
    ) -> None:
        """Without a LockConfig, lock_symbol yields a nullcontext (no lock files)."""
        cm = storage.lock_symbol("test-lib", "AAPL")
        assert isinstance(cm, contextlib.nullcontext)

    def test_delete_symbol_acquires_lock(self, tmp_path) -> None:
        storage = ArcticStorage.from_lmdb(
            str(tmp_path / "lmdb"),
            lock=LockConfig(enabled=True, lock_dir=str(tmp_path / "locks")),
        )
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        storage.write("test-lib", "AAPL", df)

        # Wrap the real lock_symbol so the call is recorded and still performed.
        spy = MagicMock(wraps=storage.lock_symbol)
        storage.lock_symbol = spy  # type: ignore[method-assign]

        storage.delete_symbol("test-lib", "AAPL")

        spy.assert_called_once_with("test-lib", "AAPL")
        assert not storage.has_symbol("test-lib", "AAPL")

    def test_defragment_symbol_acquires_lock(self, tmp_path) -> None:
        storage = ArcticStorage.from_lmdb(
            str(tmp_path / "lmdb"),
            lock=LockConfig(enabled=True, lock_dir=str(tmp_path / "locks")),
        )
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        storage.write("test-lib", "AAPL", df)

        spy = MagicMock(wraps=storage.lock_symbol)
        storage.lock_symbol = spy  # type: ignore[method-assign]

        # The lock wraps the fragmentation check too, so it is acquired even
        # when there is nothing to compact.
        storage.defragment_symbol("test-lib", "AAPL")

        spy.assert_called_once_with("test-lib", "AAPL")

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
