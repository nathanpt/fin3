"""MarketDataFetcher — main orchestrator for the fin3 library."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import structlog

from fin3.calendar.exchange import CalendarStrategy
from fin3.config.settings import ClientConfig
from fin3.exceptions import BoundaryMismatchError
from fin3.metadata.asset_profile import MetadataStore
from fin3.providers import ProviderRegistry
from fin3.providers.base import DataProvider
from fin3.inspect import LibraryOverview, inspect_library
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, empty_ohlcv, library_name
from fin3.storage.arctic import ArcticStorage
from fin3.utils.date_utils import detect_gaps, ensure_utc
from fin3.utils.logging import configure_logging
from fin3.utils.validation import validate_raw_provider_data, validate_storage_artifact

logger = structlog.get_logger(__name__)


class MarketDataFetcher:
    """Orchestrates data fetching, storage, validation, and retrieval."""

    def __init__(self, config: ClientConfig) -> None:
        configure_logging(level=config.log_level, format_=config.log_format)
        self._config = config
        self._storage = ArcticStorage(config.minio)
        self._providers = ProviderRegistry(config.providers)
        self._metadata = MetadataStore(self._storage)

    def get_data(
        self,
        asset_type: AssetType,
        provider: str,
        resolution: Resolution,
        symbols: list[str],
        start: datetime,
        end: datetime,
        **kwargs: object,
    ) -> pd.DataFrame:
        """Fetch OHLCV data, filling gaps from the provider as needed.

        Returns a wide-format DataFrame with ``(symbol, field)`` MultiIndex
        columns and a single aligned UTC DatetimeIndex.
        """
        _validate_inputs(asset_type, provider, resolution, symbols, start, end)

        lib_name = library_name(asset_type, resolution, provider)
        prov = self._providers.get(provider)
        strategy = asset_type.calendar_strategy

        for symbol in symbols:
            self._ensure_symbol(
                lib_name, symbol, prov, strategy, asset_type, resolution, start, end
            )

        per_symbol: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            df = self._storage.read(lib_name, symbol, date_range=(start, end))
            if df is not None:
                per_symbol[symbol] = df
            else:
                per_symbol[symbol] = empty_ohlcv()

        return _align_symbols(per_symbol)

    def inspect(
        self,
        asset_type: AssetType,
        provider: str,
        resolution: Resolution,
        *,
        include_integrity: bool = False,
    ) -> LibraryOverview:
        """Inspect a library and return per-symbol data profiles.

        Provides visibility into what data is stored: date ranges, row counts,
        null bars, and optionally integrity issue counts.

        Parameters
        ----------
        asset_type : AssetType
            Asset type (determines calendar for integrity checks).
        provider : str
            Provider name (e.g. ``"databento"``).
        resolution : Resolution
            Bar resolution.
        include_integrity : bool
            If True, run bar-level integrity checks per symbol.

        Returns
        -------
        LibraryOverview
            Per-symbol profiles and aggregated stats.
        """
        lib_name = library_name(asset_type, resolution, provider)
        strategy = asset_type.calendar_strategy
        return inspect_library(
            self._storage,
            lib_name,
            resolution,
            include_integrity=include_integrity,
            calendar_strategy=strategy,
        )

    def _ensure_symbol(
        self,
        lib_name: str,
        symbol: str,
        provider: DataProvider,
        strategy: CalendarStrategy,
        asset_type: AssetType,
        resolution: Resolution,
        start: datetime,
        end: datetime,
    ) -> None:
        """Ensure *symbol* has full coverage for [start, end] in the library."""
        ipo_date, delist_date = self._metadata.bootstrap_metadata(
            symbol, provider, start, end
        )

        eff_start = start
        eff_end = end
        if ipo_date is not None:
            ipo_ts = ensure_utc(ipo_date).to_pydatetime()
            if ipo_ts > start:
                eff_start = ipo_ts
        if delist_date is not None:
            delist_ts = ensure_utc(delist_date).to_pydatetime()
            if delist_ts < end:
                eff_end = delist_ts

        if eff_start >= eff_end:
            logger.info("core.no_valid_range", symbol=symbol)
            return

        existing = self._storage.read(lib_name, symbol, date_range=(eff_start, eff_end))
        gaps = detect_gaps(existing, eff_start, eff_end, asset_type, resolution)

        if not gaps:
            logger.info("core.full_coverage", symbol=symbol)
            return

        logger.info("core.gaps_found", symbol=symbol, gap_count=len(gaps))

        for gap_start, gap_end in gaps:
            self._fill_gap(
                lib_name, symbol, provider, strategy, resolution, gap_start, gap_end
            )

    def _fill_gap(
        self,
        lib_name: str,
        symbol: str,
        provider: DataProvider,
        strategy: CalendarStrategy,
        resolution: Resolution,
        gap_start: datetime,
        gap_end: datetime,
    ) -> None:
        """Fetch, validate, reindex, and store data for a single gap."""
        raw_df = provider.fetch(
            symbol=symbol, start=gap_start, end=gap_end, resolution=resolution
        )

        validate_raw_provider_data(raw_df, resolution)

        grid = strategy.generate_grid(
            ensure_utc(gap_start),
            ensure_utc(gap_end),
            resolution,
        )

        reindexed = _reindex(raw_df, grid)

        validate_storage_artifact(reindexed, resolution)

        _assert_boundary(reindexed, grid)

        is_new = not self._storage.has_symbol(lib_name, symbol)
        metadata = _write_metadata(symbol, provider, gap_start, gap_end)

        if is_new:
            self._storage.write(lib_name, symbol, reindexed, metadata=metadata)
        else:
            self._storage.update(
                lib_name,
                symbol,
                reindexed,
                date_range=(gap_start, gap_end),
                metadata=metadata,
            )

        logger.info(
            "core.gap_filled",
            symbol=symbol,
            gap_start=gap_start.isoformat(),
            gap_end=gap_end.isoformat(),
            rows=len(reindexed),
            operation="write" if is_new else "update",
        )


def _validate_inputs(
    asset_type: AssetType,
    provider: str,
    resolution: Resolution,
    symbols: list[str],
    start: datetime,
    end: datetime,
) -> None:
    if not symbols:
        raise ValueError("symbols must be a non-empty list")
    if start >= end:
        raise ValueError(f"start ({start}) must be before end ({end})")


def _reindex(df: pd.DataFrame, grid: pd.DatetimeIndex) -> pd.DataFrame:
    """Reindex *df* against *grid*, padding missing bars with volume=0, OHLC=NaN."""
    if df.empty:
        result = pd.DataFrame(index=grid, columns=list(OHLCV_COLUMNS))
        result["volume"] = 0
        for col in ("open", "high", "low", "close"):
            result[col] = float("nan")
        return result

    cols = [c for c in OHLCV_COLUMNS if c in df.columns]
    df = df[cols].copy()

    reindexed = df.reindex(grid)
    reindexed["volume"] = reindexed["volume"].fillna(0)
    for col in ("open", "high", "low", "close"):
        if col in reindexed.columns:
            reindexed[col] = reindexed[col].astype(float)

    return reindexed


def _assert_boundary(df: pd.DataFrame, grid: pd.DatetimeIndex) -> None:
    """Assert that reindexed data exactly covers the grid."""
    if df.empty or grid.empty:
        return
    actual_start = df.index[0]
    actual_end = df.index[-1]
    expected_start = grid[0]
    expected_end = grid[-1]
    if actual_start != expected_start or actual_end != expected_end:
        raise BoundaryMismatchError(
            f"Boundary mismatch: expected [{expected_start}, {expected_end}], "
            f"got [{actual_start}, {actual_end}]",
            expected_start=expected_start,
            expected_end=expected_end,
            actual_start=actual_start,
            actual_end=actual_end,
        )


def _align_symbols(per_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Align all symbol DataFrames to a union DatetimeIndex with MultiIndex columns."""
    if not per_symbol:
        return pd.DataFrame()

    union_index = pd.DatetimeIndex([], tz="UTC")
    for df in per_symbol.values():
        union_index = union_index.union(df.index)  # type: ignore[arg-type]

    if len(union_index) == 0:
        return pd.DataFrame()

    aligned: dict[str, pd.DataFrame] = {}
    for symbol, df in per_symbol.items():
        reindexed = df.reindex(union_index)
        reindexed.columns = pd.MultiIndex.from_product(
            [[symbol], list(reindexed.columns)]
        )
        aligned[symbol] = reindexed

    if len(aligned) == 1:
        return list(aligned.values())[0]

    return pd.concat(list(aligned.values()), axis=1).sort_index(axis=1)


def _write_metadata(
    symbol: str, provider: object, gap_start: datetime, gap_end: datetime
) -> dict[str, str]:
    return {
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "provider": type(provider).__name__,
        "symbol": symbol,
        "date_range": f"{gap_start.isoformat()}/{gap_end.isoformat()}",
    }
