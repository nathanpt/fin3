"""Normalize raw Databento OHLCV data stored in ArcticDB to fin3's standard schema.

Reads each symbol from the source library, normalizes column names and timezone,
reindexes against the NYSE trading calendar, validates, and writes back with
pruned previous versions.

Usage:
    uv run python scripts/normalize_databento.py [--library LIBRARY] [--dry-run]
"""

from __future__ import annotations

import argparse
import time

import pandas as pd
import structlog

from fin3.calendar.exchange import ExchangeCalendarStrategy
from fin3.config.settings import ClientConfig
from fin3.schemas import OHLCV_COLUMNS, Resolution
from fin3.storage.arctic import ArcticStorage
from fin3.utils.logging import configure_logging
from fin3.utils.integrity import check_integrity

configure_logging(level="INFO", format_="console")
logger = structlog.get_logger(__name__)

CALENDAR = ExchangeCalendarStrategy("XNYS")

# Raw Databento OHLCV columns -> fin3 standard
_COL_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
}


def normalize_symbol(
    df: pd.DataFrame, resolution: Resolution
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Normalize raw Databento DataFrame to fin3's OHLCV schema with reindexing.

    Returns (normalized_df, grid).
    """
    keep = [c for c in _COL_MAP if c in df.columns]
    if not keep:
        # Already normalized (lowercase columns)
        if all(c in df.columns for c in ("open", "high", "low", "close", "volume")):
            logger.info("Already normalized, skipping")
            return df, CALENDAR.generate_grid(df.index[0], df.index[-1], resolution)
        raise ValueError(f"No recognizable OHLCV columns found: {list(df.columns)}")

    normalized = df[keep].copy()
    normalized.rename(columns=_COL_MAP, inplace=True)

    # Localize tz-naive index to UTC
    if isinstance(normalized.index, pd.DatetimeIndex) and normalized.index.tz is None:
        normalized.index = normalized.index.tz_localize("UTC")
    normalized.index.name = None

    # Reindex against trading calendar
    first = normalized.index[0]
    last = normalized.index[-1]
    grid = CALENDAR.generate_grid(first, last, resolution)

    reindexed = normalized.reindex(grid)
    reindexed["volume"] = reindexed["volume"].fillna(0)
    for col in ("open", "high", "low", "close"):
        if col in reindexed.columns:
            reindexed[col] = reindexed[col].astype(float)

    return reindexed, grid


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize Databento OHLCV data")
    parser.add_argument(
        "--library", default="equities-1m-databento", help="ArcticDB library name"
    )
    parser.add_argument(
        "--resolution", default="1m", choices=["1m", "5m", "15m", "1h", "4h", "1d"],
        help="Bar resolution",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Normalize and validate but skip write"
    )
    parser.add_argument(
        "--symbol", default=None, help="Normalize a single symbol (default: all)"
    )
    args = parser.parse_args()

    resolution = Resolution(args.resolution)
    config = ClientConfig()
    storage = ArcticStorage(config.minio)
    lib = storage._get_or_create_library(args.library)

    symbols = [args.symbol] if args.symbol else storage.list_symbols(args.library)
    if not symbols:
        logger.info("No symbols found", library=args.library)
        return

    logger.info(
        "Starting normalization",
        library=args.library,
        resolution=resolution.value,
        symbol_count=len(symbols),
        dry_run=args.dry_run,
    )

    total_start = time.time()
    success = 0
    skipped = 0
    errors: list[tuple[str, str]] = []

    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{len(symbols)}]", symbol=symbol)
        try:
            item = lib.read(symbol)
            if item is None or item.data.empty:
                logger.warning("Skipping empty symbol", symbol=symbol)
                skipped += 1
                continue

            raw = item.data
            logger.info(
                "Read raw data",
                symbol=symbol,
                rows=len(raw),
                columns=list(raw.columns),
            )

            normalized, grid = normalize_symbol(raw, resolution)
            logger.info(
                "Normalized",
                symbol=symbol,
                rows_in=len(raw),
                rows_out=len(normalized),
            )

            # Check integrity against the calendar grid (allows inter-session gaps)
            report = check_integrity(normalized, grid, resolution)
            if not report.is_clean:
                logger.warning(
                    "Integrity issues found",
                    symbol=symbol,
                    issue_count=len(report.issues),
                    summary=report.summary,
                )

            if not args.dry_run:
                lib.write(
                    symbol,
                    normalized,
                    metadata={
                        "source": "normalize_databento",
                        "original_columns": list(raw.columns),
                        "original_rows": len(raw),
                    },
                    prune_previous_versions=True,
                )
                logger.info("Written back", symbol=symbol)

            success += 1

        except Exception as exc:
            logger.error("Failed", symbol=symbol, error=str(exc))
            errors.append((symbol, str(exc)))

    elapsed = time.time() - total_start
    logger.info(
        "Normalization complete",
        total=len(symbols),
        success=success,
        skipped=skipped,
        errors=len(errors),
        elapsed_s=f"{elapsed:.1f}",
    )
    if errors:
        logger.error("Symbols with errors", errors=errors)


if __name__ == "__main__":
    main()
