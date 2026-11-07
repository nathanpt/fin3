"""Asset lifecycle metadata store (IPO / delisting dates)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog

from fin3.calendar.exchange import CalendarStrategy
from fin3.schemas import METADATA_LIBRARY, Resolution

logger = structlog.get_logger(__name__)

METADATA_SYMBOL_PREFIX = "__meta__"

_DISCOVERY_PROBE_DAYS = 8


def _meta_symbol(symbol: str) -> str:
    return f"{METADATA_SYMBOL_PREFIX}{symbol}"


class MetadataStore:
    """Stores per-symbol lifecycle metadata in the ``fin3.metadata`` ArcticDB library."""

    def __init__(self, storage: Any) -> None:
        """Initialise the metadata store.

        Parameters
        ----------
        storage : ArcticStorage
            Storage backend used to access the metadata ArcticDB library.
        """
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

    def invalidate(self, symbol: str) -> None:
        """Remove cached lifecycle bounds so the next bootstrap re-resolves.

        Repairs cached ``ipo_date``/``delist_date`` values that are known or
        suspected to be wrong (e.g. persisted by older discovery-derived
        bootstrap runs). After invalidation the next ``bootstrap_metadata``
        call re-runs discovery under the current persistence rules; a
        symbol with no cached entry is a no-op.
        """
        if self.get_lifecycle_bounds(symbol) is None:
            return
        self._storage.delete_symbol(METADATA_LIBRARY, _meta_symbol(symbol))
        logger.info("metadata.invalidated", symbol=symbol)

    def bootstrap_metadata(
        self,
        symbol: str,
        provider: Any,
        start: datetime,
        end: datetime,
        *,
        resolution: Any | None = None,
        calendar: CalendarStrategy | None = None,
    ) -> tuple[datetime | None, datetime | None]:
        """Resolve lifecycle bounds via cache -> provider ref API -> discovery fetch.

        Returns (ipo_date, delist_date). Either may be None.

        The discovery fetch cannot freely probe the provider's full history,
        so a first observation that lands on the window's first expected bar
        does NOT prove a listing date — the symbol may simply extend earlier.
        Such observations are recorded as None instead of being persisted as
        a false ``ipo_date``. Only a first observation strictly inside the
        window (after the first expected bar) is persisted, because data
        demonstrably begins mid-window there. Pass *calendar* (the asset
        type's strategy) so non-session window starts (weekends, holidays)
        compare against the first real session rather than the raw start.
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
                fetch_kwargs: dict[str, Any] = {
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                }
                if resolution is not None:
                    fetch_kwargs["resolution"] = resolution
                discovery_df = provider.fetch(**fetch_kwargs)
                if discovery_df is not None and not discovery_df.empty:
                    first_bar = discovery_df.index[0].to_pydatetime()
                    reference = self._first_expected_bar(
                        calendar, start, resolution
                    )
                    if first_bar > reference:
                        ipo_date = first_bar
                        logger.info(
                            "metadata.discovery", symbol=symbol, ipo_date=ipo_date
                        )
                    else:
                        logger.info(
                            "metadata.discovery_inconclusive",
                            symbol=symbol,
                            first_observation=first_bar,
                            first_expected_bar=reference,
                            note="window reaches the symbol's earliest expected "
                            "bar; listing date not derivable, not persisted",
                        )
                else:
                    logger.info("metadata.symbol_not_found", symbol=symbol)
            except Exception:
                logger.warning("metadata.discovery_failed", symbol=symbol)

        self.set_lifecycle_bounds(symbol, ipo_date, delist_date)
        return ipo_date, delist_date

    @staticmethod
    def _first_expected_bar(
        calendar: CalendarStrategy | None,
        start: datetime,
        resolution: Any | None,
    ) -> datetime:
        """Return the window's first expected bar timestamp.

        With a calendar, this is the first session bar at or after *start*
        (so weekend/holiday window starts compare against the next real
        session, not the raw date). Without one, or when the calendar
        cannot answer, the raw *start* is used — conservative: a
        window-aligned first observation then stays un-persisted.
        """
        reference: datetime = start
        if calendar is not None:
            try:
                res = (
                    resolution if isinstance(resolution, Resolution) else Resolution.ONE_DAY
                )
                probe_end = pd.Timestamp(start) + pd.Timedelta(
                    days=_DISCOVERY_PROBE_DAYS
                )
                grid = calendar.generate_grid(pd.Timestamp(start), probe_end, res)
                if len(grid) > 0:
                    reference = grid[0].to_pydatetime()
            except Exception:
                logger.debug(
                    "metadata.first_session_probe_failed", fallback="raw start"
                )
        return reference
