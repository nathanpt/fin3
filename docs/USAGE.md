# fin3 Internal Usage Guide

> This doc is for developers working on fin3 and consuming it from other repos during development. Not for public distribution.

## Install in Another Repo (Development)

fin3 isn't on PyPI yet. Install it locally or from git.

### Editable install (recommended during active dev)

Changes to fin3 source are reflected immediately — no reinstall needed.

```bash
# From your backtesting repo:
uv pip install -e /path/to/fin3
# or with the databento extra:
uv pip install -e "/path/to/fin3[databento]"
```

### From git

```bash
uv pip install "git+https://github.com/yourname/fin3.git"
# with extra:
uv pip install "fin3[databento] @ git+https://github.com/yourname/fin3.git"
```

### As a dependency in pyproject.toml

```toml
[project]
dependencies = [
    "fin3[databento] @ git+https://github.com/yourname/fin3.git",
]
```

When fin3 is published to PyPI later, swap to just `"fin3[databento]"`.

---

## Prerequisites

- **MinIO** running locally or accessible over the network (ArcticDB's storage backend)
- A **data provider API key** — pick one or more:
    - **Databento** for institutional-grade equities/futures (paid)
    - **Massive** (formerly Polygon.io) for paid US-equity OHLCV with a free
      tier (consolidated across all NMS exchanges + dark pools + FINRA + OTC)
    - **Binance** for crypto (free, keyless public klines)
    - **Yahoo Finance** (`yfinance`) as a free, keyless alternative for US
      equities and ETFs (unofficial scraper; great for prototyping)
    - **ThetaData** (`thetadata` SDK) for US-equity OHLCV via the official SDK
      (API-key auth, SDK >=1.0.9; limited free EOD tier, paid tiers for
      intraday). Stocks-only v1 — ThetaData's options/Greeks value is deferred.

## Setup

### Configure via `.env`

Create a `.env` file in your project root:

```bash
# MinIO connection
FIN3_MINIO__ENDPOINT=localhost:9000
FIN3_MINIO__ACCESS_KEY=minioadmin
FIN3_MINIO__SECRET_KEY=minioadmin

# Provider credentials (nested delimiter is __)
FIN3_PROVIDERS__DATABENTO__PROVIDER_TYPE=databento
FIN3_PROVIDERS__DATABENTO__API_KEY=db-your-key-here

# Binance is optional and keyless by default (klines is a public endpoint).
# Set base_url to a public mirror if api.binance.com is geo-blocked.
FIN3_PROVIDERS__BINANCE__PROVIDER_TYPE=binance
# FIN3_PROVIDERS__BINANCE__BASE_URL=https://data-api.binance.vision

# Massive (formerly Polygon.io) — paid US-equity OHLCV (limited free tier).
# api.massive.com is the rebrand host; api.polygon.io runs in parallel.
FIN3_PROVIDERS__MASSIVE__PROVIDER_TYPE=massive
FIN3_PROVIDERS__MASSIVE__API_KEY=your-massive-key-here

# Yahoo Finance is keyless. Install with: pip install fin3[yfinance]
FIN3_PROVIDERS__YAHOO__PROVIDER_TYPE=yahoo
# FIN3_PROVIDERS__YAHOO__AUTO_ADJUST=false  # raw OHLC by default

# ThetaData (official thetadata SDK, API-key auth — no Theta Terminal).
# Install with: pip install fin3[thetadata]. Limited free EOD tier; paid
# tiers unlock intraday. Stocks-only v1 (options/Greeks deferred).
FIN3_PROVIDERS__THETADATA__PROVIDER_TYPE=thetadata
FIN3_PROVIDERS__THETADATA__API_KEY=your-thetadata-key-here
```

All settings use the `FIN3_` prefix with `__` as the nested delimiter, following Pydantic Settings conventions.

### Or configure in code

```python
from fin3 import ClientConfig, MarketDataFetcher
from fin3.config.settings import (
    MinioConfig,
    DatabentoConfig,
    BinanceConfig,
    YahooConfig,
    ThetaDataConfig,
)

config = ClientConfig(
    minio=MinioConfig(
        endpoint="localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    ),
    providers={
        "databento": DatabentoConfig(api_key="db-your-key-here"),
        "binance": BinanceConfig(),  # keyless public klines
        "yahoo": YahooConfig(),  # keyless; needs `pip install fin3[yfinance]`
        # "thetadata": ThetaDataConfig(api_key="your-thetadata-key"),  # needs `pip install fin3[thetadata]`
    },
)

fetcher = MarketDataFetcher(config)
```

## Fetching Data

### Single symbol, single day

```python
from datetime import datetime, timezone
from fin3 import MarketDataFetcher, AssetType, Resolution

df = fetcher.get_data(
    asset_type=AssetType.EQUITY_US,
    provider="databento",
    resolution=Resolution.ONE_MINUTE,
    symbols=["AAPL"],
    start=datetime(2024, 1, 2, tzinfo=timezone.utc),
    end=datetime(2024, 1, 2, 23, 59, tzinfo=timezone.utc),
)
```

### Multiple symbols

```python
df = fetcher.get_data(
    asset_type=AssetType.EQUITY_US,
    provider="databento",
    resolution=Resolution.ONE_HOUR,
    symbols=["AAPL", "MSFT", "GOOG"],
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 3, 31, tzinfo=timezone.utc),
)
```

### Free equity data via Yahoo Finance

For US equities and ETFs without a Databento subscription, swap
`provider="databento"` for `provider="yahoo"`. Yahoo data is free and
keyless (install the extra with `pip install fin3[yfinance]`), but unofficial
and rate-limited, so it suits prototyping and research rather than production.

```python
df = fetcher.get_data(
    asset_type=AssetType.EQUITY_US,
    provider="yahoo",
    resolution=Resolution.ONE_DAY,
    symbols=["AAPL", "MSFT", "GOOG"],
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 3, 31, tzinfo=timezone.utc),
)
```

Prices are stored **raw** by default (split/dividend-unadjusted, matching
Databento); set `YahooConfig(auto_adjust=True)` for adjusted OHLC. Note Yahoo
limits intraday history (`1m` → 7 days, `5m`–`30m` → 60 days, `60m` → 730
days); daily data is unrestricted. Yahoo has no native `4h` interval — `4h`
requests fetch `1h` bars and aggregate up.

### Paid equity data via Massive (formerly Polygon.io)

For paid, production-grade US-equity OHLCV without Databento, use
`provider="massive"`. Polygon.io rebranded to **Massive** (massive.com) on
2025-10-30; APIs, keys, and data are unchanged, and `api.massive.com` is the
rebrand host (`api.polygon.io` runs in parallel). A limited free tier exists;
paid tiers unlock deeper history. Massive consolidates across all NMS
exchanges, dark pools, FINRA, and OTC, so its bars avoid the single-venue null
problem some Databento datasets have.

```python
from fin3.config.settings import MassiveConfig

fetcher = MarketDataFetcher(
    ClientConfig(
        minio=MinioConfig(endpoint="localhost:9000", access_key="...", secret_key="..."),
        providers={"massive": MassiveConfig(api_key="your-massive-key")},
    )
)

df = fetcher.get_data(
    asset_type=AssetType.EQUITY_US,
    provider="massive",
    resolution=Resolution.ONE_DAY,
    symbols=["AAPL", "MSFT", "GOOG"],
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 3, 31, tzinfo=timezone.utc),
)
```

Prices are stored **raw** by default (`adjusted=False`, matching Databento);
set `MassiveConfig(adjusted=True)` for split/dividend-adjusted OHLC. Unlike
Yahoo, Massive supports an arbitrary bar multiplier, so `4h` maps natively to
`4×hour` (no aggregation fallback). Massive is subscription-based and exposes
no per-query cost, so `estimate_cost()` returns `0.0` and the `max_cost` ceiling
is **not** enforced for this provider.

### US-equity OHLCV via ThetaData (official SDK)

For an SDK-first US-equity OHLCV source, use `provider="thetadata"`. It uses
the official **`thetadata`** PyPI SDK (gRPC) with **API-key authentication**
(SDK >=1.0.9 — no Theta Terminal required). Install the extra with
`pip install fin3[thetadata]` (requires Python 3.12+). A limited free tier
serves recent **EOD** history; intraday (1m/5m/15m/1h) needs a paid Value tier.

```python
from fin3.config.settings import ThetaDataConfig

fetcher = MarketDataFetcher(
    ClientConfig(
        minio=MinioConfig(endpoint="localhost:9000", access_key="...", secret_key="..."),
        providers={"thetadata": ThetaDataConfig(api_key="your-thetadata-key")},
    )
)

df = fetcher.get_data(
    asset_type=AssetType.EQUITY_US,
    provider="thetadata",
    resolution=Resolution.ONE_DAY,
    symbols=["AAPL", "MSFT", "GOOG"],
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 3, 31, tzinfo=timezone.utc),
)
```

The v1 scope is **US-equity OHLCV only** — ThetaData's standout value
(options ticks, Greeks, full historical chains) does not map to the OHLCV
schema and is deferred to a dedicated options phase. Prices are **raw**
(unadjusted). Two operational notes:

- **Intraday is per-day**: ThetaData's intraday endpoint serves one trading
  day per call, so a multi-day intraday range issues one call per NYSE
  session (weekends/holidays are skipped via `exchange_calendars`). This is
  automatic — no caller action needed.
- **No native `4h`**: `4h` requests fetch `1h` bars and `core._aggregate_bars`
  rolls them up (same pattern as Yahoo).
- **Cost not enforced**: ThetaData is subscription-based, so
  `estimate_cost()` returns `0.0` and the `max_cost` ceiling is **not**
  enforced for this provider.


### Crypto (24/7 markets)

Crypto uses the continuous 24/7 calendar (no weekend/holiday gaps). Binance is
the natural provider — its public spot klines endpoint is free and needs no
API key. Symbols use the `BASE-USD` convention (`BTC-USD`), which fin3 maps to
Binance's `USDT` quote (`BTCUSDT`) automatically.

```python
df = fetcher.get_data(
    asset_type=AssetType.CRYPTO,
    provider="binance",
    resolution=Resolution.ONE_HOUR,
    symbols=["BTC-USD", "ETH-USD"],
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 6, 1, tzinfo=timezone.utc),
)
```

If `api.binance.com` is geo-blocked in your region, point `BinanceConfig` at a
public market-data mirror such as `https://data-api.binance.vision`.

### Futures (CME calendar)

```python
df = fetcher.get_data(
    asset_type=AssetType.FUTURES,
    provider="databento",
    resolution=Resolution.FIVE_MINUTE,
    symbols=["ES.n.0"],  # Databento continuous front-month symbol
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 3, 31, tzinfo=timezone.utc),
)
```

## What You Get Back

`get_data()` returns a pandas DataFrame with:

- **UTC DatetimeIndex** — aligned across all requested symbols
- **MultiIndex columns** — `(symbol, field)` where fields are `open`, `high`, `low`, `close`, `volume`

```
                         AAPL                                MSFT
                         open    high    low  close volume   open  ...
2024-01-02 09:30:00  185.30  185.45  185.21  185.41  1234  370.2
2024-01-02 09:31:00  185.41  185.50  185.35  185.44   876  370.3
...                     ...     ...     ...     ...   ...    ...
```

### Extracting a single symbol

```python
aapl = df["AAPL"]  # DataFrame with open/high/low/close/volume columns
closes = df[("AAPL", "close")]  # Series
```

### Converting for a backtesting framework

Most backtesters expect a flat DataFrame per symbol:

```python
# VectorBT-style: wide DataFrame with one column per field
aapl = df["AAPL"]

# Backtrader-style: feed each symbol separately
for symbol in df.columns.get_level_values(0).unique():
    panel = df[symbol]
    # panel has open, high, low, close, volume columns
```

## How It Works (Under the Hood)

```
get_data(symbols, start, end)
    │
    ├─ For each symbol:
    │   ├─ Bootstrap metadata (IPO/delist dates)
    │   ├─ Check ArcticDB for existing coverage
    │   ├─ Detect gaps via chunk-level comparison
    │   └─ For each gap:
    │       ├─ Fetch from provider
    │       ├─ Validate (Stage 1: raw data)
    │       ├─ Reindex to trading calendar grid
    │       ├─ Validate (Stage 2: padded artifact)
    │       └─ Write/update in ArcticDB
    │
    └─ Read from ArcticDB & return aligned MultiIndex DataFrame
```

Key behaviors:

- **Idempotent**: calling `get_data()` twice for the same range hits ArcticDB on the second call — no redundant provider fetches
- **Incremental**: if you already have Jan–Mar and request Jan–Jun, only Apr–Jun is fetched
- **Calendar-aware**: equity/futures gaps are detected per trading day; crypto gaps per hour
- **Validated**: two-stage validation catches structural issues (duplicates, bad OHLCV constraints, NaN where there shouldn't be)

## Storage Maintenance

### Defragmenting ArcticDB Symbols

Each `update(date_range=...)` can create additional ArcticDB data segments. Many
small updates can degrade read performance over time. fin3 exposes
non-mutating fragmentation inspection plus explicit defragmentation utilities for
manual or scheduled maintenance.

Dry-run first:

```bash
uv run python scripts/defragment_library.py equities-1m-databento --dry-run
```

Defragment selected symbols:

```bash
uv run python scripts/defragment_library.py equities-1m-databento --symbols AAPL,MSFT
```

Defragment from Python:

```python
from fin3 import AssetType, MarketDataFetcher, Resolution

report = fetcher.defragment(
    AssetType.EQUITY_US,
    "databento",
    Resolution.ONE_MINUTE,
    symbols=["AAPL", "MSFT"],
    dry_run=True,
)
print(report.would_defrag_count, report.failed_count)
```

`get_data(..., defrag=True)` also compacts the requested symbols after gap
filling. Use it after bulk ingestion or gap-filling jobs; do not enable it for
every routine read unless fragmentation is known to be a problem. Run
maintenance when no other ingestion process is writing to the same library.

## Concurrent Access Protection

Concurrent `get_data()` calls for the *same* symbol serialize automatically
via per-`(library, symbol)` file locks, so there is no double-fetch and no
risk of one process clobbering another's write. One process detects the gap,
fetches, and writes while the other waits; once it acquires the lock the second
process finds the gap already filled and skips the fetch entirely.

Locking is **on by default**. It is configured through the `FIN3_LOCK__*`
environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `FIN3_LOCK__ENABLED` | `true` | Master switch; set to `false` to disable all locking. |
| `FIN3_LOCK__LOCK_DIR` | `/tmp/fin3/locks` | Directory holding the per-symbol lock files. |
| `FIN3_LOCK__TIMEOUT_S` | `600` | Seconds to wait for a contended lock before raising `LockAcquisitionError`. |
| `FIN3_LOCK__POLL_INTERVAL_S` | `0.5` | Polling interval while waiting for a lock. |

Locks are advisory `flock` locks tied to an open file description, so they
**auto-release on process exit** — including crashes and `SIGKILL` — with no
stale-lock cleanup or heartbeat required. Because the lock files live on the
local filesystem, locking coordinates processes that share a single host; it
does not mediate access across separate machines (in a multi-host deployment
the storage backend itself remains the shared state).

## Resource Monitoring

fin3 automatically tracks resource usage (memory, disk, network) during every
`get_data()` call and prints a formatted summary panel on completion — no flags
or configuration needed.

### Inside tmux

When running inside a tmux session, a live monitor pane opens on the right side
of the window during the operation, showing real-time resource usage:

```
┌─ fin3 monitor ─────────────────────┐
│ Symbols     AAPL, MSFT             │
│ Resolution  1m                     │
│ Duration    42.3s                  │
│ Memory      842.0 MB peak          │
│ Disk        +128.4 MB              │
│ Net         512.0 MB (5 fetches)   │
│ Phase       fetching MSFT...       │
└────────────────────────────────────┘
```

The pane closes automatically when the operation finishes, and a final summary
is printed to the main pane.

### Native terminal (no tmux)

In a regular terminal (TTY) without tmux, a **live, inline info bar** renders
on stderr and updates in place during the operation — the same panel as the
tmux mode. Structured log lines scroll above the bar without corrupting it:

```
{"event": "core.gap_filled", "symbol": "AAPL", ...}
╭─────────────────────────── fin3 monitor ───────────────────────────╮
│  Symbols     AAPL, MSFT         Duration    42.3s                   │
│  Resolution  1m                 Memory      842.0 MB peak           │
│  Disk        +128.4 MB          Net         512.0 MB (5 fetches)    │
│  Phase       fetching MSFT...                                     │
╰────────────────────────────────────────────────────────────────────╯
```

On completion the live bar is replaced by the final summary panel.

### Piped / CI output

When stderr is not a TTY (piped, redirected, or CI), no live display is
shown — only the final summary is printed to stderr, so stdout remains clean
for piping:

```
╭─ fin3 resource summary ───────────────────────────────────────╮
│  Symbols: AAPL, MSFT        Resolution: 1m   Duration: 42.3s  │
│  Rows: 1,204,800                                              │
│  Disk:   +128.4 MB  (1.2 GB total in equities-1m-databento)  │
│  Memory: 842.0 MB peak RSS                                    │
│  Net:    512.0 MB downloaded (5 fetches)                      │
╰───────────────────────────────────────────────────────────────╯
```

### Programmatic Usage

You can use `ResourceTracker` directly for custom operations:

```python
from fin3.monitoring import ResourceTracker

with ResourceTracker(
    storage=storage,
    provider=provider,
    library="equities-1m-databento",
    symbols=["AAPL"],
    resolution=Resolution.ONE_MINUTE,
) as tracker:
    tracker.set_phase("custom work")
    # ... do work ...
    tracker.set_rows(10000)
# Summary panel printed automatically on exit
```

### What's Tracked

| Metric | Source | Description |
|--------|--------|-------------|
| Memory | `psutil` RSS | Peak process RSS delta during the operation |
| Disk | ArcticDB symbol sizes | Net size change for affected symbols + library total |
| Network | `DataFrame.memory_usage()` | Payload bytes across all provider fetch calls |
| Duration | `time.monotonic()` | Wall-clock time |
| Rows | Result DataFrame | Total rows in the returned data |

## Reference

### AssetType

| Value | Calendar | Use For |
|-------|----------|---------|
| `EQUITY_US` | NYSE trading hours | US-listed stocks, ETFs |
| `CRYPTO` | Continuous 24/7 | Crypto pairs (BTC-USD, ETH-USD) |
| `FUTURES` | CME trading hours | CME futures (ES, NQ, etc.) |

### Resolution

| Value | Bar Size |
|-------|----------|
| `ONE_MINUTE` | 1 minute |
| `FIVE_MINUTE` | 5 minutes |
| `FIFTEEN_MINUTE` | 15 minutes |
| `ONE_HOUR` | 1 hour |
| `FOUR_HOUR` | 4 hours |
| `ONE_DAY` | 1 day |

### Exception Hierarchy

```
Fin3Error
├── ConfigurationError      # Bad config, unknown provider
├── ProviderError            # Generic provider failure
│   ├── ProviderTimeoutError
│   └── ProviderRateLimitError
├── StorageError             # ArcticDB / MinIO issues
└── DataValidationError      # Data failed validation
    ├── SchemaValidationError  # Duplicates, wrong columns
    └── BoundaryMismatchError  # Reindexed data doesn't cover expected range
```

Catch all fin3 errors with `except Fin3Error`, or be specific:

```python
from fin3 import Fin3Error, ProviderError

try:
    df = fetcher.get_data(...)
except ProviderError as e:
    print(f"Provider failed: {e}")
except Fin3Error as e:
    print(f"Something else went wrong: {e}")
```

## Adding a New Provider

1. Create a class inheriting from `DataProvider`:

```python
from fin3.providers import ProviderRegistry
from fin3.providers.base import DataProvider

@ProviderRegistry.register("myprovider")
class MyProvider(DataProvider):
    def fetch(self, symbol, start, end, resolution, **kwargs):
        # Call your provider's API, return OHLCV DataFrame
        ...
```

2. Add a config model in `fin3/config/settings.py` following the existing pattern (see `MassiveConfig` / `BinanceConfig`).

3. Add the provider credentials to your `.env`:

```bash
FIN3_PROVIDERS__MYPROVIDER__PROVIDER_TYPE=myprovider
FIN3_PROVIDERS__MYPROVIDER__API_KEY=your-key
```

4. Use it:

```python
df = fetcher.get_data(
    asset_type=AssetType.EQUITY_US,
    provider="myprovider",
    ...
)
```
