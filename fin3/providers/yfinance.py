"""Yahoo Finance (yfinance) data provider implementation."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pandas as pd
import structlog

from fin3.config.settings import YahooConfig
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
    # Yahoo has no native 4h interval: fetch 1h and let core._aggregate_bars
    # roll the finer bars up to the requested resolution (same pattern
    # Databento uses for 5m/15m -> 1m).
    Resolution.FOUR_HOUR: "1h",
    Resolution.ONE_DAY: "1d",
}
"""Maps fin3 resolutions to yfinance ``interval`` values."""

_YF_COLUMN_MAP: dict[str, str] = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
}
"""Maps Yahoo's capitalised column names to the canonical lowercase schema."""

_MAX_PERIOD_DAILY = "max"
"""Period used for lifecycle probing — full daily history is compact and unlimited."""


def _classify_error(exc: BaseException) -> str:
    """Return ``"rate"``, ``"timeout"``, or ``"fatal"`` for an yfinance exception.

    yfinance raises its own ``YFRateLimitError`` on throttling but also surfaces
    generic network errors, so we classify by both class name and message
    substring to avoid hard-coupling to yfinance internals.
    """
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "rate" in msg or "429" in msg:
        return "rate"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    return "fatal"


@ProviderRegistry.register("yahoo")
class YahooProvider(DataProvider):
    """Fetches OHLCV data from Yahoo Finance via the ``yfinance`` library.

    yfinance scrapes Yahoo's public chart endpoints unauthenticated and is
    free, but unofficial and rate-limited. Symbols are Yahoo-native (``AAPL``,
    ``SPY``, ``BTC-USD``, ``EURUSD=X``, ``ES=F``, ``^GSPC``) and passed through
    unchanged — the caller's ``AssetType`` selects the calendar and library.

    Prices are raw by default (``auto_adjust=False``); set ``YahooConfig.auto_adjust``
    for split/dividend-adjusted OHLC.

    .. note::

       Yahoo limits intraday history (``1m`` -> 7 days, ``5m/15m/30m`` -> 60
       days, ``60m/90m`` -> 730 days). Requests outside those windows return
       an empty DataFrame rather than raising. Daily data is unrestricted.
    """

    def __init__(self, config: YahooConfig) -> None:
        """Initialise the Yahoo provider.

        Parameters
        ----------
        config : YahooConfig
            Yahoo provider configuration (price basis, retry policy, timeout).

        Raises
        ------
        ProviderError
            If the ``yfinance`` package is not importable. Install the optional
            extra with ``pip install fin3[yfinance]``.
        """
        try:
            import yfinance as yf
        except Exception as exc:  # noqa: BLE001 - surface any import failure uniformly
            raise ProviderError(
                "Failed to import yfinance. Install it with "
                "`pip install fin3[yfinance]` or `uv sync --extra yfinance`."
            ) from exc
        self._client: Any = yf
        self._auto_adjust = config.auto_adjust
        self._max_retries = config.max_retries
        self._initial_backoff = config.initial_backoff
        self._max_backoff = config.max_backoff
        self._timeout = config.timeout

    def _history(self, symbol: str, **kwargs: Any) -> pd.DataFrame:
        """Call ``Ticker(symbol).history`` with retry/backoff on transient errors.

        ``raise_errors=True`` is forwarded so yfinance surfaces failures as
        exceptions instead of silently logging them; this method then maps
        them to typed fin3 provider exceptions.
        """
        last_exc: BaseException | None = None
        for attempt in range(self._max_retries):
            try:
                ticker = self._client.Ticker(symbol)
                df: pd.DataFrame = ticker.history(
                    raise_errors=True,
                    timeout=self._timeout,
                    **kwargs,
                )
                return df
            except Exception as exc:  # noqa: BLE001 - classify and translate
                last_exc = exc
                kind = _classify_error(exc)
                if kind == "fatal":
                    raise ProviderError(
                        f"Yahoo error fetching {symbol}: {exc}"
                    ) from exc
                if attempt < self._max_retries - 1:
                    backoff = min(
                        self._initial_backoff * (2**attempt),
                        self._max_backoff,
                    )
                    logger.warning(
                        "provider.retry",
                        provider="yahoo",
                        symbol=symbol,
                        attempt=attempt + 1,
                        backoff=backoff,
                        kind=kind,
                    )
                    time.sleep(backoff)
                    continue
                if kind == "rate":
                    raise ProviderRateLimitError(
                        f"Yahoo rate limit exceeded for {symbol}"
                    ) from exc
                raise ProviderTimeoutError(f"Yahoo timeout for {symbol}") from exc

        # Unreachable when max_retries >= 1; guarded for max_retries == 0.
        raise ProviderError(
            f"Yahoo retry loop exited without a result for {symbol}: {last_exc}"
        )

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
        """Fetch OHLCV bars for a single symbol over ``[start, end]``.

        Returns an empty canonical DataFrame when Yahoo has no data for the
        range (including intraday requests older than Yahoo's history window).

        Parameters
        ----------
        symbol : str
            Yahoo-native symbol (e.g. ``AAPL``, ``BTC-USD``).
        start : datetime
            Start of the range (UTC if aware, else assumed UTC).
        end : datetime
            End of the range (UTC if aware, else assumed UTC).
        resolution : Resolution
            Bar resolution.
        asset_type : AssetType or None
            Optional asset type (ignored — symbol determines Yahoo routing).

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
            raise ProviderError(f"Unsupported resolution {resolution} for Yahoo")

        df = self._history(
            symbol,
            interval=interval,
            start=start,
            end=end,
            auto_adjust=self._auto_adjust,
            actions=False,
            prepost=False,
        )

        if df is None or df.empty:
            logger.info("provider.empty", provider="yahoo", symbol=symbol)
            return empty_ohlcv()

        result = _normalise(df)
        logger.info(
            "provider.fetched",
            provider="yahoo",
            symbol=symbol,
            rows=len(result),
        )
        return result

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

        yfinance scrapes Yahoo's public endpoints and is free, so the cost is
        always ``0.0``. Implemented so ``MarketDataFetcher``'s cost ceiling
        treats Yahoo as free rather than skipping the check.
        """
        return 0.0

    def get_instrument_bounds(self, symbol: str) -> dict[str, datetime | None]:
        """Resolve a symbol's listing date via a single full-history daily probe.

        Fetches the complete daily history (``period="max"``, ``interval="1d"``)
        and uses its first timestamp as the listing date, so gap detection does
        not try to backfill a symbol before it existed. Yahoo exposes no clean
        delisting signal, so ``delist_date`` is always None. On any error
        (unknown symbol, network), returns ``{None, None}`` so the metadata
        bootstrap falls back to discovery.
        """
        try:
            df = self._history(symbol, period=_MAX_PERIOD_DAILY, interval="1d")
        except ProviderError as exc:
            logger.warning(
                "provider.bounds_failed",
                provider="yahoo",
                symbol=symbol,
                error=str(exc),
            )
            return {"ipo_date": None, "delist_date": None}

        if df is None or df.empty:
            return {"ipo_date": None, "delist_date": None}

        first = df.index[0]
        ts = pd.Timestamp(first)
        if ts.tz is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return {"ipo_date": ts.to_pydatetime(), "delist_date": None}


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Convert a raw yfinance DataFrame to the canonical OHLCV schema.

    Yahoo returns capitalised columns (``Open``, ``High``, ...) plus an
    ``Adj Close`` column when ``auto_adjust=False`` and ``Dividends`` /
    ``Stock Splits`` when ``actions=True``; only the five OHLCV columns are
    retained. The index is normalised to UTC (Yahoo returns tz-naive dates for
    daily data and tz-aware exchange-local timestamps for intraday).
    Duplicate timestamps and ordering drift are removed.
    """
    result = df.rename(
        columns={k: v for k, v in _YF_COLUMN_MAP.items() if k in df.columns}
    )
    keep = [c for c in OHLCV_COLUMNS if c in result.columns]
    result = result[keep]

    idx = result.index
    if isinstance(idx, pd.DatetimeIndex):
        if idx.tz is None:
            result.index = idx.tz_localize("UTC")
        elif str(idx.tz) != "UTC":
            result.index = idx.tz_convert("UTC")

    result = result[~result.index.duplicated(keep="first")].sort_index()
    result.index.name = None
    return result
