"""Shared fixtures for integration tests.

Requires environment variables (set via .env or export):
  FIN3_MINIO__ENDPOINT      — e.g. "localhost:9000"
  FIN3_MINIO__ACCESS_KEY
  FIN3_MINIO__SECRET_KEY
  FIN3_PROVIDERS__DATABENTO__API_KEY   — Databento API key (db-...)

Run with:
  uv run pytest tests/integration/ -m integration -v
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from dotenv import load_dotenv

# Load .env from project root before importing fin3 (which reads env vars)
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

import pytest  # noqa: E402

from fin3.config.settings import ClientConfig, DatabentoConfig, MinioConfig  # noqa: E402
from fin3.core import MarketDataFetcher  # noqa: E402
from fin3.providers.databento import DatabentoProvider  # noqa: E402
from fin3.storage.arctic import ArcticStorage  # noqa: E402


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ---------------------------------------------------------------------------
# Skip entire module when integration deps are missing
# ---------------------------------------------------------------------------

def _has_integration_env() -> bool:
    return bool(
        _env("FIN3_MINIO__ENDPOINT")
        and _env("FIN3_MINIO__ACCESS_KEY")
        and _env("FIN3_MINIO__SECRET_KEY")
        and _env("FIN3_PROVIDERS__DATABENTO__API_KEY")
    )


pytestmark = pytest.mark.integration

if not _has_integration_env():
    collect_ignore_glob = ["*.py"]


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def minio_config() -> MinioConfig:
    return MinioConfig(
        endpoint=_env("FIN3_MINIO__ENDPOINT"),
        access_key=_env("FIN3_MINIO__ACCESS_KEY"),
        secret_key=_env("FIN3_MINIO__SECRET_KEY"),
        secure=_env("FIN3_MINIO__SECURE", "false").lower() == "true",
    )


@pytest.fixture(scope="session")
def databento_config() -> DatabentoConfig:
    return DatabentoConfig(
        api_key=_env("FIN3_PROVIDERS__DATABENTO__API_KEY"),
        dataset=_env("FIN3_PROVIDERS__DATABENTO__DATASET", "XNAS.ITCH"),
    )


@pytest.fixture(scope="session")
def client_config(minio_config: MinioConfig, databento_config: DatabentoConfig) -> ClientConfig:
    return ClientConfig(
        minio=minio_config,
        providers={"databento": databento_config},
    )


# ---------------------------------------------------------------------------
# Infrastructure fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def minio_storage(minio_config: MinioConfig) -> ArcticStorage:
    return ArcticStorage(minio_config)


@pytest.fixture(scope="session")
def databento_provider(databento_config: DatabentoConfig) -> DatabentoProvider:
    return DatabentoProvider(databento_config)


@pytest.fixture(scope="session")
def fetcher(client_config: ClientConfig) -> MarketDataFetcher:
    return MarketDataFetcher(client_config)


# ---------------------------------------------------------------------------
# Unique library name helper — avoids collisions between test runs
# ---------------------------------------------------------------------------

_lib_counter = 0


@pytest.fixture()
def unique_library(minio_storage: ArcticStorage) -> Generator[str, None, None]:
    """Return a unique library name and clean it up after the test."""
    global _lib_counter
    _lib_counter += 1
    name = f"test-integration-{_lib_counter}"
    yield name
    # cleanup
    try:
        if name in minio_storage.arctic.list_libraries():
            minio_storage.arctic.delete_library(name)
            minio_storage._library_cache.pop(name, None)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Common test parameters
# ---------------------------------------------------------------------------

RECENT_TRADING_DAY = "2024-06-03"

RANGE_1M = (
    datetime(2024, 6, 3, 9, 30, tzinfo=timezone.utc),
    datetime(2024, 6, 3, 9, 35, tzinfo=timezone.utc),
)
RANGE_1H = (
    datetime(2024, 6, 3, 14, 0, tzinfo=timezone.utc),
    datetime(2024, 6, 3, 15, 0, tzinfo=timezone.utc),
)
RANGE_1D = (
    datetime(2024, 6, 3, 0, 0, tzinfo=timezone.utc),
    datetime(2024, 6, 4, 0, 0, tzinfo=timezone.utc),
)

SYMBOL_EQUITY = "AAPL"
