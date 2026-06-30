"""Binance spot market data provider implementation."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog

from fin3.config.settings import BinanceConfig
from fin3.exceptions import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from fin3.providers import ProviderRegistry
from fin3.providers.base import DataProvider
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, empty_ohlcv

logger = structlog.get_logger(__name__)

_RESOLUTION_TO_INTERVAL: dict[Resolution, str] = {
    Resolution.ONE_MINUTE: "1m",
    Resolution.FIVE_MINUTE: "5m",
    Resolution.FIFTEEN_MINUTE: "15m",
    Resolution.ONE_HOUR: "1h",
    Resolution.FOUR_HOUR: "4h",
    Resolution.ONE_DAY: "1d",
}
"""Maps fin3 resolutions to Binance kline intervals (identical vocabulary)."""

_KLINES_PATH = "/api/v3/klines"
_MS_PER_SEC = 1000


def _to_binance_symbol(symbol: str) -> str:
    """Map a fin3 crypto symbol to Binance's spot convention.

    fin3 uses ``BASE-USD`` (e.g. ``BTC-USD``); Binance spot trades against
    ``USDT`` (e.g. ``BTCUSDT``). The separator is stripped and a trailing
    ``USD`` quote (without the ``T``) is promoted to ``USDT``. Symbols that
    already conform (e.g. ``BTCUSDT``, ``BTC-USDT``) pass through unchanged.

    Parameters
    ----------
    symbol : str
        fin3 crypto symbol (e.g. ``BTC-USD``).

    Returns
    -------
    str
        Binance spot symbol (e.g. ``BTCUSDT``).
    """
    cleaned = symbol.replace("-", "").upper()
    if cleaned.endswith("USD") and not cleaned.endswith("USDT"):
        cleaned += "T"
    return cleaned


def _to_ms(dt: datetime) -> int:
    """Convert a datetime to Unix milliseconds, treating naive values as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * _MS_PER_SEC)


@ProviderRegistry.register("binance")
class BinanceProvider(DataProvider):
    """Fetches OHLCV data from Binance's public spot klines endpoint.

    The ``/api/v3/klines`` endpoint is unauthenticated and weight-rate-limited
    per IP. This provider paginates large ranges (up to 1000 bars per request),
    maps fin3 resolutions to Binance intervals, converts ``BTC-USD`` to
    ``BTCUSDT``, and applies exponential backoff on 429/timeout responses.
    """

    def __init__(self, config: BinanceConfig) -> None:
        """Initialise the Binance provider.

        Parameters
        ----------
        config : BinanceConfig
            Binance API configuration (base URL, retry policy, timeout,
            per-request bar limit).
        """
        self._base_url = config.base_url.rstrip("/")
        self._api_key = config.api_key
        self._max_retries = config.max_retries
        self._initial_backoff = config.initial_backoff
        self._max_backoff = config.max_backoff
        self._timeout = config.timeout
        # Binance caps klines at 1000 bars per request.
        self._limit = min(config.request_limit, 1000)

    def _request(self, params: dict[str, Any]) -> list[list[Any]]:
        """Perform a single klines HTTP GET and return parsed rows.

        Raises typed provider exceptions and does **not** retry — callers
        wrap this in ``_request_with_retry``.

        Parameters
        ----------
        params : dict[str, Any]
            Query parameters for the klines endpoint.

        Returns
        -------
        list[list[Any]]
            Raw kline rows from Binance.

        Raises
        ------
        ProviderRateLimitError
            On HTTP 429.
        ProviderTimeoutError
            On read timeout.
        ProviderError
            On any other HTTP error, network error, or malformed response.
        """
        url = f"{self._base_url}{_KLINES_PATH}?{urllib.parse.urlencode(params)}"
        headers: dict[str, str] = {"User-Agent": "fin3"}
        if self._api_key:
            headers["X-MBX-APIKEY"] = self._api_key
        request = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ProviderRateLimitError(
                    f"Binance rate limit (429) for {params.get('symbol')}"
                ) from exc
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - best-effort error body
                body = ""
            raise ProviderError(
                f"Binance HTTP {exc.code} for {params.get('symbol')}: {body}"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise ProviderTimeoutError(
                    f"Binance timeout for {params.get('symbol')}"
                ) from exc
            raise ProviderError(
                f"Binance network error for {params.get('symbol')}: {exc.reason}"
            ) from exc

        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(
                f"Binance returned non-JSON response for {params.get('symbol')}"
            ) from exc

        if not isinstance(data, list):
            raise ProviderError(
                f"Binance unexpected response shape for {params.get('symbol')}"
            )
        return data

    def _request_with_retry(self, params: dict[str, Any]) -> list[list[Any]]:
        """Call ``_request`` with exponential backoff on retryable errors.

        Retries ``ProviderRateLimitError`` and ``ProviderTimeoutError`` up to
        ``max_retries`` total attempts; all other ``ProviderError`` subclasses
        propagate immediately (they are not transient).
        """
        for attempt in range(self._max_retries):
            try:
                return self._request(params)
            except (ProviderRateLimitError, ProviderTimeoutError):
                if attempt == self._max_retries - 1:
                    raise
                backoff = min(
                    self._initial_backoff * (2**attempt),
                    self._max_backoff,
                )
                logger.warning(
                    "provider.retry",
                    provider="binance",
                    symbol=params.get("symbol"),
                    attempt=attempt + 1,
                    backoff=backoff,
                )
                time.sleep(backoff)
        # Unreachable: the loop only exits via return or re-raise, but this
        # keeps the return type explicit for callers/type-checkers when
        # ``max_retries`` is configured as 0.
        raise ProviderError("Binance retry loop exited without a result")

    def fetch(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        resolution: Resolution,
        *,
        asset_type: AssetType | None = None,
        **kwargs: object,
    ) -> pd.DataFrame:
        """Fetch OHLCV klines for a single symbol over ``[start, end]``.

        Paginates up to ``request_limit`` bars per request, advancing the
        start cursor to just after the last returned open time. Returns an
        empty canonical DataFrame when no data exists for the range.

        Parameters
        ----------
        symbol : str
            fin3 crypto symbol (e.g. ``BTC-USD``).
        start : datetime
            Start of the range (UTC if aware, else assumed UTC).
        end : datetime
            End of the range (UTC if aware, else assumed UTC).
        resolution : Resolution
            Bar resolution.
        asset_type : AssetType or None
            Optional asset type (ignored — Binance only serves crypto).

        Returns
        -------
        pd.DataFrame
            OHLCV DataFrame with a UTC DatetimeIndex.

        Raises
        ------
        ProviderError
            If the resolution is unsupported or the API fails after retries.
        """
        interval = _RESOLUTION_TO_INTERVAL.get(resolution)
        if interval is None:
            raise ProviderError(f"Unsupported resolution {resolution} for Binance")

        binance_symbol = _to_binance_symbol(symbol)
        end_ms = _to_ms(end)
        rows: list[list[Any]] = []
        current_ms = _to_ms(start)

        while current_ms <= end_ms:
            params: dict[str, Any] = {
                "symbol": binance_symbol,
                "interval": interval,
                "startTime": current_ms,
                "limit": self._limit,
            }
            batch = self._request_with_retry(params)
            if not batch:
                break

            rows.extend(row for row in batch if row[0] <= end_ms)

            last_open = int(batch[-1][0])
            # Stop once the requested end is covered, or if no progress is
            # made (guards against an unexpected non-advancing response).
            if last_open >= end_ms or last_open < current_ms:
                break
            current_ms = last_open + 1
            if len(batch) < self._limit:
                break

        if not rows:
            logger.info("provider.empty", provider="binance", symbol=symbol)
            return empty_ohlcv()

        logger.info(
            "provider.fetched",
            provider="binance",
            symbol=symbol,
            rows=len(rows),
        )
        return _normalise(rows)

    def estimate_cost(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        resolution: Resolution,
        *,
        asset_type: AssetType | None = None,
    ) -> float:
        """Return the estimated download cost in USD.

        Binance's public klines endpoint is free, so the cost is always
        ``0.0``. Implemented (rather than omitted) so ``MarketDataFetcher``'s
        cost ceiling treats Binance as free instead of skipping the check.
        """
        return 0.0

    def get_instrument_bounds(self, symbol: str) -> dict[str, datetime | None]:
        """Resolve a crypto pair's listing date via a single klines probe.

        Fetches the earliest available daily kline (``startTime=0``) and uses
        its open time as the listing (IPO) date so gap detection does not try
        to backfill a pair before it existed. Crypto has no equity-style
        delisting, so ``delist_date`` is always None. On any error (unknown
        symbol, network), returns ``{None, None}`` so the metadata bootstrap
        falls back to discovery.
        """
        binance_symbol = _to_binance_symbol(symbol)
        try:
            batch = self._request_with_retry(
                {
                    "symbol": binance_symbol,
                    "interval": "1d",
                    "startTime": 0,
                    "limit": 1,
                }
            )
        except ProviderError as exc:
            logger.warning(
                "provider.bounds_failed",
                provider="binance",
                symbol=symbol,
                error=str(exc),
            )
            return {"ipo_date": None, "delist_date": None}

        if not batch:
            return {"ipo_date": None, "delist_date": None}

        first_open_ms = int(batch[0][0])
        ipo_date = datetime.fromtimestamp(first_open_ms / _MS_PER_SEC, tz=timezone.utc)
        return {"ipo_date": ipo_date, "delist_date": None}


def _normalise(rows: list[list[Any]]) -> pd.DataFrame:
    """Convert raw Binance kline rows to the canonical OHLCV schema.

    Each row is ``[openTime, open, high, low, close, volume, closeTime, ...]``
    with prices and volume encoded as strings. The index is the open time
    (UTC, derived from the millisecond epoch). Duplicate open times and any
    ordering drift are removed so Stage-1 validation always passes.
    """
    raw = pd.DataFrame(rows)
    df = pd.DataFrame(
        {
            "open": raw[1].to_numpy().astype(float),
            "high": raw[2].to_numpy().astype(float),
            "low": raw[3].to_numpy().astype(float),
            "close": raw[4].to_numpy().astype(float),
            "volume": raw[5].to_numpy().astype(float),
        },
        index=pd.to_datetime(raw[0].to_numpy(), unit="ms", utc=True),
    )
    df = df[~df.index.duplicated(keep="first")].sort_index()
    df.index.name = None
    return df[list(OHLCV_COLUMNS)]
