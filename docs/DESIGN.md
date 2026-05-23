# fin3 - DESIGN.md

## 1. Project Overview

**Project Name**: `fin3`

**Goal**:
Create a reusable Python library that allows backtesting scripts to declaratively request financial time-series data by specifying:
- `asset_type` (equities, crypto, futures, etc.)
- `provider` (databento, polygon, binance, etc.)
- `resolution` (1m, 5m, 1h, 1d, etc.)
- `symbols`: list of tickers
- `start` / `end`: date range

The library must:
1. Check if the required data already exists in ArcticDB (on MinIO).
2. Download only missing portions from the provider.
3. Store the new data durably.
4. Return a clean, consistent pandas DataFrame with a standardized schema.

This minimizes redundant downloads (cost control) and ensures high data integrity.

**Core Philosophy**:
- **Declarative & idempotent**: Calling `get_data()` multiple times with same params should be safe and efficient.
- **Consumer-agnostic**: Return clean pandas DataFrames (datetime index, OHLCV columns). Consumers like VectorBT Pro can read directly from ArcticDB natively — no conversion layer needed.
- **Extensible**: Easy to add new providers, asset types, resolutions.
- **Reliable**: Strong focus on data integrity, validation, and auditability.
- **Performant**: Suitable for interactive and large-scale backtesting on a dev server.

## 2. Requirements

### Functional Requirements
- Support multiple providers via a plugin-style architecture.
- Precise gap detection per symbol + date range.
- Automatic library naming: `{asset_type}-{resolution}-{provider}`
- Metadata storage (download date, provider version, checksums).
- Consistent output schema (datetime index, OHLCV columns).
- Error handling for network, rate limits, data quality issues.
- Logging of all downloads for cost tracking.

### Non-Functional Requirements
- **Data Integrity**: Checksums, validation, versioning.
- **Cost Efficiency**: Minimize API calls and redundant storage.
- **Pythonic**: Easy to use in Jupyter/notebooks and backtesting scripts.
- **Testable**: High unit + integration test coverage.
- **Dependencies**: Minimal (arcticdb, databento, pandas, pydantic, etc.).

## 3. System Architecture

### High-Level Components

```bash
fin3/
├── __init__.py                  # Public API
├── core.py                      # MarketDataFetcher orchestrator
├── config/
│   ├── __init__.py
│   └── settings.py              # Pydantic settings
├── providers/
│   ├── __init__.py
│   ├── base.py                  # Abstract DataProvider
│   ├── databento.py
│   ├── polygon.py               # Future
│   └── binance.py               # Future (crypto)
├── storage/
│   ├── __init__.py
│   └── arctic.py                # ArcticDB + MinIO adapter
├── utils/
│   ├── __init__.py
│   ├── date_utils.py            # Range calculations, gap detection
│   ├── validation.py            # Data quality checks
│   ├── symbol_utils.py
│   └── logging.py
├── exceptions.py
├── schemas.py                   # Data schemas + shared models (output schema, gap results, write metadata)
└── cli.py                       # Optional CLI for manual ingestion
```

### Data Flow
1. `get_data(...)` called from backtest script.
2. `MarketDataFetcher` determines library name (`{asset_type}-{resolution}-{provider}`).
3. `ArcticStorage` checks existing coverage per symbol using `lib.read(symbol, date_range=(start, end))`.
4. For missing ranges → route to correct `DataProvider`.
5. Provider fetches → validation → write gap data to ArcticDB via `lib.update(symbol, data, date_range=(gap_start, gap_end))`.
6. Final read from ArcticDB → normalized DataFrame returned.

**ArcticDB Primitives Used**:
- `write` — initial symbol creation (first data for a ticker).
- `update(date_range=...)` — gap filling. Overwrites the specified date range with new data. The `date_range` parameter ensures only the gap is modified.
- `append` — extending existing data forward in time (e.g. daily updates appending new bars).

## 4. Detailed Component Design

### 4.1 Core - MarketDataFetcher

```python
class MarketDataFetcher:
    def __init__(self, config: ClientConfig):
        self.storage = ArcticStorage(config.minio_uri)
        self.providers = ProviderRegistry(config)
    
    def get_data(
        self,
        asset_type: str,
        provider: str,
        resolution: str,
        symbols: list[str],
        start: datetime,
        end: datetime,
        **kwargs
    ) -> pd.DataFrame:
        # Implementation as discussed previously
        pass
```

**Key Methods**:
- `get_data()` - main entry point
- `ensure_data()` - internal: download missing
- `normalize_dataframe()` - enforce standardized output schema

### 4.2 Storage Layer (ArcticStorage)

```python
class ArcticStorage:
    def __init__(self, uri: str, library_options: LibraryOptions | None = None):
        self.arctic = adb.Arctic(uri)
        self._library_options = library_options
    
    def _get_or_create_library(self, name: str) -> Library:
        if name not in self.arctic.list_libraries():
            self.arctic.create_library(name, self._library_options)
        return self.arctic[name]
```

**Library Creation Options** (set once at creation, immutable afterward):
- `dynamic_schema=True` — recommended. Symbols within a library may have different columns (e.g. some tickers have `trade_count`/`vwap`, others don't). Without this, all symbols must share identical column names and types.
- `rows_per_segment` — default 100,000. Tune based on resolution (e.g. 1m data = ~390 rows/day, so default is fine for multi-month segments).
- `columns_per_segment` — default 127. No need to change for OHLCV + a few optional columns.

**Metadata Handling**:
ArcticDB metadata is per-version and **not inherited** across versions. Every `write`, `append`, or `update` call must explicitly include metadata. We will attach metadata on every write:

```python
metadata = {
    "downloaded_at": datetime.utcnow().isoformat(),
    "provider": "databento",
    "provider_version": "0.78.0",
    "symbol": "AAPL",
    "date_range": [start.isoformat(), end.isoformat()],
}
lib.update(symbol, data, date_range=(start, end), metadata=metadata)
```

**Version Management**:
ArcticDB keeps every version by default, which causes unbounded storage growth. Strategy:
- Use `prune_previous_versions=True` on all `write`/`append`/`update` calls during normal operation.
- Use snapshots (`lib.snapshot()`) to preserve known-good states before bulk updates or data repairs.
- Snapshot naming: `snap_{YYYY-MM-DD}_{description}` (e.g. `snap_2025-01-15_initial_load`).

### 4.3 Providers Layer

```python
class DataProvider(ABC):
    @abstractmethod
    def fetch(self, symbols: list[str], start: datetime, end: datetime, resolution: str, **kwargs) -> pd.DataFrame:
        ...
```

Each provider implements `fetch()` to return a pandas DataFrame matching our standardized schema. The provider is responsible for:
- API communication and rate limiting.
- Converting provider-specific data formats to our column schema.
- Returning data that covers exactly the requested date range (no gaps within the requested range).

Providers do **not** interact with ArcticDB directly — they only fetch and return DataFrames.

## 5. Data Integrity & Cost Control Strategies

**Gap Detection**:
For each requested symbol, read existing data from ArcticDB with `lib.read(symbol, date_range=(start, end))`. Compare the actual date coverage against the requested range to identify missing intervals. Only fetch missing intervals from the provider.

**Data Validation** (applied after provider fetch, before ArcticDB write):
- No duplicate timestamps within a symbol's data.
- OHLC relationship: `low <= open <= high` and `low <= close <= high`.
- No NaN in required columns (open, high, low, close, volume).
- Timestamps are monotonically increasing and match the expected resolution.

**Cost Control**:
- Log all provider API calls with symbol, date range, and row count.
- Never re-download data that already exists in ArcticDB.
- Use ArcticDB's server-side `QueryBuilder.date_range()` to check coverage without materializing full DataFrames.

**Maintenance**:
- **Defragmentation**: Frequent small `update` or `append` calls create many small data segments, degrading read performance. Run `lib.defragment_symbol_data(symbol)` periodically or after bulk gap-filling operations.
- **Version pruning**: `prune_previous_versions=True` on all writes. Old versions are not kept unless a snapshot exists.

## 6. Naming & Organization Conventions

**Library Names**: `{asset_type}-{resolution}-{provider}` (lowercase, kebab-case)  
Examples:
- `equities-1m-databento`
- `crypto-1h-binance`
- `equities-1d-polygon`

**Symbol Naming**:
- Equities: "AAPL", "TSLA"
- Crypto: "BTC-USD", "ETH-USDT" (standardized)

**Column Schema** (standardized output):
- `timestamp` (datetime64[ns, UTC] index)
- Multi-column support for multiple symbols
- `open`, `high`, `low`, `close`, `volume`
- Optional: `trade_count`, `vwap`, etc.

## 7. Error Handling & Edge Cases

- **Network errors**: Retry with exponential backoff on provider API calls. Log and raise after max retries.
- **Rate limiting**: Respect provider-specific rate limits. Use `time.sleep` or provider-provided wait mechanisms.
- **Data quality issues**: If validation fails, log the issue with full context (symbol, date range, provider) and raise `DataValidationError`. Do not write invalid data to ArcticDB.
- **Concurrent writes**: ArcticDB does not support concurrent writes to a single symbol (unless using staged writes). Our design is single-process, so this is not a concern. If parallel ingestion is needed later, use `write(staged=True)` + `finalize_staged_data()`.
- **Missing symbols**: If a symbol has never been written, use `write` for initial creation, then `update`/`append` for subsequent data.

## 8. Testing Strategy

- **Unit tests**: Test gap detection logic, schema validation, library naming, metadata handling. Mock ArcticDB and provider APIs.
- **Integration tests**: Test full `get_data()` flow with a real (or local LMDB-backed) ArcticDB instance. Verify write → read round-trips, gap filling, and append behavior.
- **Provider tests**: Each provider gets fixture-based tests with recorded API responses to avoid hitting real APIs in CI.
- **Coverage target**: >=80% on core logic (gap detection, data flow, validation).
- **Test naming**: `test_<feature>_<scenario>()` (e.g. `test_get_data_downloads_missing_range`, `test_update_fills_gap_without_touching_existing_data`).

## 9. Future Extensions

1. Async support (`asyncio`)
2. More providers (Polygon.io, Tiingo, etc.)
3. Symbol universe management + metadata
4. CLI + Web UI for monitoring storage
5. Scheduled ingestion via Prefect / Airflow
6. Leverage ArcticDB `QueryBuilder` for server-side resampling, filtering, and aggregation (avoids materializing full DataFrames for common queries)

## 10. Implementation Roadmap

**Phase 1 (MVP)**:
- ArcticStorage with basic read/write
- Databento provider
- Core MarketDataFetcher with basic gap detection
- Configuration + logging

**Phase 2**:
- Advanced gap detection (multi-interval gaps, partial coverage)
- Data validation + checksums
- Version management (snapshots, pruning strategy)
- Defragmentation maintenance utilities
- Tests + documentation

**Phase 3**:
- Additional providers
- CLI
- Caching layer

## 11. Dependencies

```toml
[project.dependencies]
arcticdb = "^5.0"
databento = "^0.XX"
pandas = "^2.0"
pydantic-settings = "^2.0"
python-dotenv = "^1.0"
structlog = "^24.0"
```

## 12. Open Questions / Decisions

1. pandas vs Polars (consider Polars for performance)?
2. Coverage metadata: store per-version in ArcticDB metadata, or maintain a separate coverage index?
