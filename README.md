# fin3

**Declarative financial time-series data** — fetch, store, validate, and retrieve OHLCV bars with production-grade integrity guarantees.

Built on [ArcticDB](https://arcticdb.io/) + [MinIO](https://min.io/) (S3-compatible storage). Supports multiple data providers (Databento shipping, Polygon and Binance on the roadmap).

```python
from fin3 import MarketDataFetcher, AssetType, Resolution, ClientConfig

fetcher = MarketDataFetcher(ClientConfig.from_env())

df = fetcher.get_data(
    asset_type=AssetType.EQUITY_US,
    provider="databento",
    resolution=Resolution.ONE_HOUR,
    symbols=["AAPL", "MSFT", "GOOG"],
    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end=datetime(2024, 3, 31, tzinfo=timezone.utc),
)
# Returns a clean, calendar-aligned MultiIndex DataFrame — ready for backtesting.
```

---

## Features

| Area | Status | What It Does |
|---|---|---|
| **OHLCV Schema** | ✅ Stable | Canonical `open`/`high`/`low`/`close`/`volume` schema, UTC timestamps, one symbol per ArcticDB symbol |
| **Calendar Alignment** | ✅ Stable | NYSE, CME, and 24/7 continuous calendars — intraday bars aligned to trading sessions, null bars for no-trade minutes |
| **Provider Abstraction** | ✅ Stable | Decorator-based `ProviderRegistry`, abstract `DataProvider` base |
| **Databento Provider** | ✅ Shipping | Historical MBO/P → OHLCV, cost estimation, retry/backoff, dataset selection (XNAS.ITCH, ARCX.PILLAR) |
| **Incremental Fetch** | ✅ Stable | Checks existing coverage, only fetches gaps — idempotent, no wasted API calls |
| **Two-Stage Validation** | ✅ Stable | Stage 1: raw provider data (duplicates, monotonicity). Stage 2: post-reindex NaN semantics, OHLCV constraints |
| **Integrity Audit** | ✅ Stable | 10+ vectorized checks, HTML dashboard, non-throwing `IntegrityReport` |
| **Inspection** | ✅ Stable | Per-symbol stats (bar count, date range, null bars, storage size, health), HTML reports |
| **MetadataStore** | ✅ Stable | 3-tier bootstrap (cache → provider → discovery fallback) for IPO/delist dates |
| **Cross-Process Locking** | ✅ Stable | `fcntl.flock` per `(library, symbol)`, auto-release on crash, configurable timeout |
| **Defragmentation** | ✅ Stable | Compact ArcticDB segments, dry-run mode, per-symbol reporting |
| **Resource Monitoring** | ✅ Stable | Live tmux pane, `rich.live` info bar, or CI-friendly stderr summary — auto-enabled on `get_data()` |
| **Pre-Fetch Cost Ceiling** | ✅ Stable | `max_cost` parameter raises `CostLimitExceededError` before expensive downloads |
| **Data Normalization** | ✅ Stable | Convert raw Databento parquet → canonical schema, in-place or dry-run |
| **Polygon Provider** | 🚧 Roadmap | Config model exists, provider class TBD |
| **Binance Provider** | 🚧 Roadmap | Config model + crypto infrastructure exist, provider class TBD |
| **Declarative Manifest** | 🚧 Roadmap | YAML-based dataset definitions, `fin3 ensure` / `fin3 sync` |
| **Retrieval API** | 🚧 Roadmap | High-level `DataManager` with alignment, resampling, multi-format output |
| **CLI** | 🚧 Roadmap | Unified `fin3` command (scripts exist individually) |

---

## Quick Start

### 1. Install

```bash
pip install fin3[databento]
```

Or from source: `uv pip install -e /path/to/fin3[databento]`.

### 2. Configure

Create a `.env` file:

```bash
FIN3_MINIO__ENDPOINT=localhost:9000
FIN3_MINIO__ACCESS_KEY=minioadmin
FIN3_MINIO__SECRET_KEY=minioadmin
FIN3_PROVIDERS__DATABENTO__PROVIDER_TYPE=databento
FIN3_PROVIDERS__DATABENTO__API_KEY=db-your-key-here
```

Or configure in code:

```python
from fin3 import ClientConfig, MarketDataFetcher
from fin3.config.settings import MinioConfig, DatabentoConfig

config = ClientConfig(
    minio=MinioConfig(endpoint="localhost:9000", access_key="minioadmin", secret_key="minioadmin"),
    providers={"databento": DatabentoConfig(api_key="db-your-key-here")},
)
fetcher = MarketDataFetcher(config)
```

### 3. Fetch data

```python
from datetime import datetime, timezone
from fin3 import AssetType, Resolution

df = fetcher.get_data(
    asset_type=AssetType.EQUITY_US,
    provider="databento",
    resolution=Resolution.ONE_MINUTE,
    symbols=["AAPL"],
    start=datetime(2024, 1, 2, tzinfo=timezone.utc),
    end=datetime(2024, 1, 2, 23, 59, tzinfo=timezone.utc),
)
```

### 4. Use it

```python
# Multi-symbol returns a MultiIndex DataFrame
aapl = df["AAPL"]           # symbol sub-DataFrame
closes = df[("AAPL", "close")]  # close price Series

# Feed into any backtesting framework
# VectorBT-style: wide DataFrame
# Backtrader-style: per-symbol panels
```

> 📖 **Full guide**: [docs/USAGE.md](docs/USAGE.md) — installation, config, crypto, futures, concurrency, monitoring, and more.

---

## How It Works

```
get_data(symbols, start, end)
    │
    ├─ For each symbol:
    │   ├─ Bootstrap metadata (IPO/delist dates via MetadataStore)
    │   ├─ Check ArcticDB for existing coverage
    │   ├─ Detect gaps (chunk-level comparison vs. calendar grid)
    │   └─ For each gap:
    │       ├─ Fetch from provider (with retry/backoff)
    │       ├─ Validate (Stage 1: raw data structural checks)
    │       ├─ Reindex to trading calendar grid
    │       ├─ Validate (Stage 2: NaN semantics, OHLCV constraints)
    │       └─ Write/update in ArcticDB (cross-process locked)
    │
    └─ Read from ArcticDB & return aligned MultiIndex DataFrame
```

Key properties:

- **Idempotent** — calling `get_data()` twice for the same range is a no-op (second call reads from storage).
- **Incremental** — if you have Jan–Mar and request Jan–Jun, only Apr–Jun is fetched.
- **Calendar-aware** — equity gaps detected per trading day; crypto gaps per hour.
- **Lock-guarded** — concurrent `get_data()` calls for the same symbol serialize via `flock`; no double-fetch, no clobbered writes.
- **Validated twice** — structural checks before reindex, NaN-semantics strictness after.

---

## Data Storage & Schema

### Library Naming

Libraries follow `{asset_prefix}-{resolution}-{provider}`:

| Library | Asset | Resolution | Provider |
|---|---|---|---|
| `equities-1m-databento` | US Equities | 1 minute | Databento |
| `equities-1h-databento` | US Equities | 1 hour | Databento |
| `equities-1d-databento` | US Equities | 1 day | Databento |

### Canonical OHLCV Schema

```
Index:  DatetimeIndex, tz=UTC, unnamed
Columns:
  open     float64  — Opening price
  high     float64  — Highest price
  low      float64  — Lowest price
  close    float64  — Closing price
  volume   float64  — Trade volume (0 for calendar-filled bars)
```

- All prices are `float64`. Volume is `float64` (allows NaN filling).
- The index is always UTC. No timezone-naive data.
- Missing bars within a trading session are filled with `volume=0, OHLC=NaN` when reindexed against the trading calendar.
- Symbols are uppercase tickers (e.g. `AAPL`, `SPY`). One symbol per ArcticDB symbol within a library.

### Timestamp Conventions

| Resolution | Timestamp convention |
|---|---|
| 1m, 5m, 15m | Bar start time in UTC, aligned to NYSE session (14:30–20:00 UTC) |
| 1h | Bar start time in UTC |
| 1d | Midnight UTC (00:00:00+00:00) |

Intraday bars are aligned to the [NYSE trading calendar](https://www.nyse.com/markets/hours-calendars). Pre-market and post-market bars are excluded.

---

## CLI Tools

The project ships several standalone scripts under `scripts/`. A unified `fin3` CLI is on the roadmap.

| Script | Purpose |
|---|---|
| `scripts/download_symbols.py` | Download data for multiple symbols with cost estimation |
| `scripts/audit_library.py` | Run 10+ integrity checks, generate HTML dashboard |
| `scripts/inspect_library.py` | Generate HTML library overview with per-symbol stats |
| `scripts/defragment_library.py` | Compact ArcticDB segments (supports `--dry-run`) |
| `scripts/normalize_databento.py` | Convert raw Databento parquet to canonical schema |
| `scripts/run_bulk_download.py` | Orchestrate multi-symbol bulk download |
| `scripts/fix_meta_rename.py` | Handle FB→META symbol rename gap |

---

## Documentation

| Document | Contents |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | Full usage guide: install, config, fetching data, crypto, futures, concurrency, monitoring, exception handling, adding providers |
| [docs/DESIGN.md](docs/DESIGN.md) | Architecture overview: requirements, data flow, component design, testing strategy |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Development roadmap with completed/planned features |
| [docs/dataset-comparison.md](docs/dataset-comparison.md) | Databento dataset cost analysis (XNAS.ITCH vs ARCX.PILLAR vs ...) |
| [docs/resource-monitoring-notes.md](docs/resource-monitoring-notes.md) | Resource monitoring implementation details and optimizations |

---

## Project Status

fin3 is **production-grade for its core path** (Databento equities/crypto/futures → ArcticDB/MinIO → aligned DataFrames) and actively developed. The key foundations are solid:

- ✅ Stable storage schema and calendar alignment
- ✅ Cross-process concurrency safety
- ✅ Two-stage data validation pipeline
- ✅ Integrity auditing with HTML dashboards
- ✅ Resource monitoring (tmux, terminal, CI)
- ✅ Incremental, idempotent data fetching

**Under active development**: Polygon/Binance providers, declarative manifest system, high-level retrieval API, and unified CLI.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full picture, priority guidance, and what's coming next.

---

## Architecture

```
                     ┌─────────────┐
                     │   Consumer   │  (backtesting, analysis, AI agents)
                     │  (your code) │
                     └──────┬──────┘
                            │ get_data()
                     ┌──────▼──────┐
                     │  MarketData │
                     │   Fetcher   │  Orchestrator with gap detection,
                     │             │  locking, cost ceiling, monitoring
                     └──┬─────┬────┘
                        │     │
              ┌─────────▼┐  ┌─▼──────────┐
              │  Storage  │  │  Provider  │  Pluggable via decorator registry
              │ (ArcticDB)│  │  (abstract)│
              │  + MinIO  │  │ ┌─────────┐│
              └───────────┘  │ │Databento││  ✅ Shipping
                             │ │ Polygon ││  🚧 Roadmap
                             │ │ Binance ││  🚧 Roadmap
                             │ └─────────┘│
                             └────────────┘
```