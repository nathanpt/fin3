"""Tests for configuration loading."""

import pytest
from pydantic import SecretStr, ValidationError

from fin3.config.settings import (
    BinanceConfig,
    CboeConfig,
    ClientConfig,
    DatabentoConfig,
    EodhdOptionsConfig,
    MassiveConfig,
    MinioConfig,
    YahooConfig,
)


class TestEodhdOptionsConfig:
    def test_defaults(self) -> None:
        config = EodhdOptionsConfig()
        assert config.provider_type == "eodhd_options"
        assert config.api_key is None
        assert config.base_url == "https://eodhd.com/api/mp/unicornbay/options"
        assert config.timeout == 30.0
        assert config.max_requests() == 200
        assert config.request_pacing_s == 0.15
        assert config.page_size == 1000

    def test_env_parsing_for_cap_and_page_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FIN3_PROVIDERS__EODHD_OPTIONS__PROVIDER_TYPE", "eodhd_options")
        monkeypatch.setenv("FIN3_PROVIDERS__EODHD_OPTIONS__PER_RUN_MAX_REQUESTS", "300")
        monkeypatch.setenv("FIN3_PROVIDERS__EODHD_OPTIONS__PAGE_SIZE", "500")
        config = ClientConfig(
            minio=MinioConfig(endpoint="e", access_key="a", secret_key="s"),
            _env_file=None,
        )
        provider_config = config.providers["eodhd_options"]
        assert isinstance(provider_config, EodhdOptionsConfig)
        assert provider_config.per_run_max_requests == 300
        assert provider_config.page_size == 500

    def test_bool_and_fractional_integers_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValidationError):
            EodhdOptionsConfig(per_run_max_requests=True)
        with pytest.raises(ValidationError):
            EodhdOptionsConfig(page_size=10.5)
        with pytest.raises(ValidationError):
            EodhdOptionsConfig(per_run_max_requests="200.5")

    def test_page_size_bounds(self) -> None:
        assert EodhdOptionsConfig(page_size=1).page_size == 1
        with pytest.raises(ValidationError):
            EodhdOptionsConfig(page_size=1001)
        with pytest.raises(ValidationError):
            EodhdOptionsConfig(page_size=0)

    def test_base_url_validation(self) -> None:
        assert EodhdOptionsConfig(
            base_url="https://proxy.example.com/api/mp/unicornbay/options/"
        ).base_url == "https://proxy.example.com/api/mp/unicornbay/options"
        with pytest.raises(ValidationError, match="HTTPS"):
            EodhdOptionsConfig(base_url="http://eodhd.com/api")
        with pytest.raises(ValidationError, match="nonempty"):
            EodhdOptionsConfig(base_url="   ")

    def test_blank_secret_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            EodhdOptionsConfig(api_key=SecretStr("   "))
        # None stays legal (unresolved key resolved later from the raw secret).
        assert EodhdOptionsConfig(api_key=None).api_key is None

    def test_timeout_and_pacing_bounds(self) -> None:
        with pytest.raises(ValidationError):
            EodhdOptionsConfig(timeout=0)
        with pytest.raises(ValidationError):
            EodhdOptionsConfig(request_pacing_s=float("nan"))
        assert EodhdOptionsConfig(request_pacing_s=0).request_pacing_s == 0

    def test_secret_masked_in_repr_and_dump(self) -> None:
        config = EodhdOptionsConfig(api_key=SecretStr("HIDDEN-VALUE-77"))
        assert "HIDDEN-VALUE-77" not in repr(config)
        assert "HIDDEN-VALUE-77" not in str(config.model_dump())

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            EodhdOptionsConfig(api_key=SecretStr("k"), unknown_setting=1)


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


class TestCboeConfig:
    def test_defaults(self) -> None:
        config = CboeConfig()
        assert config.provider_type == "cboe"
        assert (
            config.base_url
            == "https://cdn-api.cboe.com/api/global/us_indices/daily_prices"
        )
        assert config.timeout == 30.0
        assert config.cache_ttl_seconds == 300.0

    def test_blank_base_url_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Cboe base_url must be nonempty"):
            CboeConfig(base_url="   ")

    def test_base_url_stripped(self) -> None:
        config = CboeConfig(base_url="  https://example.com/api  ")
        assert config.base_url == "https://example.com/api"

    def test_positive_durations_enforced(self) -> None:
        with pytest.raises(ValidationError):
            CboeConfig(timeout=0.0)
        with pytest.raises(ValidationError):
            CboeConfig(cache_ttl_seconds=-1.0)

    def test_cboe_provider_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FIN3_MINIO__ENDPOINT", "localhost:9000")
        monkeypatch.setenv("FIN3_MINIO__ACCESS_KEY", "minioadmin")
        monkeypatch.setenv("FIN3_MINIO__SECRET_KEY", "minioadmin")
        monkeypatch.setenv("FIN3_PROVIDERS__CBOE__PROVIDER_TYPE", "cboe")
        config = ClientConfig()  # type: ignore[call-arg]
        cboe = config.providers["cboe"]
        assert type(cboe) is CboeConfig
        assert cboe.provider_type == "cboe"

    def test_cboe_joins_discriminated_union(self) -> None:
        config = ClientConfig(
            minio=MinioConfig(endpoint="localhost:9000", access_key="a", secret_key="b"),
            providers={"cboe": CboeConfig()},
        )
        assert type(config.providers["cboe"]) is CboeConfig
