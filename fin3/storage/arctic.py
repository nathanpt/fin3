"""ArcticDB + MinIO (or LMDB) storage adapter."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import arcticdb as adb
import pandas as pd
import structlog

from fin3.config.settings import MinioConfig
from fin3.exceptions import StorageError

logger = structlog.get_logger(__name__)

_S3_LIB_NAME = "main"


class ArcticStorage:
    """Thin wrapper around ArcticDB with per-library bucket management.

    Each library maps to its own S3 bucket (e.g. ``equities-1m-databento``).
    Inside each bucket a single ArcticDB library named ``main`` is used.
    """

    def __init__(self, config: MinioConfig) -> None:
        self._config = config
        self._scheme = "s3s" if config.secure else "s3"
        self._host, _, self._port = config.endpoint.partition(":")
        self._arctic_cache: dict[str, adb.Arctic] = {}
        self._library_cache: dict[str, adb.Library] = {}
        self._is_lmdb = False
        # When bucket is set, all libraries live inside this single bucket
        # using the library name as the ArcticDB library name.
        self._single_bucket = config.bucket or ""

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
    def from_lmdb(cls, path: str) -> ArcticStorage:
        """Create an ArcticStorage backed by LMDB (for testing)."""
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
        return storage

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

    def _ensure_bucket(self, bucket: str) -> None:
        """Create the S3 bucket if it doesn't already exist (MinIO/S3).

        Uses SigV4-signed PUT to create the bucket. Silently succeeds
        if the bucket already exists.
        """
        import hashlib
        import hmac
        import urllib.request
        import urllib.error
        from datetime import datetime as _dt

        scheme = "https" if self._config.secure else "http"
        url = f"{scheme}://{self._config.endpoint}/{bucket}"

        now = _dt.utcnow().strftime("%Y%m%dT%H%M%SZ")
        date_stamp = _dt.utcnow().strftime("%Y%m%d")
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
            f"PUT\n/{bucket}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
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

        req = urllib.request.Request(url, method="PUT", data=b"", headers=headers)
        try:
            urllib.request.urlopen(req, timeout=10)
            logger.info("storage.bucket_created", bucket=bucket)
        except urllib.error.HTTPError as e:
            # 409 = BucketAlreadyOwnedByYou, 200 = success — anything else is unexpected
            if e.code not in (200, 409):
                body = e.read().decode()
                logger.warning(
                    "storage.bucket_create_failed",
                    bucket=bucket,
                    code=e.code,
                    body=body[:200],
                )
        except Exception as exc:
            logger.warning("storage.bucket_create_failed", bucket=bucket, error=str(exc))

    def _get_or_create_library(self, name: str) -> adb.Library:
        if name in self._library_cache:
            return self._library_cache[name]

        # Determine which bucket and library name to use
        if self._is_lmdb:
            # LMDB: single Arctic, library name = name
            bucket = "__lmdb__"
            lib_name = name
        elif self._single_bucket:
            # Single-bucket mode: one bucket, library name = name
            bucket = self._single_bucket
            lib_name = name
            self._ensure_bucket(bucket)
        else:
            # Per-library bucket mode: bucket = name, library name = "main"
            bucket = name
            lib_name = _S3_LIB_NAME
            self._ensure_bucket(bucket)

        arctic = self._get_arctic(bucket)
        if lib_name not in arctic.list_libraries():
            arctic.create_library(
                lib_name, library_options=adb.LibraryOptions(dynamic_schema=True)
            )
        lib = arctic[lib_name]
        self._library_cache[name] = lib
        return lib

    def read(
        self,
        library: str,
        symbol: str,
        date_range: tuple[datetime | None, datetime | None] | None = None,
    ) -> pd.DataFrame | None:
        """Read data for *symbol* from *library*. Returns None if symbol not found."""
        lib = self._get_or_create_library(library)
        try:
            kwargs: dict[str, Any] = {}
            if date_range is not None:
                kwargs["date_range"] = date_range
            result = lib.read(symbol, **kwargs)
            return result.data  # type: ignore[no-any-return]
        except adb.exceptions.NoSuchVersionException:
            return None
        except Exception as exc:
            raise StorageError(f"Failed to read {library}/{symbol}") from exc

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

    def list_symbols(self, library: str) -> list[str]:
        lib = self._get_or_create_library(library)
        return lib.list_symbols()  # type: ignore[no-any-return]

    def has_symbol(self, library: str, symbol: str) -> bool:
        lib = self._get_or_create_library(library)
        try:
            lib.read(symbol)
            return True
        except adb.exceptions.NoSuchVersionException:
            return False
