"""Shared data schemas, enums, and constants."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from fin3.calendar.exchange import CalendarStrategy

OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


def empty_ohlcv() -> pd.DataFrame:
    """Return an empty OHLCV DataFrame with the canonical schema."""
    return pd.DataFrame(
        columns=list(OHLCV_COLUMNS),
        index=pd.DatetimeIndex([], tz="UTC"),
    )


class AssetType(str, Enum):
    EQUITY_US = "equity_us"
    CRYPTO = "crypto"
    FUTURES = "futures"

    @property
    def calendar_strategy(self) -> CalendarStrategy:
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
    ONE_MINUTE = "1m"
    FIVE_MINUTE = "5m"
    FIFTEEN_MINUTE = "15m"
    ONE_HOUR = "1h"
    FOUR_HOUR = "4h"
    ONE_DAY = "1d"

    @property
    def timedelta_alias(self) -> str:
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
    """Build the ArcticDB library name: ``{asset_type}-{resolution}-{provider}``."""
    return f"{asset_type.value}-{resolution.value}-{provider}"


METADATA_LIBRARY = "fin3.metadata"
