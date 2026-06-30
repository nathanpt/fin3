"""Tests for configuration loading."""

import os

import pytest
from pydantic import ValidationError

from fin3.config.settings import (
    ClientConfig,
    DatabentoConfig,
    MassiveConfig,
    MinioConfig,
)


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
                "massive": MassiveConfig(api_key="PLACEHOLDER-test-key"),
            },
        )
        assert isinstance(config.providers["databento"], DatabentoConfig)
        assert config.providers["databento"].api_key == "PLACEHOLDER-test-key"
        assert isinstance(config.providers["massive"], MassiveConfig)
        assert config.providers["massive"].provider_type == "massive"
        assert config.providers["massive"].api_key == "PLACEHOLDER-test-key"

    def test_massive_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FIN3_MINIO__ENDPOINT", "localhost:9000")
        monkeypatch.setenv("FIN3_MINIO__ACCESS_KEY", "minioadmin")
        monkeypatch.setenv("FIN3_MINIO__SECRET_KEY", "minioadmin")
        monkeypatch.setenv(
            "FIN3_PROVIDERS__MASSIVE__PROVIDER_TYPE", "massive"
        )
        monkeypatch.setenv("FIN3_PROVIDERS__MASSIVE__API_KEY", "env-key")
        monkeypatch.setenv("FIN3_PROVIDERS__MASSIVE__ADJUSTED", "true")
        config = ClientConfig()  # type: ignore[call-arg]
        assert isinstance(config.providers["massive"], MassiveConfig)
        assert config.providers["massive"].api_key == "env-key"
        assert config.providers["massive"].adjusted is True
        assert config.providers["massive"].base_url == "https://api.massive.com"
        assert config.providers["massive"].request_limit == 50000

    def test_log_level_default(self) -> None:
        config = ClientConfig(
            minio=MinioConfig(
                endpoint="localhost:9000", access_key="a", secret_key="b"
            ),
        )
        assert config.log_level == "INFO"
        assert config.log_format == "json"

    def test_lock_defaults(self) -> None:
        config = ClientConfig(
            minio=MinioConfig(
                endpoint="localhost:9000", access_key="a", secret_key="b"
            ),
        )
        assert config.lock.enabled is True
        assert config.lock.timeout_s == 600.0
        assert config.lock.poll_interval_s == 0.5
        assert config.lock.lock_dir == "/tmp/fin3/locks"

    def test_lock_config_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FIN3_MINIO__ENDPOINT", "localhost:9000")
        monkeypatch.setenv("FIN3_MINIO__ACCESS_KEY", "minioadmin")
        monkeypatch.setenv("FIN3_MINIO__SECRET_KEY", "minioadmin")
        monkeypatch.setenv("FIN3_LOCK__TIMEOUT_S", "120")
        monkeypatch.setenv("FIN3_LOCK__ENABLED", "false")
        config = ClientConfig()  # type: ignore[call-arg]
        assert config.lock.timeout_s == 120.0
        assert config.lock.enabled is False

    def test_lock_default_is_independent_per_instance(self) -> None:
        config_a = ClientConfig(
            minio=MinioConfig(
                endpoint="localhost:9000", access_key="a", secret_key="b"
            ),
        )
        config_b = ClientConfig(
            minio=MinioConfig(
                endpoint="localhost:9000", access_key="a", secret_key="b"
            ),
        )
        # Mutating one instance must not leak into the other; this guards
        # against a shared mutable default (the ``= LockConfig()`` bug).
        assert config_a.lock is not config_b.lock
        config_a.lock.timeout_s = 1.0
        assert config_b.lock.timeout_s == 600.0

    def test_empty_providers_default(self) -> None:
        config = ClientConfig(
            minio=MinioConfig(
                endpoint="localhost:9000", access_key="a", secret_key="b"
            ),
        )
        assert config.providers == {}
