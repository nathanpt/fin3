"""MarketDataFetcher — main orchestrator for the fin3 library."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import structlog

from fin3.calendar.exchange import CalendarStrategy
from fin3.config.settings import ClientConfig
from fin3.exceptions import BoundaryMismatchError, CostLimitExceededError
from fin3.metadata.asset_profile import MetadataStore
from fin3.providers import ProviderRegistry
from fin3.providers.base import DataProvider
from fin3.inspect import LibraryOverview, inspect_library
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, empty_ohlcv, library_name
from fin3.storage.arctic import ArcticStorage
from fin3.storage.defrag import DefragReport, defragment_library
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
        *,
        max_cost: float | None = None,
        defrag: bool = False,
        **kwargs: object,
    ) -> pd.DataFrame:
        """Fetch OHLCV data, filling gaps from the provider as needed.

        Returns a wide-format DataFrame with ``(symbol, field)`` MultiIndex
        columns and a single aligned UTC DatetimeIndex.

        Parameters
        ----------
        max_cost : float or None
            If set, estimate total download cost before fetching and raise
            ``CostLimitExceededError`` when the estimate exceeds this limit.
        defrag : bool
            If True, defragment all symbols in the library after gap-filling.
            Useful after bulk operations that create many small segments.
        """
        _validate_inputs(asset_type, provider, resolution, symbols, start, end)

        lib_name = library_name(asset_type, resolution, provider)
        prov = self._providers.get(provider)
        strategy = asset_type.calendar_strategy

        if max_cost is not None and hasattr(prov, "estimate_cost"):
            self._check_cost(
                lib_name, symbols, prov, strategy, asset_type, resolution,
                start, end, max_cost,
            )

        for symbol in symbols:
            self._ensure_symbol(
                lib_name, symbol, prov, strategy, asset_type, resolution, start, end
            )

        if defrag:
            self.defragment(asset_type, provider, resolution, symbols=symbols)

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

    def defragment(
        self,
        asset_type: AssetType,
        provider: str,
        resolution: Resolution,
        *,
        symbols: list[str] | None = None,
        dry_run: bool = False,
    ) -> DefragReport:
        """Defragment symbols in a library to compact data segments.

        Parameters
        ----------
        asset_type : AssetType
        provider : str
        resolution : Resolution
        symbols : list[str] or None
            Symbols to defragment. None defragments all symbols in the library.
        dry_run : bool
            If True, only report fragmentation without performing compaction.

        Returns
        -------
        DefragReport
        """
        lib_name = library_name(asset_type, resolution, provider)
        return defragment_library(
            self._storage,
            lib_name,
            symbols=symbols,
            dry_run=dry_run,
        )

    def _symbol_gaps(
        self,
        lib_name: str,
        symbol: str,
        provider: DataProvider,
        asset_type: AssetType,
        resolution: Resolution,
        start: datetime,
        end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """Compute gaps for *symbol* after IPO/delist clamping."""
        ipo_date, delist_date = self._metadata.bootstrap_metadata(
            symbol, provider, start, end, resolution=resolution
        )

        eff_start, eff_end = start, end
        if ipo_date is not None:
            ipo_ts = ensure_utc(ipo_date).to_pydatetime()
            if ipo_ts > start:
                eff_start = ipo_ts
        if delist_date is not None:
            delist_ts = ensure_utc(delist_date).to_pydatetime()
            if delist_ts < end:
                eff_end = delist_ts

        if eff_start >= eff_end:
            return []

        existing = self._storage.read(
            lib_name, symbol, date_range=(eff_start, eff_end)
        )
        return detect_gaps(existing, eff_start, eff_end, asset_type, resolution)

    def _check_cost(
        self,
        lib_name: str,
        symbols: list[str],
        provider: DataProvider,
        strategy: CalendarStrategy,
        asset_type: AssetType,
        resolution: Resolution,
        start: datetime,
        end: datetime,
        max_cost: float,
    ) -> None:
        """Estimate total download cost and raise if it exceeds *max_cost*."""
        total_cost = 0.0
        for symbol in symbols:
            gaps = self._symbol_gaps(
                lib_name, symbol, provider, asset_type, resolution, start, end
            )
            for gap_start, gap_end in gaps:
                total_cost += provider.estimate_cost(  # type: ignore[attr-defined]
                    symbol=symbol,
                    start=gap_start,
                    end=gap_end,
                    resolution=resolution,
                    asset_type=asset_type,
                )

        logger.info(
            "core.cost_estimate",
            total_cost=total_cost,
            max_cost=max_cost,
        )
        if total_cost > max_cost:
            raise CostLimitExceededError(
                f"Estimated cost ${total_cost:.2f} exceeds max_cost ${max_cost:.2f}",
                estimated_cost=total_cost,
                max_cost=max_cost,
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
        gaps = self._symbol_gaps(
            lib_name, symbol, provider, asset_type, resolution, start, end
        )

        if not gaps:
            logger.info("core.full_coverage", symbol=symbol)
            return

        logger.info("core.gaps_found", symbol=symbol, gap_count=len(gaps))

        for gap_start, gap_end in gaps:
            self._fill_gap(
                lib_name,
                symbol,
                provider,
                strategy,
                resolution,
                gap_start,
                gap_end,
                asset_type=asset_type,
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
        asset_type: AssetType | None = None,
    ) -> None:
        """Fetch, validate, reindex, and store data for a single gap."""
        raw_df = provider.fetch(
            symbol=symbol,
            start=gap_start,
            end=gap_end,
            resolution=resolution,
            asset_type=asset_type,
        )

        validate_raw_provider_data(raw_df, resolution)

        is_new = not self._storage.has_symbol(lib_name, symbol)

        if raw_df.empty and is_new:
            logger.info(
                "core.no_data_new_symbol",
                symbol=symbol,
                gap_start=gap_start.isoformat(),
                gap_end=gap_end.isoformat(),
            )
            return

        grid = strategy.generate_grid(
            ensure_utc(gap_start),
            ensure_utc(gap_end),
            resolution,
        )

        # Providers may return bars at timestamps that don't align with the
        # calendar grid (e.g. daily bars at midnight UTC vs market-open,
        # or hourly bars at whole-hour UTC vs market-open offsets).
        # Snap the raw data to the grid's timestamps so reindexing aligns.
        if not raw_df.empty and len(grid) > 0:
            raw_df = _snap_to_grid(raw_df, grid, resolution)

        reindexed = _reindex(raw_df, grid)

        validate_storage_artifact(reindexed, resolution)

        _assert_boundary(reindexed, grid)

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
        result["volume"] = 0.0
        for col in ("open", "high", "low", "close"):
            result[col] = float("nan")
        return result

    cols = [c for c in OHLCV_COLUMNS if c in df.columns]
    df = df[cols].copy()

    reindexed = df.reindex(grid)
    reindexed["volume"] = reindexed["volume"].fillna(0).astype(float)
    for col in ("open", "high", "low", "close"):
        if col in reindexed.columns:
            reindexed[col] = reindexed[col].astype(float)

    return reindexed


def _snap_to_grid(df: pd.DataFrame, grid: pd.DatetimeIndex, resolution: Resolution) -> pd.DataFrame:
    """Snap provider timestamps to the calendar grid.

    Providers return bars at timestamps that may not match the grid convention:
    - Daily: midnight UTC -> market-open (e.g. 14:30 UTC for NYSE)
    - Intraday: whole-hour UTC -> market-open offsets (e.g. 14:30, 15:30, ...)
    """
    df = df.copy()

    if resolution == Resolution.ONE_DAY:
        # Exact date-based mapping for daily bars
        grid_dates = {ts.normalize(): ts for ts in grid}
        new_index = []
        for ts in df.index:
            normalized = ts.tz_convert("UTC").normalize() if ts.tz is not None else pd.Timestamp(ts).tz_localize("UTC").normalize()
            grid_ts = grid_dates.get(normalized)
            new_index.append(grid_ts if grid_ts is not None else ts)
        df.index = pd.DatetimeIndex(new_index)
        return df

    # Intraday: nearest-neighbor alignment with tolerance
    median_spacing = pd.Series(grid).diff().dropna().median()
    tolerance = median_spacing / 2

    grid_idx = grid.get_indexer(df.index, method="nearest", tolerance=tolerance)
    new_index = []
    used_grid_positions: set[int] = set()
    for i, pos in enumerate(grid_idx):
        pos_int = int(pos)
        if pos_int == -1 or pos_int in used_grid_positions:
            # No nearby grid point, or collision — keep original timestamp
            # (will become a null row after reindex)
            new_index.append(df.index[i])
        else:
            new_index.append(grid[pos_int])
            used_grid_positions.add(pos_int)

    df.index = pd.DatetimeIndex(new_index)
    # Drop duplicate index entries (keep first) to avoid reindex issues
    df = df[~df.index.duplicated(keep="first")]
    return df


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
