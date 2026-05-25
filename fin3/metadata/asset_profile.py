"""Asset lifecycle metadata store (IPO / delisting dates)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog

from fin3.schemas import METADATA_LIBRARY

logger = structlog.get_logger(__name__)

METADATA_SYMBOL_PREFIX = "__meta__"


def _meta_symbol(symbol: str) -> str:
    return f"{METADATA_SYMBOL_PREFIX}{symbol}"


class MetadataStore:
    """Stores per-symbol lifecycle metadata in the ``fin3.metadata`` ArcticDB library."""

    def __init__(self, storage: Any) -> None:
        self._storage = storage

    def get_lifecycle_bounds(self, symbol: str) -> dict[str, Any] | None:
        """Return cached lifecycle bounds, or None on cache miss."""
        df = self._storage.read(METADATA_LIBRARY, _meta_symbol(symbol))
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        return {
            "ipo_date": row.get("ipo_date"),
            "delist_date": row.get("delist_date"),
            "discovered_at": row.get("discovered_at"),
        }

    def set_lifecycle_bounds(
        self,
        symbol: str,
        ipo_date: datetime | None = None,
        delist_date: datetime | None = None,
    ) -> None:
        """Write lifecycle bounds to metadata library."""
        meta_symbol = _meta_symbol(symbol)
        record = pd.DataFrame(
            [
                {
                    "ipo_date": ipo_date,
                    "delist_date": delist_date,
                    "discovered_at": datetime.now(timezone.utc),
                }
            ]
        )
        self._storage.write(METADATA_LIBRARY, meta_symbol, record)

    def bootstrap_metadata(
        self,
        symbol: str,
        provider: Any,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime | None, datetime | None]:
        """Resolve lifecycle bounds via cache -> provider ref API -> discovery fetch.

        Returns (ipo_date, delist_date). Either may be None.
        """
        cached = self.get_lifecycle_bounds(symbol)
        if cached is not None:
            logger.debug("metadata.cache_hit", symbol=symbol)
            return cached.get("ipo_date"), cached.get("delist_date")

        logger.info("metadata.cache_miss", symbol=symbol, action="bootstrap")

        ipo_date: datetime | None = None
        delist_date: datetime | None = None

        if hasattr(provider, "get_instrument_bounds"):
            try:
                bounds = provider.get_instrument_bounds(symbol)
                ipo_date = bounds.get("ipo_date")
                delist_date = bounds.get("delist_date")
                logger.info(
                    "metadata.provider_ref",
                    symbol=symbol,
                    ipo_date=ipo_date,
                    delist_date=delist_date,
                )
            except Exception:
                logger.warning(
                    "metadata.provider_ref_failed",
                    symbol=symbol,
                    action="discovery_fallback",
                )

        if ipo_date is None:
            try:
                discovery_df = provider.fetch(symbol=symbol, start=start, end=end)
                if discovery_df is not None and not discovery_df.empty:
                    ipo_date = discovery_df.index[0].to_pydatetime()
                    logger.info("metadata.discovery", symbol=symbol, ipo_date=ipo_date)
                else:
                    logger.info("metadata.symbol_not_found", symbol=symbol)
            except Exception:
                logger.warning("metadata.discovery_failed", symbol=symbol)

        self.set_lifecycle_bounds(symbol, ipo_date, delist_date)
        return ipo_date, delist_date
