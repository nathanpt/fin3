"""Fix META null gap caused by FB→META symbol rename on June 9, 2022.

META traded as FB before the rename. The fin3 library downloaded 'META' from
Databento, which only has data from 2021-06-30 and then a gap from 2022-02-01
to 2022-06-08 where the symbol traded as 'FB'.

This script downloads FB data for the gap period and updates it into the
existing META symbol.

Usage:
    uv run python scripts/fix_meta_rename.py
    uv run python scripts/fix_meta_rename.py --resolution 1d
    uv run python scripts/fix_meta_rename.py --dry-run
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from fin3.config.settings import ClientConfig
from fin3.core import _reindex, _snap_to_grid_dates
from fin3.providers.databento import DatabentoProvider
from fin3.schemas import AssetType, Resolution
from fin3.storage.arctic import ArcticStorage
from fin3.utils.date_utils import ensure_utc
from fin3.utils.validation import validate_raw_provider_data, validate_storage_artifact


GAP_START = datetime(2022, 2, 1, tzinfo=timezone.utc)
GAP_END = datetime(2022, 6, 9, tzinfo=timezone.utc)
FB_SYMBOL = "FB"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix META null gap from FB rename")
    parser.add_argument(
        "--resolution", default="1m",
        choices=["1m", "1d"],
        help="Bar resolution (default: 1m)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only, don't write")
    args = parser.parse_args()

    resolution = Resolution(args.resolution)
    library = f"equities-{resolution.value}-databento"
    symbol = "META"

    config = ClientConfig()
    storage = ArcticStorage(config.minio)
    provider = DatabentoProvider(config.providers["databento"])
    strategy = AssetType.EQUITY_US.calendar_strategy

    # Check current state
    df = storage.read(library, symbol)
    if df is None:
        print(f"ERROR: {symbol} not found in {library}")
        return

    total = len(df)
    null_before = int((df["volume"] == 0).sum())
    gap_data = df.loc[GAP_START:GAP_END]
    gap_null = int((gap_data["volume"] == 0).sum())
    gap_total = len(gap_data)

    print(f"{symbol} in {library} current state:")
    print(f"  Total bars: {total:,}")
    print(f"  Null bars:  {null_before:,} ({null_before/total*100:.1f}%)")
    print(f"  Gap period ({GAP_START.date()} to {GAP_END.date()}):")
    print(f"    Total: {gap_total:,}  Null: {gap_null:,} ({gap_null/gap_total*100:.1f}%)")

    if gap_null == 0:
        print("\nNo nulls in gap period — already fixed!")
        return

    # Fetch FB data for the gap
    print(f"\nFetching {FB_SYMBOL} data from Databento for {GAP_START.date()} to {GAP_END.date()} ({resolution.value})...")
    raw_df = provider.fetch(
        symbol=FB_SYMBOL,
        start=GAP_START,
        end=GAP_END,
        resolution=resolution,
        asset_type=AssetType.EQUITY_US,
    )

    if raw_df.empty:
        print("ERROR: No FB data returned from Databento")
        return

    print(f"  Received {len(raw_df):,} bars")

    validate_raw_provider_data(raw_df, resolution)

    # Reindex against trading calendar
    grid = strategy.generate_grid(
        ensure_utc(GAP_START),
        ensure_utc(GAP_END),
        resolution,
    )

    # Snap midnight timestamps to market-open for daily resolution
    if resolution == Resolution.ONE_DAY and not raw_df.empty and len(grid) > 0:
        raw_df = _snap_to_grid_dates(raw_df, grid)

    reindexed = _reindex(raw_df, grid)
    validate_storage_artifact(reindexed, resolution)

    data_bars = (reindexed["volume"] > 0).sum()
    print(f"  After reindex: {len(reindexed):,} bars ({int(data_bars):,} with data)")

    if args.dry_run:
        print("\n[DRY RUN] Would update META with FB data for the gap period.")
        new_gap_null = len(reindexed) - int(data_bars)
        projected_total_null = null_before - gap_null + new_gap_null
        print(f"  Projected null bars: {projected_total_null:,} ({projected_total_null/total*100:.1f}%)")
        return

    # Update storage
    print("\nUpdating META in storage...")
    storage.update(
        library,
        symbol,
        reindexed,
        date_range=(GAP_START, GAP_END),
        metadata={
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "provider": "DatabentoProvider",
            "symbol": FB_SYMBOL,
            "date_range": f"{GAP_START.isoformat()}/{GAP_END.isoformat()}",
            "note": "FB→META rename gap fill",
        },
    )

    # Verify
    df_after = storage.read(library, symbol)
    if df_after is not None:
        null_after = int((df_after["volume"] == 0).sum())
        print("\nResult:")
        print(f"  Before: {null_before:,} null bars ({null_before/total*100:.1f}%)")
        print(f"  After:  {null_after:,} null bars ({null_after/len(df_after)*100:.1f}%)")
        print(f"  Fixed:  {null_before - null_after:,} bars")


if __name__ == "__main__":
    main()
