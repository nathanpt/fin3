"""Cboe published volatility-index CSV provider (VIX / VIX3M).

Serves daily **close levels** — not returns — for Cboe's volatility indexes
from the full-history CSV documents published at
``https://cdn-api.cboe.com/api/global/us_indices/daily_prices/<SYMBOL>_History.csv``.
The endpoint is keyless, unpaginated, and unversioned: one document per
symbol, downloaded whole and cached in memory for ``cache_ttl_seconds``
(manifest sync fetches per session and would otherwise re-download the entire
file for every session chunk).

An observed index row encodes the published close honestly: all four OHLC
fields carry the close and volume is real zero (index levels have no traded
volume), never a sentinel. Source OPEN/HIGH/LOW columns are ignored
deliberately — Cboe's published VIX history contains rows that violate OHLC
inequalities, and fin3 neither promises usable index OHLC nor repairs
published data. Source rows dated on non-equity sessions keep their original
UTC-midnight labels on direct fetches; standard-path alignment excludes them
rather than shifting them.

Consumed for personal, non-commercial, non-redistributed research use under
the published Cboe website terms; no redistribution of Cboe-derived data.
"""

from __future__ import annotations

import io
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime

import numpy as np
import pandas as pd
import structlog

from fin3.config.settings import CboeConfig
from fin3.exceptions import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from fin3.providers import ProviderRegistry
from fin3.providers.base import DataProvider
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, empty_ohlcv
from fin3.utils.date_utils import ensure_utc

logger = structlog.get_logger(__name__)

_SUPPORTED_SYMBOLS = ("VIX", "VIX3M")
_DATE_FORMAT = "%m/%d/%Y"


def _normalise(payload: bytes) -> pd.DataFrame:
    """Convert one Cboe full-history CSV into the close-only OHLCV encoding.

    Reads only the required ``DATE`` and ``CLOSE`` columns. Dates parse
    exactly as ``%m/%d/%Y`` into UTC-midnight stamps; closes become float64.
    Missing headers, malformed bytes/CSV/dates/numbers, nonfinite or
    nonpositive closes, duplicate dates, or out-of-order dates raise
    ``ProviderError`` — conflicting source records are never dropped,
    deduplicated, sorted, or coerced to NaN. A correctly headed zero-row CSV
    returns ``empty_ohlcv()``; a zero-byte, HTML, or otherwise non-CSV
    payload is malformed, not "no data".
    """
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProviderError(f"Cboe payload is not valid UTF-8: {exc}") from exc
    if not text.strip():
        raise ProviderError(
            "Cboe returned an empty payload (expected a CSV document)"
        )

    try:
        raw = pd.read_csv(io.StringIO(text), usecols=["DATE", "CLOSE"])
    except (
        ValueError,
        TypeError,
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
    ) as exc:
        raise ProviderError(
            f"Cboe CSV is malformed (missing DATE/CLOSE headers or "
            f"unparsable rows): {exc}"
        ) from exc

    if raw.empty:
        return empty_ohlcv()

    try:
        stamps = pd.DatetimeIndex(
            pd.to_datetime(raw["DATE"], format=_DATE_FORMAT, utc=True)
        )
    except (ValueError, TypeError) as exc:
        raise ProviderError(
            f"Cboe DATE column has values outside {_DATE_FORMAT}: {exc}"
        ) from exc

    try:
        closes = pd.to_numeric(raw["CLOSE"], errors="raise").astype("float64")
    except (ValueError, TypeError) as exc:
        raise ProviderError(
            f"Cboe CLOSE column contains malformed numbers: {exc}"
        ) from exc

    values = closes.to_numpy()
    if not np.isfinite(values).all():
        raise ProviderError("Cboe CLOSE column contains nonfinite values")
    if (values <= 0).any():
        raise ProviderError("Cboe CLOSE column contains nonpositive values")
    if stamps.has_duplicates:
        first_dup = stamps[stamps.duplicated()][0]
        raise ProviderError(
            f"Cboe history contains duplicate dates (first: {first_dup.date()})"
        )
    if not stamps.is_monotonic_increasing:
        raise ProviderError("Cboe history is not in ascending date order")

    frame = pd.DataFrame(
        {
            "open": values,
            "high": values,
            "low": values,
            "close": values,
            "volume": np.zeros(len(values), dtype="float64"),
        },
        index=pd.DatetimeIndex(stamps),
    )
    frame.index.name = None
    return frame[list(OHLCV_COLUMNS)]


@ProviderRegistry.register("cboe")
class CboeProvider(DataProvider):
    """Fetches daily Cboe volatility-index close levels.

    Accepts only the exact symbols ``VIX``/``VIX3M`` at
    ``Resolution.ONE_DAY`` with asset type ``AssetType.INDEX_US`` (or
    ``None`` — MetadataStore discovery omits the asset type). Validation
    happens before any HTTP call. Full-history snapshots are shared between
    bounds and range fetches and reloaded once stale; failed reloads raise
    and never serve expired data as a fallback, and ``fetch`` returns copies
    so consumers cannot mutate the cached snapshot.
    """

    def __init__(self, config: CboeConfig) -> None:
        """Initialise the provider from its source settings.

        Parameters
        ----------
        config : CboeConfig
            Base URL, request timeout, and in-memory cache TTL.
        """
        self._base_url = config.base_url.rstrip("/")
        self._timeout = config.timeout
        self._cache_ttl = config.cache_ttl_seconds
        # symbol -> (monotonic expiry, full normalized history)
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}

    def _validate_symbol(self, symbol: str) -> None:
        if symbol not in _SUPPORTED_SYMBOLS:
            raise ProviderError(
                f"Unsupported symbol {symbol!r} for Cboe; expected VIX or VIX3M"
            )

    def _validate_target(
        self,
        symbol: str,
        resolution: Resolution,
        asset_type: AssetType | None,
        start: datetime,
        end: datetime,
    ) -> None:
        self._validate_symbol(symbol)
        if resolution != Resolution.ONE_DAY:
            raise ProviderError(
                f"Unsupported resolution {resolution} for Cboe; only 1d is supported"
            )
        if asset_type is not None and asset_type is not AssetType.INDEX_US:
            raise ProviderError(
                f"Unsupported asset type {asset_type} for Cboe; expected index_us"
            )
        if ensure_utc(start) > ensure_utc(end):
            raise ProviderError("Cboe start must not be after end")

    def _request(self, symbol: str) -> bytes:
        """Download one symbol's full-history CSV.

        Typed exception classification without retry or fallback policy;
        failures are never cached.

        Raises
        ------
        ProviderRateLimitError
            On HTTP 429.
        ProviderTimeoutError
            On a direct/socket/URL-wrapped timeout.
        ProviderError
            On any other HTTP or network failure.
        """
        url = f"{self._base_url}/{symbol}_History.csv"
        request = urllib.request.Request(url, headers={"User-Agent": "fin3"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                payload = resp.read()
                return bytes(payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ProviderRateLimitError(
                    f"Cboe rate limit (429) for {symbol}"
                ) from exc
            raise ProviderError(
                f"Cboe HTTP {exc.code} for {symbol}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise ProviderTimeoutError(f"Cboe timeout for {symbol}") from exc
            raise ProviderError(
                f"Cboe network error for {symbol}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise ProviderTimeoutError(f"Cboe timeout for {symbol}") from exc

    def _history(self, symbol: str) -> pd.DataFrame:
        """Return the symbol's full normalized history snapshot.

        Lazy-loads on first use and reloads once ``cache_ttl_seconds`` has
        elapsed on a fresh ``time.monotonic()`` clock. A failed reload raises
        and leaves the stale entry unserved.
        """
        entry = self._cache.get(symbol)
        if entry is not None and time.monotonic() < entry[0]:
            return entry[1]
        frame = _normalise(self._request(symbol))
        self._cache[symbol] = (time.monotonic() + self._cache_ttl, frame)
        return frame

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
        """Fetch daily close levels for one symbol over ``[start, end]``.

        Source dates are selected inclusively between the normalized UTC
        bound dates and keep their UTC-midnight labels (core's daily snapping
        maps them onto the session grid). Valid ranges outside the history
        return ``empty_ohlcv()``.
        """
        self._validate_target(symbol, resolution, asset_type, start, end)
        history = self._history(symbol)
        if history.empty:
            logger.info("provider.empty", provider="cboe", symbol=symbol)
            return empty_ohlcv()

        start_ts = ensure_utc(start).normalize()
        end_ts = ensure_utc(end).normalize()
        mask = (history.index >= start_ts) & (history.index <= end_ts)
        selected = history.loc[mask]
        if selected.empty:
            logger.info("provider.empty", provider="cboe", symbol=symbol)
            return empty_ohlcv()

        logger.info("provider.fetched", provider="cboe", symbol=symbol, rows=len(selected))
        # Copy so callers can never mutate the cached snapshot.
        return selected.copy()

    def estimate_cost(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        resolution: Resolution,
        *,
        asset_type: AssetType | None = None,
    ) -> float:
        """Return ``0.0`` after the same target validation as ``fetch``.

        Cboe's published CSVs are free, so ``max_cost`` ceilings treat the
        provider as free; no HTTP call is made.
        """
        self._validate_target(symbol, resolution, asset_type, start, end)
        return 0.0

    def get_instrument_bounds(self, symbol: str) -> dict[str, datetime | None]:
        """Resolve lifecycle bounds from the full source history.

        ``ipo_date`` is the first actual source date; ``delist_date`` is
        always ``None`` — the last downloaded date is a retrieval artifact,
        never a delisting. An empty (but valid) source returns both ``None``.
        Shares the history cache and symbol validation with ``fetch``.
        """
        self._validate_symbol(symbol)
        history = self._history(symbol)
        if history.empty:
            return {"ipo_date": None, "delist_date": None}
        return {
            "ipo_date": history.index[0].to_pydatetime(),
            "delist_date": None,
        }
