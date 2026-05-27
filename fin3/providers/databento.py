"""Databento data provider implementation."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import structlog

from fin3.config.settings import (
    MAX_BACKOFF_SECONDS,
    MAX_RETRIES,
    INITIAL_BACKOFF_SECONDS,
)
from fin3.config.settings import DatabentoConfig
from fin3.exceptions import ProviderError, ProviderRateLimitError, ProviderTimeoutError
from fin3.providers import ProviderRegistry
from fin3.providers.base import DataProvider
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution
from fin3.schemas import empty_ohlcv

logger = structlog.get_logger(__name__)

_RESOLUTION_TO_SCHEMA: dict[Resolution, str] = {
    Resolution.ONE_MINUTE: "ohlcv-1m",
    Resolution.FIVE_MINUTE: "ohlcv-1m",
    Resolution.FIFTEEN_MINUTE: "ohlcv-1m",
    Resolution.ONE_HOUR: "ohlcv-1h",
    Resolution.FOUR_HOUR: "ohlcv-1h",
    Resolution.ONE_DAY: "ohlcv-1d",
}


@ProviderRegistry.register("databento")
class DatabentoProvider(DataProvider):
    """Fetches OHLCV data from Databento's timeseries API."""

    def __init__(self, config: DatabentoConfig) -> None:
        try:
            import databento as db

            self._client = db.Historical(key=config.api_key)
        except Exception as exc:
            raise ProviderError(
                f"Failed to initialise Databento client: {exc}"
            ) from exc
        self._dataset = config.dataset

    @staticmethod
    def _dataset_for(
        resolution: Resolution, asset_type: AssetType | None
    ) -> str | None:
        """Return ARCX.PILLAR for 1m US equities, None otherwise."""
        if resolution == Resolution.ONE_MINUTE and asset_type is AssetType.EQUITY_US:
            return "ARCX.PILLAR"
        return None

    @staticmethod
    def _symbol_for_dataset(symbol: str, dataset: str) -> str:
        """Convert symbol to the convention used by *dataset*.

        ARCX.PILLAR uses CMS convention (space-separated, e.g. ``BRK B``).
        """
        if dataset == "ARCX.PILLAR":
            return symbol.replace(".", " ")
        return symbol

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
        schema = _RESOLUTION_TO_SCHEMA.get(resolution)
        if schema is None:
            raise ProviderError(f"Unsupported resolution {resolution} for Databento")

        dataset = self._dataset_for(resolution, asset_type) or self._dataset
        resolved_symbol = self._symbol_for_dataset(symbol, dataset)

        for attempt in range(MAX_RETRIES):
            try:
                store = self._client.timeseries.get_range(
                    dataset=dataset,
                    symbols=resolved_symbol,
                    schema=schema,
                    start=start,
                    end=end,
                    stype_in="raw_symbol",
                    stype_out="instrument_id",
                )
                df = store.to_df()
                if df is None or df.empty:
                    return empty_ohlcv()
                return _normalise(df)
            except Exception as exc:
                error_str = str(exc).lower()
                if "data_start_after_available_end" in error_str:
                    return empty_ohlcv()
                if "429" in error_str or "rate" in error_str:
                    if attempt < MAX_RETRIES - 1:
                        backoff = min(
                            INITIAL_BACKOFF_SECONDS * (2**attempt),
                            MAX_BACKOFF_SECONDS,
                        )
                        logger.warning(
                            "provider.rate_limited",
                            provider="databento",
                            symbol=symbol,
                            backoff=backoff,
                        )
                        import time as _time

                        _time.sleep(backoff)
                        continue
                    raise ProviderRateLimitError(
                        f"Databento rate limit exceeded for {symbol}"
                    ) from exc
                if "timeout" in error_str:
                    if attempt < MAX_RETRIES - 1:
                        continue
                    raise ProviderTimeoutError(
                        f"Databento timeout for {symbol}"
                    ) from exc
                raise ProviderError(
                    f"Databento error fetching {symbol}: {exc}"
                ) from exc

        return empty_ohlcv()

    def estimate_cost(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        resolution: Resolution,
        *,
        asset_type: AssetType | None = None,
    ) -> float:
        """Query Databento for the estimated cost of a download (USD).

        Wraps ``client.metadata.get_cost()``. Returns a ``float``.
        """
        schema = _RESOLUTION_TO_SCHEMA.get(resolution)
        if schema is None:
            return 0.0

        dataset = self._dataset_for(resolution, asset_type) or self._dataset
        resolved_symbol = self._symbol_for_dataset(symbol, dataset)

        try:
            return float(
                self._client.metadata.get_cost(
                    dataset=dataset,
                    symbols=resolved_symbol,
                    schema=schema,
                    start=start,
                    end=end,
                )
            )
        except Exception as exc:
            raise ProviderError(
                f"Databento cost estimate failed for {symbol}: {exc}"
            ) from exc

    def get_instrument_bounds(self, symbol: str) -> dict[str, datetime | None]:
        """Query Databento instrument definitions for lifecycle bounds."""
        try:
            # Use a narrow date range to minimise billing cost.
            # Databento definitions are published per session; a few recent
            # trading days are sufficient to get the instrument mapping.
            store = self._client.timeseries.get_range(
                dataset=self._dataset,
                symbols=symbol,
                schema="definition",
                start="2024-01-01",
                end="2024-12-31",
                stype_in="raw_symbol",
                stype_out="instrument_id",
                limit=1,
            )
            df = store.to_df()
            if df is not None and not df.empty:
                ts = df.iloc[0].get("ts_event")
                return {
                    "ipo_date": pd.Timestamp(ts).to_pydatetime()
                    if ts is not None
                    else None,
                    "delist_date": None,
                }
        except Exception as exc:
            logger.warning(
                "databento.instrument_bounds_failed", symbol=symbol, error=str(exc)
            )
        return {"ipo_date": None, "delist_date": None}


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a Databento DataFrame to standard OHLCV schema."""
    col_map: dict[str, str] = {}
    for col in df.columns:
        if col in ("open", "high", "low", "close", "volume"):
            col_map[col] = col

    result = df.rename(columns=col_map)
    keep = [c for c in OHLCV_COLUMNS if c in result.columns]
    result = result[keep]

    if "ts_event" in result.index.names or result.index.name == "ts_event":
        pass
    elif "ts_event" in result.columns:
        result = result.set_index("ts_event")
    elif "ts_recv" in result.columns:
        result = result.set_index("ts_recv")

    idx = result.index
    if isinstance(idx, pd.DatetimeIndex):
        if idx.tz is None:
            result.index = idx.tz_localize("UTC")
        elif str(idx.tz) != "UTC":
            result.index = idx.tz_convert("UTC")

    result.index.name = None
    return result
