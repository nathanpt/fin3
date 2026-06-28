"""Shared data schemas, enums, and constants."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from fin3.calendar.exchange import CalendarStrategy

OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
"""Tuple of canonical OHLCV column names used throughout fin3."""


def empty_ohlcv() -> pd.DataFrame:
    """Return an empty OHLCV DataFrame with the canonical schema."""
    return pd.DataFrame(
        columns=list(OHLCV_COLUMNS),
        index=pd.DatetimeIndex([], tz="UTC"),
    )


class AssetType(str, Enum):
    """Financial asset type used for calendar selection and schema routing.

    Each asset type maps to a calendar strategy (NYSE, CME, or 24/7 continuous)
    and a MIC code used for exchange-calendar alignment.
    """

    EQUITY_US = "equity_us"
    CRYPTO = "crypto"
    FUTURES = "futures"

    @property
    def calendar_strategy(self) -> CalendarStrategy:
        """Return the appropriate calendar strategy for this asset type.

        Returns an ``ExchangeCalendarStrategy`` for exchange-traded assets
        (NYSE for equities, CME for futures) or a
        ``ContinuousCalendarStrategy`` for crypto (24/7 trading).
        """
        return _calendar_strategy(self)

    @property
    def mic_code(self) -> str | None:
        """Return the MIC code for exchange-based assets, or None for continuous."""
        mapping: dict[AssetType, str | None] = {
            AssetType.EQUITY_US: "XNYS",
            AssetType.CRYPTO: None,
            AssetType.FUTURES: "CME",
        }
        return mapping[self]


@lru_cache(maxsize=None)
def _calendar_strategy(asset_type: AssetType) -> CalendarStrategy:
    from fin3.calendar.exchange import (
        ContinuousCalendarStrategy,
        ExchangeCalendarStrategy,
    )

    mic = asset_type.mic_code
    if mic is None:
        return ContinuousCalendarStrategy()
    return ExchangeCalendarStrategy(mic)


class Resolution(str, Enum):
    """OHLCV bar resolution / aggregation interval.

    Maps to timedelta aliases accepted by ``pandas.date_range``.
    Used throughout fin3 for calendar grid generation and library naming.
    """

    ONE_MINUTE = "1m"
    FIVE_MINUTE = "5m"
    FIFTEEN_MINUTE = "15m"
    ONE_HOUR = "1h"
    FOUR_HOUR = "4h"
    ONE_DAY = "1d"

    @property
    def timedelta_alias(self) -> str:
        """Return the pandas-compatible timedelta alias (e.g. ``1min``, ``1h``, ``1D``)."""
        mapping: dict[Resolution, str] = {
            Resolution.ONE_MINUTE: "1min",
            Resolution.FIVE_MINUTE: "5min",
            Resolution.FIFTEEN_MINUTE: "15min",
            Resolution.ONE_HOUR: "1h",
            Resolution.FOUR_HOUR: "4h",
            Resolution.ONE_DAY: "1D",
        }
        return mapping[self]


def library_name(asset_type: AssetType, resolution: Resolution, provider: str) -> str:
    """Build the ArcticDB library/bucket name: ``{asset_type}-{resolution}-{provider}``.

    Convention matches existing MinIO buckets: ``equities-1m-databento``,
    ``crypto-tick-databento``, etc.
    """
    _ASSET_PREFIX: dict[AssetType, str] = {
        AssetType.EQUITY_US: "equities",
        AssetType.CRYPTO: "crypto-tick",
        AssetType.FUTURES: "futures",
    }
    prefix = _ASSET_PREFIX.get(asset_type, asset_type.value)
    return f"{prefix}-{resolution.value}-{provider}"


METADATA_LIBRARY = "fin3-metadata"
"""Name of the ArcticDB library used for per-symbol lifecycle metadata."""
