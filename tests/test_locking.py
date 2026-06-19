"""Tests for fin3.storage.locking — SymbolLock cross-process advisory locks.

All tests are filesystem-only (no MinIO, no ArcticDB). Cross-process behaviour
is exercised with ``multiprocessing`` using the ``spawn`` start method: spawned
children are fully independent processes (no inherited file descriptors), which
is what makes ``flock`` mutual exclusion observable. Worker functions are kept
at module scope so they are picklable by ``spawn``.
"""

from __future__ import annotations

import multiprocessing
import os
import socket
from pathlib import Path

import pytest

from fin3.storage.locking import LockAcquisitionError, SymbolLock

LIBRARY = "test-lib"


# ---------------------------------------------------------------------------
# Module-level worker functions (must be top-level for multiprocessing.spawn)
# ---------------------------------------------------------------------------


def _worker_acquire_and_die(
    lock_dir: str,
    library: str,
    symbol: str,
    ready: multiprocessing.synchronize.Event,
) -> None:
    """Acquire a lock, signal readiness, then exit abruptly (simulate crash).

    ``os._exit`` skips ``_HeldLock.__exit__`` and all ``finally`` blocks, so the
    lock is released only by the OS closing the file descriptor on process exit
    — exactly the crash-recovery path we want to prove works.
    """
    lock = SymbolLock(lock_dir, timeout_s=30.0)
    # Acquire without a `with` block so __exit__ is never reached.
    lock.acquire(library, symbol)
    ready.set()
    os._exit(0)


def _worker_acquire_times_out(
    lock_dir: str,
    library: str,
    symbol: str,
    result_queue: multiprocessing.Queue,
) -> None:
    """Attempt to acquire an already-held lock; report the outcome via a queue."""
    lock = SymbolLock(lock_dir, timeout_s=0.5, poll_interval_s=0.1)
    try:
        with lock.acquire(library, symbol):
            result_queue.put({"status": "acquired"})
    except LockAcquisitionError as exc:
        result_queue.put(
            {
                "status": "timeout",
                "symbol": exc.symbol,
                "library": exc.library,
                "holder_pid": exc.holder_pid,
                "holder_hostname": exc.holder_hostname,
                "lock_path": exc.lock_path,
                "message": str(exc),
            }
        )


def _spawn_context() -> multiprocessing.context.BaseContext:
    """Return a fresh ``spawn`` multiprocessing context (no inherited fds)."""
    return multiprocessing.get_context("spawn")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSymbolLock:
    def test_basic_acquire_release(self, tmp_path: Path) -> None:
        lock = SymbolLock(str(tmp_path), timeout_s=5.0)
        pid = os.getpid()
        hostname = socket.gethostname()

        held = lock.acquire(LIBRARY, "AAPL")
        try:
            lock_path = held.lock_path
            assert os.path.exists(lock_path)
            # Holder info is written as PID then hostname, two lines.
            with open(lock_path) as f:
                lines = f.read().splitlines()
            assert int(lines[0]) == pid
            assert lines[1] == hostname
        finally:
            held.__exit__(None, None, None)

        # After release the lock can be re-acquired immediately by the same
        # process (the file is intentionally left in place).
        with lock.acquire(LIBRARY, "AAPL"):
            pass

    def test_timeout_raises_and_reports_holder(self, tmp_path: Path) -> None:
        lock_dir = str(tmp_path)
        lock = SymbolLock(lock_dir, timeout_s=5.0)
        parent_pid = os.getpid()

        # Parent holds the lock for the whole test.
        held = lock.acquire(LIBRARY, "AAPL")
        try:
            ctx = _spawn_context()
            queue = ctx.Queue()
            child = ctx.Process(
                target=_worker_acquire_times_out,
                args=(lock_dir, LIBRARY, "AAPL", queue),
            )
            child.start()
            result = queue.get(timeout=30.0)
            child.join(timeout=30.0)

            assert result["status"] == "timeout"
            # The child should have read the parent's identity back from the
            # lock file and attached it to the error.
            assert result["holder_pid"] == parent_pid
            assert result["holder_hostname"] == socket.gethostname()
            assert result["symbol"] == "AAPL"
            assert result["library"] == LIBRARY
            assert str(parent_pid) in result["message"]
            assert isinstance(result["lock_path"], str)
            assert not child.is_alive()
        finally:
            held.__exit__(None, None, None)

    def test_stale_lock_auto_releases_on_child_exit(self, tmp_path: Path) -> None:
        lock_dir = str(tmp_path)
        lock = SymbolLock(lock_dir, timeout_s=5.0)

        # Parent acquires once first to confirm the file works and is free.
        with lock.acquire(LIBRARY, "AAPL"):
            pass

        # Spawn a child that acquires and then dies *while holding* the lock.
        ctx = _spawn_context()
        ready = ctx.Event()
        child = ctx.Process(
            target=_worker_acquire_and_die,
            args=(lock_dir, LIBRARY, "AAPL", ready),
        )
        child.start()
        assert ready.wait(timeout=30.0), "child did not acquire the lock in time"
        child.join(timeout=30.0)
        assert not child.is_alive()

        # The child released the lock only via process-exit fd cleanup. The
        # parent must now be able to acquire it again.
        parent_lock = SymbolLock(lock_dir, timeout_s=10.0)
        with parent_lock.acquire(LIBRARY, "AAPL"):
            pass

    def test_different_symbols_do_not_block(self, tmp_path: Path) -> None:
        lock = SymbolLock(str(tmp_path), timeout_s=5.0)
        with lock.acquire(LIBRARY, "AAPL"):
            # A different symbol must be acquirable immediately while AAPL is
            # held; if it blocked this would time out and raise.
            with lock.acquire(LIBRARY, "MSFT"):
                pass

    def test_filename_sanitization(self, tmp_path: Path) -> None:
        # "/" is path-unsafe; it must be replaced, while still keeping the
        # (library, symbol) pair distinguishable.
        library = "equities-1m-databento"
        symbol = "BRK/A"
        lock = SymbolLock(str(tmp_path), timeout_s=5.0)

        with lock.acquire(library, symbol):
            lock_files = list(tmp_path.glob("*.lock"))
            assert len(lock_files) == 1
            name = lock_files[0].name
            assert name == "equities-1m-databento__BRK_A.lock"
            assert "/" not in name
            assert "BRK_A" in name

        # The sanitized lock still round-trips and re-acquires fine.
        with lock.acquire(library, symbol):
            pass

    def test_context_manager_releases_on_exception(self, tmp_path: Path) -> None:
        lock = SymbolLock(str(tmp_path), timeout_s=5.0)

        class _BoomError(Exception):
            pass

        # Normal exits release the lock (re-acquire works back to back).
        with lock.acquire(LIBRARY, "AAPL"):
            pass
        with lock.acquire(LIBRARY, "AAPL"):
            pass

        # An exception raised inside the `with` block must still release.
        with pytest.raises(_BoomError):
            with lock.acquire(LIBRARY, "AAPL"):
                raise _BoomError("boom")

        # If __exit__ had not released on exception, this would time out.
        with lock.acquire(LIBRARY, "AAPL"):
            pass

    def test_missing_fcntl_raises_on_acquire(self, tmp_path: Path) -> None:
        # Simulate a non-Unix platform where fcntl is unavailable: acquire()
        # must raise a clear, actionable RuntimeError rather than an
        # AttributeError on the fcntl module.
        import fin3.storage.locking as locking

        lock = SymbolLock(str(tmp_path), timeout_s=5.0)
        original = locking.fcntl
        locking.fcntl = None
        try:
            with pytest.raises(RuntimeError, match="fcntl"):
                lock.acquire(LIBRARY, "AAPL")
        finally:
            locking.fcntl = original
