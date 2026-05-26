"""Tests for configuration loading."""

import os

import pytest
from pydantic import ValidationError

from fin3.config.settings import ClientConfig, DatabentoConfig, MinioConfig


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent unit tests from reading the project .env file.

    pydantic-settings reads both .env and env vars. The integration conftest
    calls load_dotenv() at module level, polluting os.environ. We clear all
    FIN3_ env vars so unit tests don't pick up real credentials.
    """
    original = ClientConfig.model_config.copy()
    monkeypatch.setattr(ClientConfig, "model_config", {**original, "env_file": None})
    for key in list(os.environ):
        if key.startswith("FIN3_"):
            monkeypatch.delenv(key, raising=False)


class TestClientConfig:
    def test_minio_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            ClientConfig()  # type: ignore[call-arg]

    def test_minio_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FIN3_MINIO__ENDPOINT", "localhost:9000")
        monkeypatch.setenv("FIN3_MINIO__ACCESS_KEY", "minioadmin")
        monkeypatch.setenv("FIN3_MINIO__SECRET_KEY", "minioadmin")
        config = ClientConfig()  # type: ignore[call-arg]
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
