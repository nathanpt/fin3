# fin3

Declarative financial time-series data fetching, storage, and retrieval. Uses ArcticDB on MinIO for durable storage and supports multiple data providers (Databento, Polygon, Binance).

## Data Storage

All OHLCV data is stored in [ArcticDB](https://arcticdb.io/) libraries backed by MinIO (S3-compatible). Each library corresponds to a single bucket.

### Library Naming

Libraries follow the convention `{asset_prefix}-{resolution}-{provider}`:

| Library | Asset | Resolution | Provider |
|---|---|---|---|
| `equities-1m-databento` | US Equities | 1 minute | Databento |
| `equities-1h-databento` | US Equities | 1 hour | Databento |
| `equities-1d-databento` | US Equities | 1 day | Databento |

### Canonical OHLCV Schema

The normalized schema that fin3 expects and writes:

```
Index:  DatetimeIndex, tz=UTC, unnamed
Columns:
  open     float64   — Opening price
  high     float64   — Highest price
  low      float64   — Lowest price
  close    float64   — Closing price
  volume   float64   — Trade volume (0 for calendar-filled bars)
```

- All prices are float64. Volume is float64 (allows NaN filling).
- The index is always UTC. No timezone-naive data.
- Missing bars within a trading session are filled with `volume=0, OHLC=NaN` when reindexed against the trading calendar.
- Symbols are uppercase tickers (e.g. `AAPL`, `SPY`). One symbol per ArcticDB symbol within a library.

### Timestamps

| Resolution | Timestamp convention |
|---|---|
| 1m, 5m, 15m | Bar start time in UTC, aligned to NYSE session (14:30–20:00 UTC) |
| 1h | Bar start time in UTC |
| 1d | Midnight UTC (00:00:00+00:00) |

Intraday bars are aligned to the [NYSE trading calendar](https://www.nyse.com/markets/hours-calendars). Pre-market and post-market bars are excluded.

### Raw Databento Format (pre-normalization)

Data ingested directly from Databento parquet files arrives in a different schema. The `scripts/normalize_databento.py` script converts it to the canonical format:

```
Raw columns:     Rtype, Publisher_id, Instrument_id, Open, High, Low, Close, Volume, Symbol, VWAP, Transactions
Normalized:      open, high, low, close, volume
Index change:    tz-naive → UTC localized
Column change:   Capitalized → lowercase, 6 extra columns dropped
```

The six dropped columns are Databento metadata (`Rtype`, `Publisher_id`, `Instrument_id`, `Symbol`, `VWAP`, `Transactions`) and are not used by fin3.

### Calendar Alignment

Intraday data is reindexed against the NYSE trading calendar using `exchange_calendar` via `ExchangeCalendarStrategy("XNYS")`. This fills gaps in the trading session (e.g. no-trade minutes) with `volume=0, OHLC=NaN` bars so that every minute within market hours has a row.

Daily data is **not** reindexed against the calendar — timestamps are stored as-is from the provider.

### Inspecting Data

Use the inspection script to generate an HTML report of a library's contents:

```bash
uv run python scripts/inspect_library.py equities-1m-databento --resolution 1m --output .
```

This produces a dark-themed HTML page with summary cards and a per-symbol table showing bar counts, date ranges, null bars, storage size, and health status.

### Auditing Data Integrity

Use the audit script to run a full bar-level integrity check against the trading calendar and generate an HTML dashboard:

```bash
uv run python scripts/audit_library.py equities-1m-databento --resolution 1m
```

This produces a `{library}-audit.html` file with:

- **Summary cards** — symbol count, total rows, storage size, date range, null bar percentage, issue count
- **Health distribution** — symbols categorized as clean (0% null), good (<1%), warning (1-5%), or critical (>5%)
- **Issue findings** — each issue type with severity, description, and affected symbols
- **Per-symbol table** — bar counts, null bars, storage size, issue counts, and health badges

The audit checks for: resolution mismatches, missing/extra bars against the calendar grid, duplicate timestamps, monotonicity, NaN/negative volume, NaN semantics inconsistencies, OHLCV constraint violations, and negative prices.

### Normalizing Data

To convert raw Databento data to the canonical schema:

```bash
# Dry run first
uv run python scripts/normalize_databento.py --library equities-1m-databento --resolution 1m --dry-run

# Normalize all symbols
uv run python scripts/normalize_databento.py --library equities-1m-databento --resolution 1m

# Single symbol
uv run python scripts/normalize_databento.py --library equities-1d-databento --resolution 1d --symbol AAPL
```

**Warning:** Normalization overwrites data in place with `prune_previous_versions=True`. There is no undo. Always run with `--dry-run` first.
