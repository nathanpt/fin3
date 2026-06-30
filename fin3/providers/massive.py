"""Massive (formerly Polygon.io) aggregates market data provider implementation.

Polygon.io rebranded to **Massive** (massive.com) on 2025-10-30. APIs, keys,
and data are unchanged; ``api.polygon.io`` and ``api.massive.com`` run in
parallel for an extended transition. This provider targets the new
``api.massive.com`` host by default (configurable via ``MassiveConfig.base_url``).
"""

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

from fin3.config.settings import MassiveConfig
from fin3.exceptions import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from fin3.providers import ProviderRegistry
from fin3.providers.base import DataProvider
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, empty_ohlcv

logger = structlog.get_logger(__name__)

_RESOLUTION_TO_RANGE: dict[Resolution, tuple[int, str]] = {
    Resolution.ONE_MINUTE: (1, "minute"),
    Resolution.FIVE_MINUTE: (5, "minute"),
    Resolution.FIFTEEN_MINUTE: (15, "minute"),
    Resolution.ONE_HOUR: (1, "hour"),
    Resolution.FOUR_HOUR: (4, "hour"),
    Resolution.ONE_DAY: (1, "day"),
}
"""Maps fin3 resolutions to Massive's ``(multiplier, timespan)`` pair.

Unlike Yahoo/Binance, Massive supports an arbitrary multiplier, so ``4h`` maps
natively to ``4 x hour`` (no ``core._aggregate_bars`` fallback required)."""

_MS_PER_SEC = 1000


def _to_ms(dt: datetime) -> int:
    """Convert a datetime to Unix milliseconds, treating naive values as UTC.

    The Massive aggregates endpoint accepts the ``from``/``to`` path segments
    as either ``YYYY-MM-DD`` dates or millisecond timestamps; using
    millisecond timestamps uniformly supports both daily and intraday ranges.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * _MS_PER_SEC)


@ProviderRegistry.register("massive")
class MassiveProvider(DataProvider):
    """Fetches OHLCV data from Massive's REST aggregates (``/v2/aggs``) endpoint.

    The endpoint is API-key authenticated (paid subscription; limited free
    tier). This provider paginates large ranges via the response's ``next_url``
    cursor (up to ``request_limit`` bars per page), maps fin3 resolutions to
    Massive's ``(multiplier, timespan)`` pair (incl. native ``4h``), stores
    **raw** OHLC by default (``adjusted=False``) for Databento parity, and
    applies exponential backoff on 429/timeout responses.

    Massive is subscription-based and exposes no per-query cost, so
    ``estimate_cost()`` returns ``0.0`` and the ``MarketDataFetcher`` cost
    ceiling is **not** enforced for this provider.
    """

    def __init__(self, config: MassiveConfig) -> None:
        """Initialise the Massive provider.

        Parameters
        ----------
        config : MassiveConfig
            Massive API configuration (base URL, API key, price basis, retry
            policy, timeout, per-request bar limit).
        """
        self._base_url = config.base_url.rstrip("/")
        self._api_key = config.api_key
        self._adjusted = config.adjusted
        self._max_retries = config.max_retries
        self._initial_backoff = config.initial_backoff
        self._max_backoff = config.max_backoff
        self._timeout = config.timeout
        # Massive caps aggregates pages at 50,000 bars.
        self._limit = min(config.request_limit, 50000)

    def _request(self, url: str) -> dict[str, Any]:
        """Perform a single aggregates HTTP GET and return parsed JSON.

        Raises typed provider exceptions and does **not** retry — callers
        wrap this in ``_request_with_retry``.

        Parameters
        ----------
        url : str
            Fully-qualified request URL, including the ``apiKey`` query param.

        Returns
        -------
        dict[str, Any]
            Parsed JSON response (contains ``results`` and optional
            ``next_url``).

        Raises
        ------
        ProviderRateLimitError
            On HTTP 429.
        ProviderTimeoutError
            On read timeout.
        ProviderError
            On any other HTTP error, network error, or malformed response.
        """
        headers = {"User-Agent": "fin3"}
        request = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ProviderRateLimitError(f"Massive rate limit (429) at {url}") from exc
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - best-effort error body
                body = ""
            raise ProviderError(
                f"Massive HTTP {exc.code}: {body}"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise ProviderTimeoutError(f"Massive timeout at {url}") from exc
            raise ProviderError(
                f"Massive network error: {exc.reason}"
            ) from exc

        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError("Massive returned non-JSON response") from exc

        if not isinstance(data, dict):
            raise ProviderError("Massive unexpected response shape (not an object)")
        return data

    def _request_with_retry(self, url: str) -> dict[str, Any]:
        """Call ``_request`` with exponential backoff on retryable errors.

        Retries ``ProviderRateLimitError`` and ``ProviderTimeoutError`` up to
        ``max_retries`` total attempts; all other ``ProviderError`` subclasses
        propagate immediately (they are not transient).
        """
        for attempt in range(self._max_retries):
            try:
                return self._request(url)
            except (ProviderRateLimitError, ProviderTimeoutError):
                if attempt == self._max_retries - 1:
                    raise
                backoff = min(
                    self._initial_backoff * (2**attempt),
                    self._max_backoff,
                )
                logger.warning(
                    "provider.retry",
                    provider="massive",
                    attempt=attempt + 1,
                    backoff=backoff,
                )
                time.sleep(backoff)
        # Unreachable: the loop only exits via return or re-raise, but this
        # keeps the return type explicit for callers/type-checkers when
        # ``max_retries`` is configured as 0.
        raise ProviderError("Massive retry loop exited without a result")

    def _build_url(
        self,
        symbol: str,
        multiplier: int,
        timespan: str,
        from_ms: int,
        to_ms: int,
    ) -> str:
        """Build the first-page request URL (path + query params + apiKey).

        Subsequent pages follow the response's ``next_url`` cursor directly
        via ``_add_api_key`` rather than rebuilding the path.
        """
        path = (
            f"/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/"
            f"{multiplier}/{timespan}/{from_ms}/{to_ms}"
        )
        params = urllib.parse.urlencode(
            {
                "adjusted": str(self._adjusted).lower(),
                "sort": "asc",
                "limit": self._limit,
                "apiKey": self._api_key,
            }
        )
        return f"{self._base_url}{path}?{params}"

    @staticmethod
    def _add_api_key(url: str, api_key: str) -> str:
        """Ensure the ``apiKey`` query param is present on a ``next_url``.

        Massive's ``next_url`` carries the pagination cursor but may omit the
        API key; strip any stale ``apiKey`` and append a fresh one.
        """
        parsed = urllib.parse.urlparse(url)
        params = [
            (k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k != "apiKey"
        ]
        params.append(("apiKey", api_key))
        return parsed._replace(query=urllib.parse.urlencode(params)).geturl()

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
        """Fetch OHLCV aggregates for a single symbol over ``[start, end]``.

        Paginates by following the response's ``next_url`` cursor until it is
        absent, results are empty, or the cursor stops advancing. Returns an
        empty canonical DataFrame when no data exists for the range.

        Parameters
        ----------
        symbol : str
            Massive ticker (e.g. ``AAPL``). fin3's uppercase equity convention
            is already Massive-native, so symbols pass through unchanged.
        start : datetime
            Start of the range (UTC if aware, else assumed UTC).
        end : datetime
            End of the range (UTC if aware, else assumed UTC).
        resolution : Resolution
            Bar resolution.
        asset_type : AssetType or None
            Optional asset type (ignored — Massive serves all configured
            asset types via the same endpoint).

        Returns
        -------
        pd.DataFrame
            OHLCV DataFrame with a UTC DatetimeIndex.

        Raises
        ------
        ProviderError
            If the resolution is unsupported or the API fails after retries.
        """
        range_ = _RESOLUTION_TO_RANGE.get(resolution)
        if range_ is None:
            raise ProviderError(f"Unsupported resolution {resolution} for Massive")
        multiplier, timespan = range_

        to_ms = _to_ms(end)
        url = self._build_url(symbol, multiplier, timespan, _to_ms(start), to_ms)

        results: list[dict[str, Any]] = []
        previous_url: str | None = None

        while url is not None:
            payload = self._request_with_retry(url)
            batch = payload.get("results")
            if not batch:
                break

            # Defensive filtering: keep bars whose open time is within range.
            results.extend(row for row in batch if int(row["t"]) <= to_ms)

            next_url = payload.get("next_url")
            if not next_url or next_url == previous_url:
                # No more pages, or cursor failed to advance (guard against an
                # unexpected non-advancing response).
                break
            previous_url = next_url
            url = self._add_api_key(next_url, self._api_key)

        if not results:
            logger.info("provider.empty", provider="massive", symbol=symbol)
            return empty_ohlcv()

        logger.info(
            "provider.fetched",
            provider="massive",
            symbol=symbol,
            rows=len(results),
        )
        return _normalise(results)

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

        Massive is subscription-based and exposes no per-query cost, so this
        always returns ``0.0``. The ``MarketDataFetcher`` ``max_cost`` ceiling
        is therefore **not** enforced for this provider. Implemented (rather
        than omitted) so the fetcher treats Massive uniformly.
        """
        return 0.0

    def get_instrument_bounds(self, symbol: str) -> dict[str, datetime | None]:
        """Resolve an equity's listing date via a single daily-agg probe.

        Fetches the earliest available daily aggregate (range spanning
        ``1970-01-01`` to today, ``sort=asc``, ``limit=1``) and uses its
        timestamp as the listing (IPO) date so gap detection does not try to
        backfill a symbol before it existed. US equities have no automated
        delisting date from this endpoint, so ``delist_date`` is always None.
        On any error (unknown symbol, network), returns ``{None, None}`` so
        the metadata bootstrap falls back to discovery.

        .. note::

            On **limited plans** (including the free tier), Massive returns
            HTTP 200 with plan-truncated results rather than an error or the
            symbol's full history. The probe therefore resolves to the plan's
            history boundary (e.g. ~2024-07 on the free tier as of mid-2026),
            **not** the symbol's true listing date (AAPL IPO 1980-12-12). This
            is still a correct lower bound for gap detection — it is the
            effective first *accessible* bar — but the result is plan-dependent
            and a later plan upgrade will not invalidate any cached value
            automatically. A paid key resolves the true listing date.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Probe the earliest available daily aggregate: full history from
        # 1970-01-01 to today, ascending, first bar only.
        path = f"/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/1/day/1970-01-01/{today}"
        params = urllib.parse.urlencode(
            {
                "adjusted": str(self._adjusted).lower(),
                "sort": "asc",
                "limit": 1,
                "apiKey": self._api_key,
            }
        )
        url = f"{self._base_url}{path}?{params}"

        try:
            payload = self._request_with_retry(url)
        except ProviderError as exc:
            logger.warning(
                "provider.bounds_failed",
                provider="massive",
                symbol=symbol,
                error=str(exc),
            )
            return {"ipo_date": None, "delist_date": None}

        results = payload.get("results")
        if not results:
            return {"ipo_date": None, "delist_date": None}

        first_t_ms = int(results[0]["t"])
        ipo_date = datetime.fromtimestamp(first_t_ms / _MS_PER_SEC, tz=timezone.utc)
        return {"ipo_date": ipo_date, "delist_date": None}


def _normalise(results: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert raw Massive aggregate rows to the canonical OHLCV schema.

    Each row carries ``o``/``h``/``l``/``c``/``v``/``t`` (plus optional
    ``vw``/``n``/``T`` which are dropped). The index is the bar open time
    (UTC, derived from the millisecond epoch). Duplicate open times and any
    ordering drift are removed so Stage-1 validation always passes.
    """
    raw = pd.DataFrame(results)
    df = pd.DataFrame(
        {
            "open": pd.to_numeric(raw["o"], errors="raise").to_numpy(),
            "high": pd.to_numeric(raw["h"], errors="raise").to_numpy(),
            "low": pd.to_numeric(raw["l"], errors="raise").to_numpy(),
            "close": pd.to_numeric(raw["c"], errors="raise").to_numpy(),
            "volume": pd.to_numeric(raw["v"], errors="raise").to_numpy(),
        },
        index=pd.to_datetime(pd.to_numeric(raw["t"]).to_numpy(), unit="ms", utc=True),
    )
    df = df[~df.index.duplicated(keep="first")].sort_index()
    df.index.name = None
    return df[list(OHLCV_COLUMNS)]
