"""fin3 exception hierarchy.

All domain exceptions inherit from Fin3Error so consumers can catch
all fin3 errors with a single ``except Fin3Error`` handler.
"""


class Fin3Error(Exception):
    """Base exception for all fin3 errors."""


class ConfigurationError(Fin3Error):
    """Missing or invalid configuration (API key, unknown provider, etc.)."""


class ProviderError(Fin3Error):
    """Generic provider failure (network error, unexpected response)."""


class ProviderTimeoutError(ProviderError):
    """Provider request exceeded timeout."""


class ProviderRateLimitError(ProviderError):
    """Provider returned 429 or equivalent rate-limit response."""


class StorageError(Fin3Error):
    """ArcticDB / MinIO connectivity or write failure."""


class DataValidationError(Fin3Error):
    """Data failed validation rules."""


class SchemaValidationError(DataValidationError):
    """Structural validation failure (duplicates, wrong columns, bad types)."""


class BoundaryMismatchError(DataValidationError):
    """Reindexed data index does not match expected gap range."""

    def __init__(
        self,
        message: str,
        expected_start: object | None = None,
        expected_end: object | None = None,
        actual_start: object | None = None,
        actual_end: object | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_start = expected_start
        self.expected_end = expected_end
        self.actual_start = actual_start
        self.actual_end = actual_end
