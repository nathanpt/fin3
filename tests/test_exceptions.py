"""Tests for the exception hierarchy."""

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


class TestExceptionHierarchy:
    def test_all_inherit_from_fin3_error(self) -> None:
        for exc_cls in (
            ConfigurationError,
            ProviderError,
            ProviderTimeoutError,
            ProviderRateLimitError,
            StorageError,
            DataValidationError,
            SchemaValidationError,
            BoundaryMismatchError,
        ):
            assert issubclass(exc_cls, Fin3Error)

    def test_provider_timeout_inherits_provider_error(self) -> None:
        assert issubclass(ProviderTimeoutError, ProviderError)

    def test_provider_rate_limit_inherits_provider_error(self) -> None:
        assert issubclass(ProviderRateLimitError, ProviderError)

    def test_schema_validation_inherits_data_validation(self) -> None:
        assert issubclass(SchemaValidationError, DataValidationError)

    def test_boundary_mismatch_inherits_data_validation(self) -> None:
        assert issubclass(BoundaryMismatchError, DataValidationError)

    def test_boundary_mismatch_carries_bounds(self) -> None:
        exc = BoundaryMismatchError(
            "mismatch",
            expected_start=1,
            expected_end=2,
            actual_start=3,
            actual_end=4,
        )
        assert exc.expected_start == 1
        assert exc.expected_end == 2
        assert exc.actual_start == 3
        assert exc.actual_end == 4

    def test_catch_all_with_fin3_error(self) -> None:
        for exc_cls in (
            ConfigurationError,
            ProviderError,
            StorageError,
            DataValidationError,
        ):
            try:
                raise exc_cls("test")
            except Fin3Error:
                pass
            else:
                raise AssertionError(f"{exc_cls.__name__} not caught by Fin3Error")
