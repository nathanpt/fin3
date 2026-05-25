"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fin3.config.settings import (
    ClientConfig,
    DatabentoConfig,
    MinioConfig,
)
from fin3.schemas import empty_ohlcv
from fin3.storage.arctic import ArcticStorage


@pytest.fixture
def lmdb_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_lmdb")


@pytest.fixture
def storage(lmdb_path: str) -> ArcticStorage:
    return ArcticStorage.from_lmdb(lmdb_path)


@pytest.fixture
def minio_config() -> MinioConfig:
    return MinioConfig(endpoint="localhost:9000", access_key="test", secret_key="test")


@pytest.fixture
def client_config(minio_config: MinioConfig) -> ClientConfig:
    return ClientConfig(
        minio=minio_config,
        providers={
            "databento": DatabentoConfig(api_key="test_key"),
        },
    )


def make_ohlcv(
    start: str,
    periods: int,
    freq: str = "1min",
    tz: str = "UTC",
    base_price: float = 100.0,
    base_volume: float = 1000.0,
) -> pd.DataFrame:
    """Helper to create synthetic OHLCV DataFrames for tests."""
    index = pd.date_range(start, periods=periods, freq=freq, tz=tz)
    data = pd.DataFrame(
        {
            "open": [base_price + i * 0.1 for i in range(periods)],
            "high": [base_price + i * 0.1 + 0.5 for i in range(periods)],
            "low": [base_price + i * 0.1 - 0.5 for i in range(periods)],
            "close": [base_price + i * 0.1 + 0.2 for i in range(periods)],
            "volume": [base_volume for _ in range(periods)],
        },
        index=index,
    )
    return data


def make_empty_ohlcv() -> pd.DataFrame:
    return empty_ohlcv()
