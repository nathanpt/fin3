"""Download equity bar data from Databento.

Usage:
    uv run python scripts/download_symbols.py SLV 2024-01-01 2024-01-31
    uv run python scripts/download_symbols.py SLV,AAPL,META 2024-01-01 2024-06-01 --delete
    uv run python scripts/download_symbols.py AAPL,MSFT 2018-05-01 2026-05-27 --resolution 1d
    uv run python scripts/download_symbols.py --symbols-file symbols.txt 2024-01-01 2024-06-01

Symbols file format: one symbol per line, or comma-separated, or any mix.
Blank lines and lines starting with '#' are ignored.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from fin3.config.settings import ClientConfig
from fin3.core import MarketDataFetcher
from fin3.schemas import AssetType, Resolution
from fin3.storage.arctic import ArcticStorage

import fin3.providers.databento  # noqa: F401  # register provider


def parse_symbols(text: str) -> list[str]:
    """Parse symbols from newline- and/or comma-separated text.

    Blank lines and ``#`` comments are ignored. Whitespace is trimmed.
    """
    symbols: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        for tok in line.split(","):
            tok = tok.strip()
            if tok:
                symbols.append(tok.upper())
    # Preserve order, drop duplicates
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Download equity bars from Databento")
    parser.add_argument(
        "symbols", nargs="?", default=None,
        help="Comma-separated symbols (e.g. SLV,AAPL,META). Mutually exclusive with --symbols-file.",
    )
    parser.add_argument(
        "--symbols-file", default=None,
        help="Path to a file of symbols (one per line, or comma-separated). "
             "Mutually exclusive with the positional symbols argument.",
    )
    parser.add_argument("start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--resolution", default="1m",
        choices=["1m", "5m", "15m", "1h", "4h", "1d"],
        help="Bar resolution (default: 1m)",
    )
    parser.add_argument("--delete", action="store_true", help="Delete existing data before downloading")
    parser.add_argument("--max-cost", type=float, default=None, help="Abort if estimated cost exceeds this USD amount")
    args = parser.parse_args()

    if args.symbols is not None and args.symbols_file is not None:
        parser.error("Provide either positional symbols or --symbols-file, not both.")
    if args.symbols is None and args.symbols_file is None:
        parser.error("One of positional symbols or --symbols-file is required.")

    if args.symbols_file is not None:
        try:
            with open(args.symbols_file) as f:
                file_text = f.read()
        except OSError as exc:
            print(f"Error reading --symbols-file {args.symbols_file!r}: {exc}", file=sys.stderr)
            sys.exit(2)
        symbols = parse_symbols(file_text)
        if not symbols:
            parser.error(f"No symbols found in {args.symbols_file!r}.")
    else:
        symbols = parse_symbols(args.symbols)

    resolution = Resolution(args.resolution)
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    config = ClientConfig()
    storage = ArcticStorage(config.minio)

    lib_name = f"equities-{resolution.value}-databento"

    if args.delete:
        for symbol in symbols:
            if storage.has_symbol(lib_name, symbol):
                storage.delete_symbol(lib_name, symbol)
                print(f"Deleted {symbol} from {lib_name}")
            else:
                print(f"{symbol} not found in {lib_name}, skipping delete")

    fetcher = MarketDataFetcher(config)

    print(f"Downloading {symbols} {start.date()} to {end.date()} ({resolution.value})...")
    df = fetcher.get_data(
        asset_type=AssetType.EQUITY_US,
        provider="databento",
        resolution=resolution,
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
