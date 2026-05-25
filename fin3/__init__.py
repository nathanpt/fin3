"""fin3 — Declarative financial time-series data library."""

from fin3.config.settings import ClientConfig
from fin3.core import MarketDataFetcher
from fin3.exceptions import (
    BoundaryMismatchError,
    ConfigurationError,
    DataValidationError,
    Fin3Error,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    SchemaValidationError,
    StorageError,
)
from fin3.schemas import AssetType, Resolution

__all__ = [
    "AssetType",
    "BoundaryMismatchError",
    "ClientConfig",
    "ConfigurationError",
    "DataValidationError",
    "Fin3Error",
    "MarketDataFetcher",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "Resolution",
    "SchemaValidationError",
    "StorageError",
]
