"""Centralised Pydantic settings with .env file support."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field
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

class ThetaDataConfig(BaseModel):
    """ThetaData (thetadata SDK) provider settings.

    ThetaData authenticates via an API key (SDK >=1.0.9) — no Theta Terminal
    required. It is subscription-based (a limited free EOD tier exists), so
    ``estimate_cost()`` returns ``0.0`` and the ``MarketDataFetcher`` cost
    ceiling is **not** enforced for this provider. The v1 scope is US-equity
    OHLCV; ThetaData's options/Greeks/chains value does not map to the OHLCV
    schema and is deferred to a dedicated options phase.
    """

    provider_type: Literal["thetadata"] = "thetadata"
    api_key: str
    max_retries: int = 3
    initial_backoff: float = 1.0
    max_backoff: float = 30.0
    timeout: float = 30.0


ProviderConfig = Annotated[
    DatabentoConfig | MassiveConfig | BinanceConfig | YahooConfig | ThetaDataConfig,
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

    model_config = SettingsConfigDict(
        env_prefix="FIN3_",
        env_file=".env",
        env_nested_delimiter="__",
    )
