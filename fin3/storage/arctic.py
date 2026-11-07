"""ArcticDB + MinIO (or LMDB) storage adapter."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import urllib.error
import urllib.request
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, ContextManager, Iterator, cast

import arcticdb as adb
import pandas as pd
import structlog
from arcticdb.version_store.admin_tools import sum_sizes

from fin3.config.settings import LockConfig, MinioConfig
from fin3.exceptions import StorageError
from fin3.storage.locking import SymbolLock

logger = structlog.get_logger(__name__)

_S3_LIB_NAME = "main"


@contextmanager
def _suppress_blockmanager_warning() -> Iterator[None]:
    """Suppress the pandas/arcticdb ``BlockManagerUnconsolidated`` deprecation.

    ArcticDB constructs returned DataFrames via a pandas internal path that
    pandas deprecates ("Passing a BlockManagerUnconsolidated to DataFrame is
    deprecated"). The warning originates in arcticdb's code, not ours, and is
    not actionable until arcticdb updates. It fires on essentially every read
    and, when it reaches the terminal mid-render, tears apart the live
    monitor's in-place redraw.

    A module-level ``warnings.filterwarnings`` is unreliable here: arcticdb
    and other libs re-prepend their own filters at import time, landing ahead
    of ours and winning. Using ``catch_warnings()`` scopes the suppression to
    just the read call, independent of the global filter ordering.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r".*BlockManagerUnconsolidated.*",
            category=DeprecationWarning,
        )
        yield


class ArcticStorage:
    """Thin wrapper around ArcticDB with per-library bucket management.

    Each library maps to its own S3 bucket (e.g. ``equities-1m-databento``).
    Inside each bucket a single ArcticDB library named ``main`` is used.
    """

    def __init__(self, config: MinioConfig, lock: LockConfig | None = None) -> None:
        """Initialise the ArcticDB storage wrapper.

        Parameters
        ----------
        config : MinioConfig
            MinIO / S3 connection settings and bucket configuration.
        lock : LockConfig or None
            Cross-process locking configuration. Pass ``None`` or a config
            with ``enabled=False`` to disable locking.
        """
        self._config = config
        self._scheme = "s3s" if config.secure else "s3"
        self._host, _, self._port = config.endpoint.partition(":")
        self._arctic_cache: dict[str, adb.Arctic] = {}
        self._library_cache: dict[str, adb.Library] = {}
        self._is_lmdb = False
        # When bucket is set, all libraries live inside this single bucket
        # using the library name as the ArcticDB library name.
        self._single_bucket = config.bucket or ""
        if lock is not None and lock.enabled:
            self._lock: SymbolLock | None = SymbolLock(
                lock_dir=lock.lock_dir, timeout_s=lock.timeout_s,
                poll_interval_s=lock.poll_interval_s,
            )
        else:
            self._lock = None

    def _build_uri(self, bucket: str) -> str:
        uri = (
            f"{self._scheme}://{self._host}:{bucket}"
            f"?access={self._config.access_key}&secret={self._config.secret_key}"
        )
        if self._port:
            uri += f"&port={self._port}"
        return uri

    def _get_arctic(self, bucket: str) -> adb.Arctic:
        if bucket in self._arctic_cache:
            return self._arctic_cache[bucket]
        # LMDB mode: single Arctic instance, library name = bucket
        if self._is_lmdb:
            return self._arctic_cache["__lmdb__"]
        try:
            uri = self._build_uri(bucket)
            arctic = adb.Arctic(uri)
        except Exception as exc:
            raise StorageError(
                f"Failed to connect to ArcticDB bucket {bucket} at {self._config.endpoint}"
            ) from exc
        self._arctic_cache[bucket] = arctic
        return arctic

    @classmethod
    def from_lmdb(
        cls, path: str, lock: LockConfig | None = None
    ) -> ArcticStorage:
        """Create an ArcticStorage backed by LMDB (for testing).

        LMDB is single-process, so locking is off by default; pass *lock* to
        enable it (e.g. for tests of the concurrent-access-protection path).
        """
        storage = cls.__new__(cls)
        storage._config = MinioConfig(endpoint="lmdb", access_key="", secret_key="")
        storage._scheme = "lmdb"
        storage._host = ""
        storage._port = ""
        storage._arctic_cache = {}
        storage._library_cache = {}
        storage._is_lmdb = True
        arctic = adb.Arctic(f"lmdb://{path}")
        storage._arctic_cache["__lmdb__"] = arctic
        if lock is not None and lock.enabled:
            storage._lock = SymbolLock(
                lock_dir=lock.lock_dir, timeout_s=lock.timeout_s,
                poll_interval_s=lock.poll_interval_s,
            )
        else:
            storage._lock = None
        return storage

    def lock_symbol(self, library: str, symbol: str) -> ContextManager[None]:
        """Return a context manager that acquires a per-(library, symbol) advisory lock.

        Returns a nullcontext when locking is disabled, so callers can always
        wrap their mutating region in ``with self.lock_symbol(...):`` regardless
        of whether locking is configured.
        """
        if self._lock is None:
            return contextlib.nullcontext()
        # ``SymbolLock.acquire`` returns a ``_HeldLock`` (whose ``__enter__``
        # yields the held lock object, not ``None``); callers only use it as a
        # bare ``with`` block, so the broader ``ContextManager[None]`` view is
        # the honest contract here.
        return cast("ContextManager[None]", self._lock.acquire(library, symbol))

    @property
    def arctic(self) -> adb.Arctic:
        """Return the first cached Arctic instance (for test cleanup).

        Prefer using ``arctic_for(bucket)`` in production code.
        """
        if self._arctic_cache:
            return next(iter(self._arctic_cache.values()))
        raise StorageError("No Arctic instances initialised")

    def arctic_for(self, bucket: str) -> adb.Arctic:
        """Return the Arctic instance for a given bucket."""
        return self._get_arctic(bucket)

    def _bucket_request(self, bucket: str, method: str) -> None:
        """Issue an empty-payload SigV4-signed bucket-level request.

        Returns on a 2xx response. :class:`urllib.error.HTTPError` propagates
        so callers can branch on the status code (e.g. 404 = absent bucket);
        transport failures raise :class:`StorageError` with the original cause
        preserved.
        """
        scheme = "https" if self._config.secure else "http"
        url = f"{scheme}://{self._config.endpoint}/{bucket}"

        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        region = "us-east-1"
        service = "s3"
        host = self._config.endpoint

        payload_hash = hashlib.sha256(b"").hexdigest()
        headers: dict[str, str] = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": now,
        }

        canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted(headers.items()))
        signed_headers = ";".join(sorted(headers.keys()))
        canonical_request = (
            f"{method}\n/{bucket}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )

        scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = (
            f"AWS4-HMAC-SHA256\n{now}\n{scope}\n"
            + hashlib.sha256(canonical_request.encode()).hexdigest()
        )

        def _sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        signing_key = _sign(
            _sign(_sign(_sign(("AWS4" + self._config.secret_key).encode(), date_stamp), region), service),
            "aws4_request",
        )
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth = (
            f"AWS4-HMAC-SHA256 Credential={self._config.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        headers["Authorization"] = auth

        req = urllib.request.Request(url, method=method, data=b"", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
            if not 200 <= status < 300:
                raise StorageError(
                    f"Unexpected HTTP {status} for {method} bucket {bucket} "
                    f"at {self._config.endpoint}"
                )
        except urllib.error.HTTPError:
            raise
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(
                f"{method} request for bucket {bucket} at "
                f"{self._config.endpoint} failed: {exc}"
            ) from exc

    def _bucket_exists(self, bucket: str) -> bool:
        """Read-only bucket existence probe via signed HEAD.

        404 means absent. 403 (no permission), transport failures, and any
        other unexpected response raise :class:`StorageError` — lack of
        permission or connectivity is never treated as an empty/absent
        bucket.
        """
        try:
            self._bucket_request(bucket, "HEAD")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise StorageError(
                f"Bucket existence probe for {bucket} at "
                f"{self._config.endpoint} failed: HTTP {exc.code}"
            ) from exc
        return True

    def _ensure_bucket(self, bucket: str) -> None:
        """Create the S3 bucket if it doesn't already exist (MinIO/S3).

        Uses SigV4-signed PUT. Raises :class:`StorageError` on creation
        errors; 409 (BucketAlreadyOwnedByYou) is accepted only when a
        subsequent HEAD confirms the bucket exists and is accessible.
        """
        try:
            self._bucket_request(bucket, "PUT")
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                if self._bucket_exists(bucket):
                    logger.info("storage.bucket_exists", bucket=bucket)
                    return
                raise StorageError(
                    f"Bucket creation returned 409 but {bucket} is not "
                    f"accessible at {self._config.endpoint}"
                ) from exc
            body = ""
            with contextlib.suppress(Exception):
                body = exc.read().decode()[:200]
            raise StorageError(
                f"Failed to create bucket {bucket} at {self._config.endpoint}: "
                f"HTTP {exc.code} {body}"
            ) from exc
        logger.info("storage.bucket_created", bucket=bucket)

    def _library_location(self, name: str) -> tuple[str, str]:
        """Map a logical library name to its ``(bucket, arctic library name)``.

        LMDB uses the shared ``__lmdb__`` Arctic instance with the supplied
        name; single-bucket MinIO uses ``config.bucket`` with the supplied
        name; per-library-bucket mode uses the supplied name as bucket with
        Arctic library ``main``.
        """
        if self._is_lmdb:
            return ("__lmdb__", name)
        if self._single_bucket:
            return (self._single_bucket, name)
        return (name, _S3_LIB_NAME)

    def _get_existing_library(self, name: str) -> adb.Library | None:
        """Return the library handle if it exists, else None. Strictly read-only.

        Uses only a signed HEAD bucket probe plus ``Arctic.list_libraries()``
        — never ``_ensure_bucket``, ``create_library``, or any write method.
        May fill the in-memory handle cache; absence is not cached.
        """
        cached = self._library_cache.get(name)
        if cached is not None:
            return cached

        bucket, lib_name = self._library_location(name)
        if not self._is_lmdb and not self._bucket_exists(bucket):
            return None

        arctic = self._get_arctic(bucket)
        if lib_name not in arctic.list_libraries():
            return None
        lib = arctic[lib_name]
        self._library_cache[name] = lib
        return lib

    def has_library(self, name: str) -> bool:
        """Return True when the library already exists (read-only check)."""
        return self._get_existing_library(name) is not None

    def ensure_library(self, name: str) -> None:
        """Explicitly create the library if missing (the one mutating path)."""
        self._get_or_create_library(name)

    def _get_or_create_library(self, name: str) -> adb.Library:
        cached = self._library_cache.get(name)
        if cached is not None:
            return cached

        bucket, lib_name = self._library_location(name)
        if not self._is_lmdb:
            self._ensure_bucket(bucket)

        arctic = self._get_arctic(bucket)
        if lib_name not in arctic.list_libraries():
            try:
                arctic.create_library(
                    lib_name, library_options=adb.LibraryOptions(dynamic_schema=True)
                )
            except Exception as exc:
                # Concurrent duplicate create_library (two manifest runs
                # racing on the same canonical library): reopen only when a
                # re-list confirms the library now exists; otherwise the
                # failure is real and must propagate.
                if lib_name not in arctic.list_libraries():
                    raise StorageError(
                        f"Failed to create library {name}"
                    ) from exc
                logger.info("storage.library_created_concurrently", library=name)
        lib = arctic[lib_name]
        self._library_cache[name] = lib
        return lib

    def read_with_metadata(
        self,
        library: str,
        symbol: str,
        date_range: tuple[datetime | None, datetime | None] | None = None,
        columns: list[str] | None = None,
    ) -> tuple[pd.DataFrame, object] | None:
        """Read data and metadata for *symbol* from one ``lib.read()`` call.

        Returns ``None`` if the symbol (or library) does not exist. Data and
        metadata come from the **same** version, so callers pairing a chain
        frame with its coverage manifest cannot observe a torn read.

        Parameters
        ----------
        columns : list[str] or None
            If provided, only read the specified columns from storage.
            The index is always returned regardless.

        Returns
        -------
        tuple[pd.DataFrame, object] or None
            ``(data, metadata)`` — metadata is ``None`` when the version
            carries none.
        """
        lib = self._get_existing_library(library)
        if lib is None:
            return None
        try:
            kwargs: dict[str, Any] = {}
            if date_range is not None:
                kwargs["date_range"] = date_range
            if columns is not None:
                kwargs["columns"] = columns
            with _suppress_blockmanager_warning():
                result = lib.read(symbol, **kwargs)
                return (result.data, result.metadata)
        except adb.exceptions.NoSuchVersionException:
            return None
        except Exception as exc:
            raise StorageError(f"Failed to read {library}/{symbol}") from exc

    def read(
        self,
        library: str,
        symbol: str,
        date_range: tuple[datetime | None, datetime | None] | None = None,
        columns: list[str] | None = None,
    ) -> pd.DataFrame | None:
        """Read data for *symbol* from *library*. Returns None if symbol not found.

        Delegates to :meth:`read_with_metadata` and discards the metadata;
        the caller-facing signature and behavior are unchanged.

        Parameters
        ----------
        columns : list[str] or None
            If provided, only read the specified columns from storage.
            The index is always returned regardless.
        """
        result = self.read_with_metadata(library, symbol, date_range, columns)
        if result is None:
            return None
        return result[0]

    def write(
        self,
        library: str,
        symbol: str,
        data: pd.DataFrame,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write (initial creation) data for *symbol*."""
        lib = self._get_or_create_library(library)
        try:
            lib.write(symbol, data, metadata=metadata, prune_previous_versions=True)
        except Exception as exc:
            raise StorageError(f"Failed to write {library}/{symbol}") from exc
        logger.info("storage.write", library=library, symbol=symbol, rows=len(data))

    def update(
        self,
        library: str,
        symbol: str,
        data: pd.DataFrame,
        date_range: tuple[datetime, datetime],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Update existing symbol over *date_range*."""
        lib = self._get_or_create_library(library)
        try:
            lib.update(
                symbol,
                data,
                date_range=date_range,
                metadata=metadata,
                prune_previous_versions=True,
            )
        except Exception as exc:
            raise StorageError(f"Failed to update {library}/{symbol}") from exc
        logger.info("storage.update", library=library, symbol=symbol, rows=len(data))

    def delete_symbol(self, library: str, symbol: str) -> None:
        """Delete all versions of a symbol from the library."""
        lib = self._get_or_create_library(library)
        with self.lock_symbol(library, symbol):
            lib.delete(symbol)
            logger.info("storage.delete", library=library, symbol=symbol)

    def list_symbols(self, library: str) -> list[str]:
        """Return all symbols stored in a library.

        Parameters
        ----------
        library : str
            Library/bucket name (e.g. ``equities-1m-databento``).

        Returns
        -------
        list[str]
            Sorted list of symbol names.
        """
        lib = self._get_existing_library(library)
        if lib is None:
            return []
        return lib.list_symbols()  # type: ignore[no-any-return]

    def has_symbol(self, library: str, symbol: str) -> bool:
        """Check whether a symbol exists in a library.

        Parameters
        ----------
        library : str
            Library/bucket name.
        symbol : str
            Symbol ticker (e.g. ``AAPL``).

        Returns
        -------
        bool
            True if the symbol has stored data in the library.
        """
        lib = self._get_existing_library(library)
        if lib is None:
            return False
        try:
            with _suppress_blockmanager_warning():
                lib.read(symbol)
            return True
        except adb.exceptions.NoSuchVersionException:
            return False

    def get_symbol_size(self, library: str, symbol: str) -> int:
        """Return compressed storage size in bytes for a symbol.

        Returns 0 if the symbol does not exist.
        """
        lib = self._get_existing_library(library)
        if lib is None:
            return 0
        try:
            sizes = lib.admin_tools().get_sizes_for_symbol(symbol)
            total = sum_sizes(sizes.values())
            return int(total.bytes_compressed)
        except Exception:
            return 0

    def get_segment_count(self, library: str, symbol: str) -> int:
        """Return the number of table-data segments for a symbol.

        Returns 0 if the symbol does not exist or segment metadata is unavailable.
        """
        lib = self._get_existing_library(library)
        if lib is None:
            return 0
        try:
            sizes = lib.admin_tools().get_sizes_for_symbol(symbol)
            table_data = sizes.get(adb.KeyType.TABLE_DATA)
            if table_data is None:
                return 0
            return int(table_data.count)
        except Exception:
            return 0

    def get_library_size(self, library: str) -> int:
        """Return the total compressed storage size in bytes for an entire library.

        Uses a single ``admin_tools().get_sizes()`` scan rather than summing
        per-symbol sizes, which is dramatically cheaper over network backends
        (one round-trip vs N) and avoids double-counting library-global key
        types such as ``VERSION_REF``.

        Returns 0 if the library is empty or metadata is unavailable.
        """
        lib = self._get_existing_library(library)
        if lib is None:
            return 0
        try:
            sizes = lib.admin_tools().get_sizes()
            total = sum_sizes(sizes.values())
            return int(total.bytes_compressed)
        except Exception:
            return 0

    def is_symbol_fragmented(self, library: str, symbol: str) -> bool:
        """Return whether ArcticDB considers a symbol fragmented.

        Returns False if the symbol does not exist or fragmentation metadata is unavailable.
        """
        lib = self._get_existing_library(library)
        if lib is None:
            return False
        try:
            return bool(lib.is_symbol_fragmented(symbol))
        except Exception:
            return False

    def defragment_symbol(
        self,
        library: str,
        symbol: str,
        *,
        prune_previous_versions: bool = True,
        segment_size: int | None = None,
    ) -> None:
        """Compact data segments for a single symbol.

        No-ops when ArcticDB reports that the symbol has nothing to compact.
        """
        with self.lock_symbol(library, symbol):
            if not self.is_symbol_fragmented(library, symbol):
                return

            lib = self._get_or_create_library(library)
            try:
                lib.defragment_symbol_data(
                    symbol,
                    segment_size=segment_size,
                    prune_previous_versions=prune_previous_versions,
                )
            except Exception as exc:
                raise StorageError(f"Failed to defragment {library}/{symbol}") from exc
