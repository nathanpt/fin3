"""Tests for fin3.storage.locking — SymbolLock cross-process advisory locks.

All tests are filesystem-only (no MinIO, no ArcticDB). Cross-process behaviour
is exercised with ``multiprocessing`` using the ``spawn`` start method: spawned
children are fully independent processes (no inherited file descriptors), which
is what makes ``flock`` mutual exclusion observable. Worker functions are kept
at module scope so they are picklable by ``spawn``.
"""

from __future__ import annotations

import errno
import fcntl
import multiprocessing
import os
import socket
import time
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


class TestSameProcessContention:
    """flock mutual exclusion is per open file description, not per process.

    A second ``acquire()`` from the *same* process (a distinct ``os.open``,
    hence a distinct file description) must contend with the first. This pins
    the non-reentrant semantics: a "helpful" reentrant refactor would let the
    second acquire succeed and break cross-process mutual exclusion.
    """

    def test_same_process_reacquire_times_out(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No real sleeps: the deadline (0.2s) is enforced before any sleep
        # would matter, and poll_interval_s keeps any polling wait tiny.
        lock = SymbolLock(str(tmp_path), timeout_s=0.2, poll_interval_s=0.05)
        with lock.acquire(LIBRARY, "AAPL"):
            with pytest.raises(LockAcquisitionError) as excinfo:
                lock.acquire(LIBRARY, "AAPL")
            exc = excinfo.value
            # Holder info is read back from the lock file we wrote ourselves.
            assert exc.holder_pid == os.getpid()
            assert exc.holder_hostname == socket.gethostname()
            assert exc.symbol == "AAPL"
            assert exc.library == LIBRARY
            assert exc.timeout_s == 0.2
            assert exc.lock_path == str(tmp_path / "test-lib__AAPL.lock")
            assert f"held by PID {os.getpid()} on {socket.gethostname()}" in str(exc)
            # The timeout raise is chained to the EAGAIN from flock.
            assert isinstance(exc.__cause__, OSError)
            assert exc.__cause__.errno in (errno.EAGAIN, errno.EWOULDBLOCK)

        # The failed attempt must not have disturbed the held lock.
        with lock.acquire(LIBRARY, "AAPL"):
            pass

    def test_acquire_polls_then_succeeds_when_holder_releases(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Cover the poll-and-retry branch: the first LOCK_NB attempt hits
        # EAGAIN, one poll_interval_s "sleep" happens, and the lock is
        # acquired on the next iteration. time.sleep is patched to release
        # the contending holder, so no real sleeping occurs.
        lock = SymbolLock(str(tmp_path), timeout_s=10.0, poll_interval_s=0.05)
        lock_path = tmp_path / "test-lib__AAPL.lock"
        holder_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(holder_fd, fcntl.LOCK_EX)

        slept: list[float] = []

        def fake_sleep(seconds: float) -> None:
            slept.append(seconds)
            # The other holder goes away while we are waiting.
            fcntl.flock(holder_fd, fcntl.LOCK_UN)

        monkeypatch.setattr(time, "sleep", fake_sleep)
        try:
            with lock.acquire(LIBRARY, "AAPL"):
                assert slept == [0.05]
                # The fresh fd is genuinely locked: a third-party LOCK_NB
                # attempt on the same file reports contention.
                probe_fd = os.open(str(lock_path), os.O_RDWR)
                try:
                    with pytest.raises(OSError) as excinfo:
                        fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    assert excinfo.value.errno in (errno.EAGAIN, errno.EWOULDBLOCK)
                finally:
                    os.close(probe_fd)
        finally:
            os.close(holder_fd)

    def test_held_lock_release_makes_lock_free_again(self, tmp_path: Path) -> None:
        # __exit__ unlocks the flock and closes the fd: afterwards a raw
        # third-party LOCK_EX succeeds on the very same file.
        lock = SymbolLock(str(tmp_path), timeout_s=5.0)
        lock_path = tmp_path / "test-lib__AAPL.lock"
        with lock.acquire(LIBRARY, "AAPL") as held:
            assert isinstance(held.fd, int)
            probe_fd = os.open(str(lock_path), os.O_RDWR)
            with pytest.raises(OSError):
                fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.close(probe_fd)

        probe_fd = os.open(str(lock_path), os.O_RDWR)
        try:
            fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # must not raise
            fcntl.flock(probe_fd, fcntl.LOCK_UN)
        finally:
            os.close(probe_fd)

    def test_unexpected_flock_error_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Only EAGAIN/EWOULDBLOCK mean contention; any other OSError from
        # flock is a real failure and must propagate (after fd cleanup),
        # not be swallowed by the retry loop.
        lock = SymbolLock(str(tmp_path), timeout_s=5.0)

        def boom(fd: int, operation: int) -> None:
            raise OSError(errno.EBADF, "simulated unexpected flock failure")

        monkeypatch.setattr(fcntl, "flock", boom)
        with pytest.raises(OSError) as excinfo:
            lock.acquire(LIBRARY, "AAPL")
        assert excinfo.value.errno == errno.EBADF


class TestHolderInfo:
    """Direct coverage of holder-identity plumbing: error-message branches
    and best-effort ``_read_holder`` parsing."""

    @staticmethod
    def _make_error(
        holder_pid: int | None, holder_hostname: str | None
    ) -> LockAcquisitionError:
        return LockAcquisitionError(
            symbol="AAPL",
            library=LIBRARY,
            timeout_s=1.5,
            holder_pid=holder_pid,
            holder_hostname=holder_hostname,
            lock_path="/tmp/locks/test-lib__AAPL.lock",
        )

    def test_message_with_full_holder_info(self) -> None:
        exc = self._make_error(4242, "box-9")
        assert exc.symbol == "AAPL"
        assert exc.library == LIBRARY
        assert exc.timeout_s == 1.5
        assert exc.holder_pid == 4242
        assert exc.holder_hostname == "box-9"
        assert exc.lock_path == "/tmp/locks/test-lib__AAPL.lock"
        assert "Timed out after 1.5s" in str(exc)
        assert "held by PID 4242 on box-9" in str(exc)

    def test_message_with_pid_only(self) -> None:
        exc = self._make_error(4242, None)
        assert "held by PID 4242 (hostname unknown)" in str(exc)

    def test_message_with_unknown_holder(self) -> None:
        exc = self._make_error(None, None)
        assert "current holder unknown" in str(exc)

    @staticmethod
    def _fd_with(tmp_path: Path, payload: bytes) -> int:
        fd = os.open(str(tmp_path / "holder.lock"), os.O_RDWR | os.O_CREAT, 0o644)
        os.write(fd, payload)
        return fd

    def test_reads_pid_and_hostname(self, tmp_path: Path) -> None:
        fd = self._fd_with(tmp_path, b"4242\nsome-host\n")
        try:
            assert SymbolLock._read_holder(fd) == (4242, "some-host")
        finally:
            os.close(fd)

    def test_empty_file_returns_nones(self, tmp_path: Path) -> None:
        fd = self._fd_with(tmp_path, b"")
        try:
            assert SymbolLock._read_holder(fd) == (None, None)
        finally:
            os.close(fd)

    def test_non_integer_pid_yields_none_pid(self, tmp_path: Path) -> None:
        fd = self._fd_with(tmp_path, b"not-a-pid\nsome-host\n")
        try:
            assert SymbolLock._read_holder(fd) == (None, "some-host")
        finally:
            os.close(fd)

    def test_single_line_yields_pid_without_hostname(self, tmp_path: Path) -> None:
        fd = self._fd_with(tmp_path, b"4242\n")
        try:
            assert SymbolLock._read_holder(fd) == (4242, None)
        finally:
            os.close(fd)

    def test_unreadable_fd_returns_nones(self, tmp_path: Path) -> None:
        # A closed fd makes os.lseek raise OSError; the reader must degrade
        # to (None, None) instead of propagating (holder crashed mid-write).
        fd = self._fd_with(tmp_path, b"4242\nsome-host\n")
        os.close(fd)
        assert SymbolLock._read_holder(fd) == (None, None)
