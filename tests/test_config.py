"""Tests for configuration loading."""

import os

import pytest
from pydantic import ValidationError

from fin3.config.settings import (
    BinanceConfig,
    ClientConfig,
    DatabentoConfig,
    MassiveConfig,
    MinioConfig,
    YahooConfig,
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


class TestConfigEnvErrorPaths:
    """Malformed env input must raise ValidationError, not pass silently."""

    @staticmethod
    def _set_minio_env(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FIN3_MINIO__ENDPOINT", "localhost:9000")
        monkeypatch.setenv("FIN3_MINIO__ACCESS_KEY", "minioadmin")
        monkeypatch.setenv("FIN3_MINIO__SECRET_KEY", "minioadmin")

    def test_unknown_provider_type_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``provider_type`` outside the discriminated union is an error."""
        self._set_minio_env(monkeypatch)
        monkeypatch.setenv("FIN3_PROVIDERS__X__PROVIDER_TYPE", "unknown")
        with pytest.raises(ValidationError, match="union_tag_invalid"):
            ClientConfig()  # type: ignore[call-arg]

    def test_invalid_bool_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set_minio_env(monkeypatch)
        monkeypatch.setenv("FIN3_MINIO__SECURE", "notabool")
        with pytest.raises(ValidationError, match="bool_parsing"):
            ClientConfig()  # type: ignore[call-arg]


class TestProviderUnionFromUntypedEnv:
    """The discriminated union parsing REAL env input (dict of strings).

    Unlike ``test_provider_discriminated_union`` above — which feeds
    pre-built models — these tests start from raw string env vars, the
    way pydantic-settings actually sees them.
    """

    def test_parses_multiple_provider_types(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FIN3_MINIO__ENDPOINT", "localhost:9000")
        monkeypatch.setenv("FIN3_MINIO__ACCESS_KEY", "minioadmin")
        monkeypatch.setenv("FIN3_MINIO__SECRET_KEY", "minioadmin")
        # databento: required field plus string -> int / string -> float coercion.
        monkeypatch.setenv("FIN3_PROVIDERS__DATABENTO__PROVIDER_TYPE", "databento")
        monkeypatch.setenv("FIN3_PROVIDERS__DATABENTO__API_KEY", "k1")
        monkeypatch.setenv("FIN3_PROVIDERS__DATABENTO__MAX_RETRIES", "9")
        monkeypatch.setenv("FIN3_PROVIDERS__DATABENTO__INITIAL_BACKOFF", "0.25")
        # binance: every field optional; defaults must survive env parsing.
        monkeypatch.setenv("FIN3_PROVIDERS__BINANCE__PROVIDER_TYPE", "binance")
        # yahoo: discriminator-only entry; all defaults.
        monkeypatch.setenv("FIN3_PROVIDERS__YAHOO__PROVIDER_TYPE", "yahoo")

        config = ClientConfig()  # type: ignore[call-arg]

        databento = config.providers["databento"]
        assert type(databento) is DatabentoConfig
        assert databento.api_key == "k1"
        assert databento.max_retries == 9
        assert isinstance(databento.max_retries, int)
        assert databento.initial_backoff == 0.25
        assert isinstance(databento.initial_backoff, float)

        binance = config.providers["binance"]
        assert type(binance) is BinanceConfig
        assert binance.api_key == ""
        assert binance.timeout == 30.0

        yahoo = config.providers["yahoo"]
        assert type(yahoo) is YahooConfig
        assert yahoo.provider_type == "yahoo"
        assert yahoo.auto_adjust is False
