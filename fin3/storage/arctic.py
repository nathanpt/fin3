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


class ArcticStorage:
    """Thin wrapper around ArcticDB with library management."""

    def __init__(self, config: MinioConfig) -> None:
        try:
            if config.secure:
                uri = f"s3s://{config.endpoint}"
            else:
                uri = f"http://{config.endpoint}"
            self._arctic = adb.Arctic(uri)
        except Exception as exc:
            raise StorageError(
                f"Failed to connect to ArcticDB at {config.endpoint}"
            ) from exc
        self._config = config
        self._library_cache: dict[str, adb.Library] = {}

    @classmethod
    def from_lmdb(cls, path: str) -> ArcticStorage:
        """Create an ArcticStorage backed by LMDB (for testing)."""
        storage = cls.__new__(cls)
        storage._config = MinioConfig(endpoint="lmdb", access_key="", secret_key="")
        storage._arctic = adb.Arctic(f"lmdb://{path}")
        storage._library_cache = {}
        return storage

    @property
    def arctic(self) -> adb.Arctic:
        return self._arctic

    def _get_or_create_library(self, name: str) -> adb.Library:
        if name in self._library_cache:
            return self._library_cache[name]
        if name not in self._arctic.list_libraries():
            self._arctic.create_library(
                name, library_options=adb.LibraryOptions(dynamic_schema=True)
            )
        lib = self._arctic[name]
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
