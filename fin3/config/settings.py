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


class ClientConfig(BaseSettings):
    minio: MinioConfig
    providers: dict[str, ProviderConfig] = {}
    log_level: str = "INFO"
    log_format: str = "json"

    model_config = SettingsConfigDict(
        env_prefix="FIN3_",
        env_file=".env",
        env_nested_delimiter="__",
    )


MAX_RETRIES: int = 3
INITIAL_BACKOFF_SECONDS: float = 1.0
MAX_BACKOFF_SECONDS: float = 30.0
