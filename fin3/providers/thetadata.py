"""ThetaData (thetadata SDK) data provider implementation."""

from __future__ import annotations

import time
from datetime import date, datetime, time as dtime
from typing import Any

import pandas as pd
import structlog

from fin3.config.settings import ThetaDataConfig
from fin3.exceptions import (
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from fin3.providers import ProviderRegistry
from fin3.providers.base import DataProvider
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, empty_ohlcv

logger = structlog.get_logger(__name__)

_INTRADAY_INTERVAL: dict[Resolution, str] = {
    Resolution.ONE_MINUTE: "1m",
    Resolution.FIVE_MINUTE: "5m",
    Resolution.FIFTEEN_MINUTE: "15m",
    Resolution.ONE_HOUR: "1h",
    # ThetaData has no native 4h interval: fetch 1h and let
    # core._aggregate_bars roll the finer bars up to 4h (yfinance pattern).
    Resolution.FOUR_HOUR: "1h",
}
"""Maps fin3 intraday resolutions to ThetaData ``interval`` strings.

``ONE_DAY`` is intentionally absent — it routes to the daily branch."""

_RTH_START = dtime(9, 30)  # NYSE regular session open (ET)
_RTH_END = dtime(16, 0)  # NYSE regular session close (ET)

# Candidate timestamp column names ThetaData has used across its endpoints
# (EOD tends toward ``"date"``; intraday toward ``"ms_of_day"``/``"ms_of_date"``).
# The SDK converts these server-side to Python ``datetime`` objects but keeps
# the original header verbatim, so detection is by name first, then by dtype.
_TS_CANDIDATES = (
    "date",
    "datetime",
    "ms_of_day",
    "ms_of_date",
    "time",
    "timestamp",
)


def _classify_error(exc: BaseException) -> str:
    """Return ``"nodata"``, ``"rate"``, ``"timeout"``, or ``"fatal"``.

    Classifies by both exception class name and message substring to avoid
    hard-coupling to ThetaData internals (the SDK need not be importable at
    classification time). ``NoDataFoundError`` is matched by class name
    ("catch by name") so this works without importing ``thetadata.errors``.
    """
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "nodatafound" in name or "no data" in msg:
        return "nodata"
    if "ratelimit" in name or "rate" in msg or "429" in msg:
        return "rate"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    return "fatal"


@ProviderRegistry.register("thetadata")
class ThetaDataProvider(DataProvider):
    """Fetches OHLCV data from ThetaData via the official ``thetadata`` SDK.

    ThetaData authenticates via an **API key** (SDK >=1.0.9) — no Theta Terminal
    required. The v1 scope is US-equity OHLCV; ThetaData's options/Greeks/chains
    value does not map to the OHLCV schema and is deferred to a dedicated
    options phase.

    Daily bars come from ``stock_history_eod``; intraday bars come from
    ``stock_history_ohlc``, which ThetaData serves **per trading day**, so a
    multi-day intraday range issues one call per NYSE session (enumerated via
    ``exchange_calendars`` to avoid wasted calls on weekends/holidays).
    ThetaData has no native ``4h`` interval — ``4h`` requests fetch ``1h`` bars
    and rely on ``core._aggregate_bars`` to roll them up.

    Prices are stored **raw** (ThetaData OHLC is unadjusted). ThetaData is
    subscription-based (a limited free EOD tier exists), so ``estimate_cost()``
    returns ``0.0`` and the ``MarketDataFetcher`` cost ceiling is **not**
    enforced for this provider.
    """

    def __init__(self, config: ThetaDataConfig) -> None:
        """Initialise the ThetaData provider.

        Parameters
        ----------
        config : ThetaDataConfig
            ThetaData provider configuration (API key, retry policy, timeout).

        Raises
        ------
        ProviderError
            If the ``thetadata`` package is not importable. Install the optional
            extra with ``pip install fin3[thetadata]``.
        """
        try:
            import thetadata
        except Exception as exc:  # noqa: BLE001 - surface any import failure uniformly
            raise ProviderError(
                "Failed to import thetadata. Install it with "
                "`pip install fin3[thetadata]` or `uv sync --extra thetadata`."
            ) from exc
        # dataframe_type="pandas" so all returned frames are pandas (SDK default
        # is polars). The gRPC transport manages its own channel timeout, so
        # ``config.timeout`` is stored for config parity / future use.
        self._client: Any = thetadata.ThetaClient(  # type: ignore[attr-defined]
            api_key=config.api_key, dataframe_type="pandas"
        )
        self._max_retries = config.max_retries
        self._initial_backoff = config.initial_backoff
        self._max_backoff = config.max_backoff
        self._timeout = config.timeout

    def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Call an SDK method with retry/backoff on transient errors.

        Returns ``None`` for ``"nodata"`` (signalling an empty result the caller
        should skip); raises ``ProviderError`` for fatal errors; retries
        ``"rate"``/``"timeout"`` up to ``max_retries`` with exponential backoff
        before raising ``ProviderRateLimitError`` / ``ProviderTimeoutError``.
        """
        last_exc: BaseException | None = None
        for attempt in range(self._max_retries):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - classify and translate
                last_exc = exc
                kind = _classify_error(exc)
                if kind == "nodata":
                    return None
                if kind == "fatal":
                    raise ProviderError(f"ThetaData error: {exc}") from exc
                if attempt < self._max_retries - 1:
                    backoff = min(
                        self._initial_backoff * (2**attempt),
                        self._max_backoff,
                    )
                    logger.warning(
                        "provider.retry",
                        provider="thetadata",
                        attempt=attempt + 1,
                        backoff=backoff,
                        kind=kind,
                    )
                    time.sleep(backoff)
                    continue
                if kind == "rate":
                    raise ProviderRateLimitError(
                        f"ThetaData rate limit exceeded: {exc}"
                    ) from exc
                raise ProviderTimeoutError(f"ThetaData timeout: {exc}") from exc

        # Unreachable when max_retries >= 1; guarded for max_retries == 0.
        raise ProviderError(
            f"ThetaData retry loop exited without a result: {last_exc}"
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

        Returns an empty canonical DataFrame when ThetaData has no data for the
        range (including any intraday session with no trades).

        Parameters
        ----------
        symbol : str
            ThetaData-native stock ticker (e.g. ``AAPL``).
        start : datetime
            Start of the range (UTC if aware, else assumed UTC).
        end : datetime
            End of the range (UTC if aware, else assumed UTC).
        resolution : Resolution
            Bar resolution. Daily routes to the EOD endpoint; intraday routes
            to the per-session OHLC endpoint.
        asset_type : AssetType or None
            Optional asset type used to pick the trading calendar for intraday
            session enumeration. Defaults to ``EQUITY_US`` (XNYS).

        Returns
        -------
        pd.DataFrame
            OHLCV DataFrame with a UTC DatetimeIndex.

        Raises
        ------
        ProviderError
            If the resolution is unsupported or the API fails after retries.
        """
        if resolution == Resolution.ONE_DAY:
            df = self._call(
                self._client.stock_history_eod,
                symbol=symbol,
                start_date=start.date(),
                end_date=end.date(),
            )
            if df is None or df.empty:
                logger.info("provider.empty", provider="thetadata", symbol=symbol)
                return empty_ohlcv()
            result = _normalise(df)
            logger.info(
                "provider.fetched",
                provider="thetadata",
                symbol=symbol,
                rows=len(result),
            )
            return result

        interval = _INTRADAY_INTERVAL.get(resolution)
        if interval is None:
            raise ProviderError(
                f"Unsupported resolution {resolution} for ThetaData"
            )

        # Enumerate NYSE trading sessions to avoid wasted calls (and
        # NoDataFoundError churn) on weekends/holidays. exchange_calendars is
        # already a core dependency.
        from exchange_calendars import get_calendar

        mic = (asset_type or AssetType.EQUITY_US).mic_code or "XNYS"
        cal = get_calendar(mic)
        sessions = cal.sessions_in_range(start.date(), end.date())

        frames: list[pd.DataFrame] = []
        for session in sessions:
            day_df = self._call(
                self._client.stock_history_ohlc,
                symbol=symbol,
                date=session.date(),
                interval=interval,
                start_time=_RTH_START,
                end_time=_RTH_END,
            )
            if day_df is None or day_df.empty:
                continue
            frames.append(day_df)

        if not frames:
            logger.info("provider.empty", provider="thetadata", symbol=symbol)
            return empty_ohlcv()

        result = _normalise(pd.concat(frames, ignore_index=False))
        logger.info(
            "provider.fetched",
            provider="thetadata",
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

        ThetaData is subscription-based and exposes no per-query cost, so this
        always returns ``0.0``. The ``MarketDataFetcher`` ``max_cost`` ceiling
        is therefore **not** enforced for this provider. Implemented (rather
        than omitted) so the fetcher treats ThetaData uniformly.
        """
        return 0.0

    def get_instrument_bounds(self, symbol: str) -> dict[str, datetime | None]:
        """Resolve an equity's listing date via a single full-history daily probe.

        Fetches the earliest available daily bar (range spanning ``1970-01-01``
        to today) and uses its first timestamp as the listing (IPO) date so gap
        detection does not try to backfill a symbol before it existed. US
        equities have no automated delisting date from this endpoint, so
        ``delist_date`` is always None. On any error (unknown symbol, network),
        returns ``{None, None}`` so the metadata bootstrap falls back to
        discovery.

        .. note::

            On **limited plans** (including the free tier), the probe resolves
            to the plan's history boundary (~2 years EOD), **not** the symbol's
            true listing date (AAPL IPO 1980-12-12). This is still a correct
            lower bound for gap detection — it is the effective first
            *accessible* bar — but the result is plan-dependent. A paid key
            resolves a deeper (closer-to-true) listing date.
        """
        try:
            df = self._call(
                self._client.stock_history_eod,
                symbol=symbol,
                start_date=date(1970, 1, 1),
                end_date=date.today(),
            )
        except ProviderError as exc:
            logger.warning(
                "provider.bounds_failed",
                provider="thetadata",
                symbol=symbol,
                error=str(exc),
            )
            return {"ipo_date": None, "delist_date": None}
        if df is None or df.empty:
            return {"ipo_date": None, "delist_date": None}

        result = _normalise(df)
        if result.empty:
            return {"ipo_date": None, "delist_date": None}

        ipo_date = result.index[0].to_pydatetime()
        return {"ipo_date": ipo_date, "delist_date": None}


def _find_ts_column(df: pd.DataFrame) -> str | None:
    """Locate the timestamp column in a raw ThetaData DataFrame.

    The SDK returns the timestamp as a regular column (not the index) named by
    its server-side header. Detect by known candidate names first, then fall
    back to the first datetime-typed column.
    """
    lower_cols = {str(c).lower(): c for c in df.columns}
    for candidate in _TS_CANDIDATES:
        if candidate in lower_cols:
            return lower_cols[candidate]
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col
    return None


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Convert a raw ThetaData DataFrame to the canonical OHLCV schema.

    ThetaData returns OHLCV columns plus a timestamp column (header-named by the
    server; a Python ``datetime`` in UTC or America/New_York) on a plain
    RangeIndex. This collapses the timestamp to a UTC ``DatetimeIndex``, keeps
    only the five OHLCV columns, and removes duplicate timestamps / ordering
    drift so Stage-1 validation always passes.

    .. note::

        The raw header names and the timestamp column's timezone could not be
        confirmed from the SDK source (set server-side); detection is therefore
        defensive. The output invariant is fixed regardless of the input shape:
        columns == ``OHLCV_COLUMNS``, UTC ``DatetimeIndex``, deduped, sorted.
    """
    # Lowercase column names for case-insensitive OHLCV matching.
    result = df.rename(columns={str(c): str(c).lower() for c in df.columns})

    ts_col = _find_ts_column(result)
    if ts_col is None:
        # No timestamp column found — cannot build a time index.
        return empty_ohlcv()

    ts = pd.to_datetime(result[ts_col])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    else:
        ts = ts.dt.tz_convert("UTC")

    keep = [c for c in OHLCV_COLUMNS if c in result.columns]
    out = result[keep].copy()
    out.index = pd.DatetimeIndex(ts)
    out = out[~out.index.duplicated(keep="first")].sort_index()
    out.index.name = None
    return out
