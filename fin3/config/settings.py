"""Centralised Pydantic settings with .env file support."""

from __future__ import annotations

import math
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MinioConfig(BaseModel):
    """MinIO / S3-compatible storage connection settings.

    Controls how fin3 connects to ArcticDB's storage backend. Supports
    both single-bucket and per-library-bucket layouts.
    """

    endpoint: str
    access_key: str
    secret_key: str
    secure: bool = False
    bucket: str = ""
    """When set, all libraries are stored inside this single bucket
    (library name used as ArcticDB library name). When empty, each
    library maps to its own S3 bucket."""


class DatabentoConfig(BaseModel):
    """Databento API connection settings.

    Controls dataset selection (e.g. XNAS.ITCH, ARCX.PILLAR), retry policy,
    and API authentication for the Databento provider.
    """

    provider_type: Literal["databento"] = "databento"
    api_key: str
    dataset: str = "XNAS.ITCH"
    max_retries: int = 3
    initial_backoff: float = 1.0
    max_backoff: float = 30.0


class MassiveConfig(BaseModel):
    """Massive (formerly Polygon.io) API connection settings.

    Polygon.io rebranded to **Massive** (massive.com) on 2025-10-30. APIs,
    keys, and data are unchanged; ``api.polygon.io`` and ``api.massive.com``
    run in parallel for an extended transition. The legacy names are
    deprecated — build against ``api.massive.com``.

    Configures authentication and behaviour for Massive's REST aggregates
    (``/v2/aggs``) endpoint, which serves historical OHLCV bars. The v1 scope
    is US equities (options/forex/futures/crypto are out of scope — crypto is
    served by Binance). Massive is a paid/keyed source with a limited free
    tier.
    """

    provider_type: Literal["massive"] = "massive"
    api_key: str
    base_url: str = "https://api.massive.com"
    adjusted: bool = False
    """Price basis. Stored **raw** by default (``adjusted=False``) for parity
    with Databento and fin3's store-raw-canonical philosophy; flip to ``True``
    for split/dividend-adjusted OHLC. Note Massive's API default is adjusted,
    so this is sent explicitly on every request."""
    request_limit: int = 50000
    """Max bars per request. Massive caps aggregates pages at 50,000."""
    max_retries: int = 3
    initial_backoff: float = 1.0
    max_backoff: float = 60.0
    timeout: float = 30.0


class BinanceConfig(BaseModel):
    """Binance API connection settings.

    Binance's spot klines endpoint (``/api/v3/klines``) is public and requires
    no authentication, so ``api_key``/``api_secret`` are optional — supplying
    them only grants a higher per-IP rate-limit weight allowance. The provider
    trades the fin3 ``BASE-USD`` convention against Binance's ``USDT`` quote
    (e.g. ``BTC-USD`` -> ``BTCUSDT``).
    """

    provider_type: Literal["binance"] = "binance"
    api_key: str = ""
    api_secret: str = ""
    base_url: str = "https://api.binance.com"
    max_retries: int = 3
    initial_backoff: float = 1.0
    max_backoff: float = 60.0
    timeout: float = 30.0
    request_limit: int = 1000


class YahooConfig(BaseModel):
    """Yahoo Finance (yfinance) provider settings.

    yfinance scrapes Yahoo's public endpoints unauthenticated, so no API key
    is needed. Prices are stored **raw** by default (``auto_adjust=False``) for
    parity with Databento and fin3's store-raw-canonical philosophy; flip
    ``auto_adjust`` for split/dividend-adjusted OHLC. Yahoo has no native
    ``4h`` interval — requests at that resolution fetch ``1h`` bars and rely
    on ``core._aggregate_bars`` to roll them up.
    """

    provider_type: Literal["yahoo"] = "yahoo"
    auto_adjust: bool = False
    max_retries: int = 3
    initial_backoff: float = 1.0
    max_backoff: float = 60.0
    timeout: float = 30.0


class CboeConfig(BaseModel):
    """Cboe published index-CSV provider settings.

    Serves daily **close levels** for the Cboe volatility indexes (``VIX``,
    ``VIX3M``) from Cboe's full-history CSV endpoint — keyless and free to
    retrieve. These are provider-source settings only; storage keeps using
    ``ClientConfig.minio``. The source is consumed for personal,
    non-commercial, non-redistributed research use under the published Cboe
    website terms.
    """

    provider_type: Literal["cboe"] = "cboe"
    base_url: str = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices"
    timeout: float = Field(default=30.0, gt=0)
    cache_ttl_seconds: float = Field(default=300.0, gt=0)

    @field_validator("base_url")
    @classmethod
    def _base_url_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Cboe base_url must be nonempty")
        return value.strip()


class EodhdOptionsConfig(BaseModel):
    """EODHD US options EOD provider settings (marketplace UnicornBay).

    Historical per-contract chains for US equity/ETF options, extracted
    through the documented paginated ``/contracts`` and ``/eod`` endpoints.
    ``api_key`` is a ``SecretStr`` and is **never** rendered in dumps,
    errors, or reports; it may be ``None`` here, in which case options
    ingestion falls back to the raw ``EODHD_API_KEY`` secret (see
    ``ClientConfig.eodhd_api_key``). Reads never need a key. An explicit
    config key wins over the raw secret.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    provider_type: Literal["eodhd_options"] = "eodhd_options"
    api_key: SecretStr | None = Field(default=None, repr=False)

    @field_validator("api_key", mode="before")
    @classmethod
    def _api_key_nonempty(cls, value: object) -> object:
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            raise ValueError("EodhdOptionsConfig api_key must not be empty/whitespace")
        return value

    base_url: str = "https://eodhd.com/api/mp/unicornbay/options"
    timeout: float = Field(default=30.0, gt=0)
    per_run_max_requests: int = Field(default=200, gt=0)
    request_pacing_s: float = Field(default=0.15, ge=0)
    page_size: int = Field(default=1000, ge=1, le=1000)

    @field_validator("base_url")
    @classmethod
    def _base_url_https(cls, value: str) -> str:
        url = value.strip()
        if not url:
            raise ValueError("EodhdOptionsConfig base_url must be nonempty")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(
                "EodhdOptionsConfig base_url must be an absolute HTTPS URL"
            )
        return url.rstrip("/")

    @field_validator("timeout", "request_pacing_s")
    @classmethod
    def _finite_float(cls, value: float, info: ValidationInfo) -> float:
        if not math.isfinite(value):
            raise ValueError(
                f"EodhdOptionsConfig {info.field_name} must be finite"
            )
        return value

    @field_validator("per_run_max_requests", "page_size", mode="before")
    @classmethod
    def _strict_int(cls, value: object, info: ValidationInfo) -> object:
        # Accept Python ints and decimal env strings ("500", " 500 " as env
        # vars arrive); reject bools and fractional strings/values. Plain
        # (non-strict) int coercion is intentionally used for the float
        # fields' env strings; here integrality is explicit.
        if isinstance(value, bool):
            raise ValueError(
                f"EodhdOptionsConfig {info.field_name} must be an integer"
            )
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            try:
                as_float = float(stripped)
            except ValueError as exc:
                raise ValueError(
                    f"EodhdOptionsConfig {info.field_name} must be an integer"
                ) from exc
            if not as_float.is_integer():
                raise ValueError(
                    f"EodhdOptionsConfig {info.field_name} must be an integer, "
                    f"got {value!r}"
                )
            return int(as_float)
        raise ValueError(
            f"EodhdOptionsConfig {info.field_name} must be an integer"
        )

    def max_requests(self) -> int:
        """Configured per-run request cap (a positive real ``int``)."""
        value = self.per_run_max_requests
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("per_run_max_requests must be a positive integer")
        return value


ProviderConfig = Annotated[
    DatabentoConfig | MassiveConfig | BinanceConfig | YahooConfig | CboeConfig
    | EodhdOptionsConfig,
    Field(discriminator="provider_type"),
]
"""Discriminated union of all supported provider config models.

``provider_type`` field determines which model is used at deserialisation.
"""


class LockConfig(BaseModel):
    """Configuration for per-symbol cross-process file locking.

    Locks guard writes in ``get_data()`` so that two processes cannot
    simultaneously fetch and store the same symbol, which would otherwise
    corrupt the underlying ArcticDB library. Set ``enabled`` to ``False`` to
    disable locking entirely (no lock files are written).
    """

    enabled: bool = True
    lock_dir: str = "/tmp/fin3/locks"
    timeout_s: float = 600.0
    poll_interval_s: float = 0.5


class ClientConfig(BaseSettings):
    """Top-level client configuration.

    Loaded from ``.env`` or environment variables with ``FIN3_`` prefix.
    Nested fields use ``__`` delimiter (e.g. ``FIN3_MINIO__ENDPOINT``).

    Parameters
    ----------
    minio : MinioConfig
        MinIO / S3 connection settings.
    providers : dict[str, ProviderConfig]
        Mapping of provider name to its config model.
    log_level : str
        Logging level (``INFO``, ``DEBUG``, etc.).
    log_format : str
        ``"json"`` for structured JSON or ``"console"`` for human-readable.
    lock : LockConfig
        Cross-process locking configuration.
    """

    minio: MinioConfig
    providers: dict[str, ProviderConfig] = {}
    log_level: str = "INFO"
    log_format: str = "json"
    lock: LockConfig = Field(default_factory=LockConfig)
    eodhd_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="EODHD_API_KEY",
        exclude=True,
        repr=False,
    )
    """Raw ``EODHD_API_KEY`` secret used by options ingestion when the
    ``eodhd_options`` provider config carries no key of its own. Hidden from
    dumps and reprs; collected by ``fin3.cli._collect_secrets``. Missing or
    blank is harmless for bar operations and options reads — it is rejected
    only when ingestion resolves an ingestion key."""

    model_config = SettingsConfigDict(
        env_prefix="FIN3_",
        env_file=".env",
        env_nested_delimiter="__",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
