"""Abstract base class for data providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from fin3.schemas import Resolution

if TYPE_CHECKING:
    from fin3.schemas import AssetType


class DataProvider(ABC):
    @abstractmethod
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
        """Fetch OHLCV data for a single symbol.

        Must return a DataFrame with columns (open, high, low, close, volume)
        and a UTC DatetimeIndex. Return an empty DataFrame (correct columns,
        zero rows) when no data exists for the range. Never return None.
        """
        ...
