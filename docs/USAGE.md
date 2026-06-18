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
- A **data provider API key** (Databento is the only provider implemented today)

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
```

All settings use the `FIN3_` prefix with `__` as the nested delimiter, following Pydantic Settings conventions.

### Or configure in code

```python
from fin3 import ClientConfig, MarketDataFetcher
from fin3.config.settings import MinioConfig, DatabentoConfig

config = ClientConfig(
    minio=MinioConfig(
        endpoint="localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    ),
    providers={
        "databento": DatabentoConfig(api_key="db-your-key-here"),
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

### Crypto (24/7 markets)

```python
df = fetcher.get_data(
    asset_type=AssetType.CRYPTO,
    provider="databento",
    resolution=Resolution.ONE_HOUR,
    symbols=["BTC-USD"],
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 6, 1, tzinfo=timezone.utc),
)
```

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
Concurrent access protection is a separate Phase 2 concern.

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

### Outside tmux

When not in tmux, a summary panel is printed to stderr after the operation
completes:

```
╭─ fin3 resource summary ───────────────────────────────────────╮
│  Symbols: AAPL, MSFT        Resolution: 1m   Duration: 42.3s  │
│  Rows: 1,204,800                                              │
│  Disk:   +128.4 MB  (1.2 GB total in equities-1m-databento)  │
│  Memory: 842.0 MB peak RSS                                    │
│  Net:    512.0 MB downloaded (5 fetches)                      │
╰───────────────────────────────────────────────────────────────╯
```

Piped/CI output: the summary goes to stderr so stdout remains clean for piping.

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

2. Add a config model in `fin3/config/settings.py` following the existing pattern (see `PolygonConfig` / `BinanceConfig`).

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
