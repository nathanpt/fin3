"""Tests for ProviderRegistry and DatabentoProvider._normalise."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from fin3.config.settings import DatabentoConfig
from fin3.exceptions import ConfigurationError
from fin3.providers import ProviderRegistry
from fin3.providers.databento import _normalise


class TestProviderRegistry:
    def test_register_and_get_provider(self) -> None:
        """Register a provider and retrieve it by name."""
        # DatabentoProvider is already registered via decorator
        config = DatabentoConfig(api_key="test_key")
        registry = ProviderRegistry({"databento": config})
        provider = registry.get("databento")
        assert provider is not None

    def test_get_unknown_provider_raises(self) -> None:
        """Requesting a provider not in configs raises ConfigurationError."""
        config = DatabentoConfig(api_key="test_key")
        registry = ProviderRegistry({"databento": config})
        with pytest.raises(ConfigurationError, match="not configured"):
            registry.get("polygon")

    def test_unknown_provider_name_at_init_raises(self) -> None:
        """Passing an unregistered provider name at init raises ConfigurationError."""
        with pytest.raises(ConfigurationError, match="Unknown provider"):
            ProviderRegistry({"nonexistent": MagicMock()})


class TestNormalise:
    def test_normalise_with_ts_event_index(self) -> None:
        df = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [500]},
            index=pd.DatetimeIndex(["2024-01-02 09:30"], tz="UTC"),
        )
        df.index.name = "ts_event"
        result = _normalise(df)
        assert list(result.columns) == ["open", "high", "low", "close", "volume"]
        assert result.index.name is None

    def test_normalise_tz_naive_gets_localized(self) -> None:
        df = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [500]},
            index=pd.DatetimeIndex(["2024-01-02 09:30"]),
        )
        result = _normalise(df)
        assert result.index.tz is not None
        assert str(result.index.tz) == "UTC"
