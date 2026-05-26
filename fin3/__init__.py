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
from fin3.inspect import LibraryOverview, SymbolProfile, inspect_library
from fin3.schemas import AssetType, Resolution
from fin3.utils.integrity import IntegrityIssue, IntegrityReport, check_integrity

__all__ = [
    "AssetType",
    "BoundaryMismatchError",
    "ClientConfig",
    "ConfigurationError",
    "DataValidationError",
    "Fin3Error",
    "IntegrityIssue",
    "IntegrityReport",
    "LibraryOverview",
    "MarketDataFetcher",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "Resolution",
    "SchemaValidationError",
    "StorageError",
    "SymbolProfile",
    "check_integrity",
    "inspect_library",
]
