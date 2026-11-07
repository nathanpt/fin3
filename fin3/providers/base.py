"""Abstract base classes for data providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import TYPE_CHECKING, Sequence

import pandas as pd

from fin3.schemas import Resolution

if TYPE_CHECKING:
    from fin3.schemas import AssetType


class DataProvider(ABC):
    """Abstract base class for market data providers.

    Subclasses implement ``fetch()`` to return OHLCV DataFrames with a UTC
    DatetimeIndex and columns matching ``OHLCV_COLUMNS``. Providers are
    registered with the ``ProviderRegistry`` via the ``@register`` decorator.
    """
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


class ChainProvider(ABC):
    """Abstract base class for options chain providers.

    Chain providers return normalized chain DataFrames indexed by a sorted,
    unique ``("as_of", "contract")`` MultiIndex with exactly
    ``CHAIN_COLUMNS`` — never OHLCV frames. They are registered under their
    own registry names and resolved via ``ProviderRegistry.get_chain``;
    ``ProviderRegistry.get`` rejects them so bar pipelines cannot silently
    consume chain data.
    """

    @abstractmethod
    def fetch_history(
        self,
        root: str,
        start: date,
        end: date,
        *,
        contracts: Sequence[str] | None = None,
        max_requests: int | None = None,
    ) -> pd.DataFrame:
        """Fetch normalized chain rows for *root* over inclusive [start, end].

        With ``contracts=None`` the full enumerated reference inventory of
        the root is extracted (bounded by the provider's request budget).
        With an explicit ``contracts`` selection only those contracts'
        histories are retrieved. Raises ``RequestLimitExceededError`` when
        the per-run request cap would be exceeded.
        """
        ...

    def fetch_chain(
        self, root: str, as_of: date, *, max_requests: int | None = None
    ) -> pd.DataFrame:
        """Fetch the chain for *root* as observed on *as_of* (single date)."""
        return self.fetch_history(root, as_of, as_of, max_requests=max_requests)
