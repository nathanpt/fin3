"""Tests for configuration loading."""

from typing import Any

import pytest
from pydantic import ValidationError

from fin3.config.settings import ClientConfig, DatabentoConfig, MinioConfig


class TestClientConfig:
    def test_minio_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            ClientConfig()

    def test_minio_from_env(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("FIN3_MINIO__ENDPOINT", "localhost:9000")
        monkeypatch.setenv("FIN3_MINIO__ACCESS_KEY", "minioadmin")
        monkeypatch.setenv("FIN3_MINIO__SECRET_KEY", "minioadmin")
        config = ClientConfig()
        assert config.minio.endpoint == "localhost:9000"
        assert config.minio.access_key == "minioadmin"
        assert config.minio.secure is False

    def test_provider_discriminated_union(self) -> None:
        config = ClientConfig(
            minio=MinioConfig(
                endpoint="localhost:9000", access_key="a", secret_key="b"
            ),
            providers={
                "databento": DatabentoConfig(api_key="PLACEHOLDER-test-key"),
            },
        )
        assert isinstance(config.providers["databento"], DatabentoConfig)
        assert config.providers["databento"].api_key == "PLACEHOLDER-test-key"

    def test_log_level_default(self) -> None:
        config = ClientConfig(
            minio=MinioConfig(
                endpoint="localhost:9000", access_key="a", secret_key="b"
            ),
        )
        assert config.log_level == "INFO"
        assert config.log_format == "json"

    def test_empty_providers_default(self) -> None:
        config = ClientConfig(
            minio=MinioConfig(
                endpoint="localhost:9000", access_key="a", secret_key="b"
            ),
        )
        assert config.providers == {}
