"""Download 1m equity data from Databento (ARCX.PILLAR).

Usage:
    uv run python scripts/download_symbols.py SLV 2024-01-01 2024-01-31
    uv run python scripts/download_symbols.py SLV,AAPL,META 2024-01-01 2024-06-01 --delete
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import fin3.providers.databento  # register provider
from fin3.config.settings import ClientConfig
from fin3.core import MarketDataFetcher
from fin3.schemas import AssetType, Resolution
from fin3.storage.arctic import ArcticStorage


def main() -> None:
    parser = argparse.ArgumentParser(description="Download 1m equity bars from Databento")
    parser.add_argument("symbols", help="Comma-separated symbols (e.g. SLV,AAPL,META)")
    parser.add_argument("start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--delete", action="store_true", help="Delete existing data before downloading")
    parser.add_argument("--max-cost", type=float, default=None, help="Abort if estimated cost exceeds this USD amount")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    config = ClientConfig()
    storage = ArcticStorage(config.minio)

    lib_name = "equities-1m-databento"

    if args.delete:
        for symbol in symbols:
            if storage.has_symbol(lib_name, symbol):
                storage.delete_symbol(lib_name, symbol)
                print(f"Deleted {symbol} from {lib_name}")
            else:
                print(f"{symbol} not found in {lib_name}, skipping delete")

    fetcher = MarketDataFetcher(config)

    print(f"Downloading {symbols} {start.date()} to {end.date()}...")
    df = fetcher.get_data(
        asset_type=AssetType.EQUITY_US,
        provider="databento",
        resolution=Resolution.ONE_MINUTE,
        symbols=symbols,
        start=start,
        end=end,
        max_cost=args.max_cost,
    )

    for symbol in symbols:
        if symbol in df.columns.get_level_values(0):
            sym_df = df[symbol]
            total = len(sym_df)
            null_bars = sym_df["close"].isna().sum()
            print(f"{symbol}: {total} rows, {null_bars} null bars ({null_bars / total * 100:.1f}%)")
        else:
            print(f"{symbol}: no data returned")

    print(f"\nTotal rows: {len(df)}")


if __name__ == "__main__":
    main()
