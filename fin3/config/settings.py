"""Centralised Pydantic settings with .env file support."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MinioConfig(BaseModel):
    endpoint: str
    access_key: str
    secret_key: str
    secure: bool = False
    bucket: str = ""
    """When set, all libraries are stored inside this single bucket
    (library name used as ArcticDB library name). When empty, each
    library maps to its own S3 bucket."""


class DatabentoConfig(BaseModel):
    provider_type: Literal["databento"] = "databento"
    api_key: str
    dataset: str = "XNAS.ITCH"
    max_retries: int = 3
    initial_backoff: float = 1.0
    max_backoff: float = 30.0


class PolygonConfig(BaseModel):
    provider_type: Literal["polygon"] = "polygon"
    api_key: str


class BinanceConfig(BaseModel):
    provider_type: Literal["binance"] = "binance"
    api_key: str
    api_secret: str = ""


ProviderConfig = Annotated[
    DatabentoConfig | PolygonConfig | BinanceConfig,
    Field(discriminator="provider_type"),
]


class LockConfig(BaseModel):
    """Configuration for per-symbol cross-process file locking.

    Locks guard writes in ``get_data()`` so that two processes cannot
    simultaneously fetch and store the same symbol, which would otherwise
    corrupt the underlying ArcticDB library. Set ``enabled`` to ``False`` to
    disable locking entirely (no lock files are written).
    """

    enabled: bool = True
    lock_dir: str = "/tmp/fin3/locks"
    timeout_s: float = 600.0
    poll_interval_s: float = 0.5


class ClientConfig(BaseSettings):
    minio: MinioConfig
    providers: dict[str, ProviderConfig] = {}
    log_level: str = "INFO"
    log_format: str = "json"
    lock: LockConfig = Field(default_factory=LockConfig)

    model_config = SettingsConfigDict(
        env_prefix="FIN3_",
        env_file=".env",
        env_nested_delimiter="__",
    )


