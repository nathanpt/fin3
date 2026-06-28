"""Cross-process advisory locks keyed by ``(library, symbol)``.

This module backs the concurrent-access-protection feature for
``MarketDataFetcher.get_data()``. ``get_data()`` performs a check-then-act
sequence (detect a data gap, fetch from a provider, ``update()`` ArcticDB)
which two processes can race on: both detect the same gap, both fetch, and the
second ``update()`` (with ``prune_previous_versions=True``) silently clobbers
the first with no rollback. ``SymbolLock`` provides the primitive that will be
wired in to serialise per-symbol access.

Design properties
-----------------

* **Mutual exclusion**: ``SymbolLock`` is built on :func:`fcntl.flock` with
  ``LOCK_EX``. ``flock`` provides kernel-mediated mutual exclusion between
  *processes* on the same host: only one process may hold an exclusive lock on
  a given open file at a time.

* **Auto-release on crash**: an ``flock`` is tied to the open file
  *description*, not the process. When a process exits for any reason — clean
  return, unhandled exception, ``SIGKILL``, segfault — the kernel closes its
  file descriptors and the lock is released automatically. There is therefore
  no "stale lock" hazard and no need for a heartbeat or lease. The flip side:
  the file descriptor must stay open for as long as the lock should be held, so
  ``acquire()`` never closes it until ``_HeldLock.__exit__``.

* **Granularity**: locks are scoped to ``(library, symbol)``. Each pair maps to
  its own lock file, so fetching ``AAPL`` never blocks a concurrent fetch of
  ``MSFT``.

* **Deadlock freedom**: a single ``get_data()`` operation holds at most one
  such lock at a time and releases it before acquiring another, so a
  wait-for cycle is structurally impossible.

The lock file is *not* deleted on release. Deleting it would reintroduce a
TOCTOU race in which a third process recreates the file between our ``unlink``
and another caller's ``open``; ``flock`` on a leftover/stale path is harmless,
so reuse is safe.

Stdlib only (``fcntl``, ``os``, ``re``, ``socket``, ``time``). ``fcntl`` is a
Unix-only module; importing it is guarded so the module still imports on
non-Unix platforms (where ``acquire()`` raises a clear error).
"""

from __future__ import annotations

import errno
import os
import re
import socket
import structlog
import time
from types import TracebackType

# ``fcntl`` is Unix-only. Guard the import so the module still imports on
# non-Unix platforms (e.g. Windows); ``acquire()`` raises a clear error there.
try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix platforms
    fcntl = None  # type: ignore[assignment]

logger = structlog.get_logger(__name__)

# Any character outside [A-Za-z0-9._-] is replaced with "_" when building a
# lock-file name. This keeps the (library, symbol) pair distinguishable and
# path-safe while forbidding "/" (directory traversal) and other metacharacters.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_filename_component(component: str) -> str:
    """Replace any path-unsafe character in *component* with ``_``."""
    return _UNSAFE_FILENAME_CHARS.sub("_", component)


class LockAcquisitionError(RuntimeError):
    """Raised when a per-symbol lock cannot be acquired within the timeout.

    Carries identifying information so an operator can locate (and, if needed,
    kill) the process that is holding the lock.

    Attributes
    ----------
    symbol, library
        The ``(library, symbol)`` pair that was contended.
    timeout_s
        The configured wait budget that elapsed.
    holder_pid, holder_hostname
        Best-effort identity of the current holder, read back from the lock
        file. Either/both may be ``None`` if the file was unreadable or empty
        (e.g. the holder crashed mid-write).
    lock_path
        Absolute path of the lock file on disk.
    """

    def __init__(
        self,
        symbol: str,
        library: str,
        timeout_s: float,
        holder_pid: int | None,
        holder_hostname: str | None,
        lock_path: str,
    ) -> None:
        self.symbol = symbol
        self.library = library
        self.timeout_s = timeout_s
        self.holder_pid = holder_pid
        self.holder_hostname = holder_hostname
        self.lock_path = lock_path

        if holder_pid is not None and holder_hostname is not None:
            holder_desc = f"held by PID {holder_pid} on {holder_hostname}"
        elif holder_pid is not None:
            holder_desc = f"held by PID {holder_pid} (hostname unknown)"
        else:
            holder_desc = "current holder unknown"

        message = (
            f"Timed out after {timeout_s}s waiting for lock on "
            f"{library}/{symbol} at {lock_path}; {holder_desc}. "
            f"If that process has crashed the lock auto-releases on exit."
        )
        super().__init__(message)


class _HeldLock:
    """Context manager wrapping a successfully acquired advisory lock.

    On ``__exit__`` the ``flock`` is released (``LOCK_UN``) and the file
    descriptor closed. The lock file is intentionally left in place: see the
    module docstring for the rationale (avoids a TOCTOU recreate race).
    """

    def __init__(self, fd: int, library: str, symbol: str, lock_path: str) -> None:
        self._fd = fd
        self.library = library
        self.symbol = symbol
        self.lock_path = lock_path

    @property
    def fd(self) -> int:
        """The locked file descriptor (advanced use; do not close manually)."""
        return self._fd

    def __enter__(self) -> _HeldLock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if fcntl is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        logger.debug(
            "lock.released",
            library=self.library,
            symbol=self.symbol,
            lock_path=self.lock_path,
        )


class SymbolLock:
    """Factory of per-``(library, symbol)`` advisory locks backed by ``flock``.

    Example::

        lock = SymbolLock("/var/lib/fin3/locks")
        with lock.acquire("equities-1m-databento", "AAPL"):
            # exclusive access to this (library, symbol); other processes
            # calling acquire() for the same pair block (or time out)
            ...

    The same ``SymbolLock`` instance is reused for many symbols; the
    per-symbol lock file is derived from the ``(library, symbol)`` arguments.
    """

    def __init__(
        self,
        lock_dir: str,
        timeout_s: float = 600.0,
        poll_interval_s: float = 0.5,
    ) -> None:
        """Initialise the lock manager.

        Parameters
        ----------
        lock_dir : str
            Directory on the local filesystem where lock files are stored.
        timeout_s : float
            Maximum time to wait for a contended lock before raising.
        poll_interval_s : float
            Polling interval while waiting for a lock.
        """
        self._lock_dir = lock_dir
        self._timeout_s = timeout_s
        self._poll_interval_s = poll_interval_s

    def _lock_path(self, library: str, symbol: str) -> str:
        """Return the on-disk path for a given ``(library, symbol)`` pair."""
        safe_library = _sanitize_filename_component(library)
        safe_symbol = _sanitize_filename_component(symbol)
        filename = f"{safe_library}__{safe_symbol}.lock"
        return os.path.join(self._lock_dir, filename)

    def acquire(self, library: str, symbol: str) -> _HeldLock:
        """Acquire an exclusive lock for ``(library, symbol)``.

        Blocks (polling) until the lock is acquired or *timeout_s* elapses. On
        timeout, reads back the current holder's identity (best-effort) and
        raises :class:`LockAcquisitionError`. Returns a context manager that
        releases the lock on ``__exit__``.
        """
        if fcntl is None:
            raise RuntimeError(
                "SymbolLock requires fcntl (Unix); not available on this platform"
            )

        lock_path = self._lock_path(library, symbol)
        os.makedirs(self._lock_dir, exist_ok=True)
        deadline = time.monotonic() + self._timeout_s

        # Open (create-if-needed) the lock file. O_RDWR so we can later rewrite
        # holder info; O_CREAT so a missing file is created atomically by the
        # kernel. This open() yields a distinct open file description per
        # caller, which is what makes flock mutual exclusion work between
        # processes (and between this caller and the current holder).
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    # flock(LOCK_NB) reports contention via EAGAIN/EWOULDBLOCK.
                    # Anything else is unexpected; propagate it.
                    if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                        raise
                    if time.monotonic() >= deadline:
                        holder_pid, holder_hostname = self._read_holder(fd)
                        logger.warning(
                            "lock.timeout",
                            library=library,
                            symbol=symbol,
                            lock_path=lock_path,
                            timeout_s=self._timeout_s,
                            holder_pid=holder_pid,
                            holder_hostname=holder_hostname,
                        )
                        raise LockAcquisitionError(
                            symbol=symbol,
                            library=library,
                            timeout_s=self._timeout_s,
                            holder_pid=holder_pid,
                            holder_hostname=holder_hostname,
                            lock_path=lock_path,
                        ) from exc
                    time.sleep(self._poll_interval_s)
        except BaseException:
            # Timed out, or an unexpected error: never leak the fd. On the
            # success path we return below, transferring fd ownership to the
            # _HeldLock.
            os.close(fd)
            raise

        self._write_holder(fd)
        logger.info(
            "lock.acquired",
            library=library,
            symbol=symbol,
            lock_path=lock_path,
            pid=os.getpid(),
        )
        return _HeldLock(fd=fd, library=library, symbol=symbol, lock_path=lock_path)

    @staticmethod
    def _write_holder(fd: int) -> None:
        """Write the current process's PID and hostname into the lock file.

        Two newline-separated lines, truncated/rewritten each acquisition.
        Raw ``os`` I/O is unbuffered (no ``flush`` needed); ``fsync`` forces
        durability so a contending caller can reliably read back the holder.
        """
        payload = f"{os.getpid()}\n{socket.gethostname()}\n".encode("utf-8")
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
        os.fsync(fd)

    @staticmethod
    def _read_holder(fd: int) -> tuple[int | None, str | None]:
        """Best-effort read of the holder's PID and hostname from the lock file.

        Returns ``(None, None)`` if the file is unreadable or malformed.
        """
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            data = os.read(fd, 4096)
        except OSError:
            return None, None

        lines = data.decode("utf-8", errors="replace").splitlines()
        holder_pid: int | None = None
        holder_hostname: str | None = None
        if len(lines) >= 1:
            try:
                holder_pid = int(lines[0].strip())
            except ValueError:
                holder_pid = None
        if len(lines) >= 2:
            holder_hostname = lines[1].strip() or None
        return holder_pid, holder_hostname
