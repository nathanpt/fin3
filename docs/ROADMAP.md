# fin3 — Roadmap

A product-oriented roadmap for fin3. Each phase builds toward the vision of a
complete, declarative, production-ready financial data platform. Items marked
with checkmarks are done.

**Priority guidance from external review** (June 2026):

1. **Documentation + examples** — Unblocks everything else and your future self.
2. **Polygon + Binance provider implementations** — Fulfills the multi-provider promise.
3. **Declarative manifest/config layer** — Huge leverage for managing many symbols.
4. **High-level retrieval API** — Delight for data consumers (backtesting, analysis).
5. **CLI + packaging polish** — Operational friction reduction.

---

## Phase 1: Production Data Pipeline Foundations ✓

**Goal**: Stop vibe-coding and build something reliable.

All Phase 1 items are complete.

- ~~Implement proper error handling, retries, and logging~~
- ~~Add configuration management and environment separation~~
- ~~Build automated tests for the ingestion layer~~
- ~~Set up basic monitoring/logging for the pipeline~~
- ~~Pre-download cost checking via `get_cost()`~~
- ~~Dataset selection (XNAS.ITCH default, ARCX.PILLAR configurable)~~
- ~~Configurable retry policy on DatabentoConfig~~

---

## Phase 2: Infrastructure & Self-Hosting Mastery

**Goal**: Get comfortable running real infrastructure under constraints.

### Completed

- ~~**Defragmentation maintenance utilities** — `ArcticStorage` helpers,
  `fin3.storage.defrag`, `MarketDataFetcher.defragment()`, with dry-run
  reporting, per-symbol statuses, failure reporting, and documentation.~~
- ~~**Concurrent access protection** — Cross-process `fcntl.flock` advisory locks
  scoped per `(library, symbol)`, no stale locks, `LockAcquisitionError` with
  holder PID/host, integration test proving serialized provider fetch.~~
- ~~**Resource monitoring** — `fin3/monitoring/` with `ResourceTracker` context
  manager, auto-enabled in `MarketDataFetcher.get_data()`, with tmux split pane,
  rich.live info bar, and piped/CI stderr summary. Tracks RSS, network bytes,
  disk deltas, duration, and rows.~~

### Crypto Market Data Ingestion (Binance)

#### Infrastructure (✓ Completed)

All infra pieces for crypto support are implemented:

- ~~`AssetType.CRYPTO` enum value with `mic_code=None`, continuous 24/7 calendar~~
- ~~`ContinuousCalendarStrategy` — generates 24/7 trading grid, no gap~~
- ~~`BinanceConfig` Pydantic model with `api_key` and `api_secret`~~
- ~~Crypto-aware chunk boundaries (hourly chunks via `_chunk_boundaries`)~~
- ~~`library_name(AssetType.CRYPTO, ...)` → `crypto-{resolution}-{provider}`~~
- ~~Crypto symbol convention (`BTC-USD`) documented in USAGE.md~~
- ~~Tests: `ContinuousCalendarStrategy.generate_grid()`, gap detection with crypto, crypto library naming~~

#### BinanceProvider Implementation (Remaining)

Implement the `BinanceProvider` class registered via `@ProviderRegistry.register("binance")`:

- **API**: Binance klines endpoint for historical OHLCV (spot market).
- **Symbol mapping**: `BTC-USD` (fin3) ↔ `BTCUSDT` (Binance).
- **Calendar**: Continuous 24/7 — no calendar gaps to fill.
- **Rate limits**: Binance has strict weight-based rate limits — implement
  request budgeting with backoff.
- **Resolution mapping**: Binance klines use `1m`, `5m`, `1h`, `1d` etc. —
  straightforward mapping to fin3 `Resolution`.
- **Testing**: Unit tests with mocked responses + optional live integration test.

**Acceptance criteria**: `get_data(asset_type=AssetType.CRYPTO, provider="binance", ...)`
works end-to-end for BTC-USD at `1m`, `1h`, `1d` resolutions.

**Risks**: Symbol conventions differ by provider; crypto trades 24/7 so no
calendar-gap safety net; exchange outages/maintenance windows; strict
pagination/rate limits.

---

## Phase 3: Multi-Provider Completeness (Highest Technical Debt)

**Goal**: Fulfill the multi-provider promise so alternative sources are usable
when Databento is expensive, rate-limited, missing symbols, or has quality
issues.

### Polygon Provider Implementation

- **What**: Full `PolygonProvider` implementing the `DataProvider` abstract base.
  Handles Polygon's REST API (aggs/bars endpoint) for historical OHLCV bars,
  authentication, rate limiting, pagination, symbol mapping, and error handling.
- **Config**: `PolygonConfig` model already exists at `fin3/config/settings.py` —
  reuse it.
- **Resolution mapping**: Polygon uses millisecond-precision timestamps and
  different aggregation windows — normalize to fin3's canonical OHLCV schema.
- **Corporate actions**: Polygon's `adjusted=true` parameter returns split-adjusted
  prices; document this behavior.
- **Calendar integration**: US equities only → use existing `ExchangeCalendarStrategy`
  for NYSE. Reject crypto/futures requests until resolved.
- **Testing**: Unit tests with mocked HTTP responses + optional live integration test.

### Binance Provider Implementation

(Crypto infrastructure is complete — see Phase 2. This item is the `BinanceProvider`
class itself.)

- **API**: Binance `GET /api/v3/klines` for spot OHLCV.
- **Config**: `BinanceConfig` model already exists at `fin3/config/settings.py`.
- **Symbol normalization**: Map `BTC-USD` (fin3 convention) ↔ `BTCUSDT` (Binance
  convention). Support configurable symbol mapping.
- **Rate limits**: Binance has strict weight-based rate limits — implement
  request budgeting with backoff.
- **Resolution mapping**: Binance klines use `1m`, `5m`, `1h`, `1d` etc. —
  straightforward mapping to fin3 `Resolution`.
- **Calendar**: Continuous 24/7 via existing `ContinuousCalendarStrategy`.

### Provider Registry & Capability Negotiation

The `ProviderRegistry` decorator pattern is implemented (`providers/__init__.py`).
Extend it with:

- Capability introspection: which `AssetType`/`Resolution` combinations each
  provider supports, required calendar strategy, relative cost/preference weight.
- Provider preference/fallback lists per library or per symbol in
  `MarketDataFetcher`.

**Nuances/edge cases**:
- Different providers have wildly different timestamp semantics and resolution
  granularity.
- Corporate action handling differs: Databento MBO/P provides native adjusted
  prices; Polygon adjusts via parameter; Binance does not adjust.
- Provider discovery should remain registry-based (decorator pattern already in place).

---

## Phase 4: Comprehensive Documentation & API Reference

**Goal**: Make fin3 easy to adopt, maintain, and contribute to — for your future
self and any collaborators.

### User-Facing Documentation

- **Getting Started guide**: Minimal end-to-end example showing declarative
  usage — `pip install`, `.env` config, a single `get_data()` call, inspect
  the returned DataFrame.
- **Core concept docs**:
  - What "declarative" means in practice here (you describe *what* data you
    want, not *how* to fetch/store it).
  - The data model: canonical OHLCV schema, library naming, symbol conventions.
  - Calendar alignment: how NYSE calendars produce clean intraday DataFrames
    without gap artifacts.
- **Usage patterns**:
  - Single symbol, single day.
  - Multi-symbol aligned DataFrame (how `get_data()` handles multi-column output).
  - Gap filling behavior and null-bar semantics.
  - Converting output for common backtesting frameworks (VectorBT, Backtrader).
- **Operational guides**:
  - Detecting bad data via `audit_library.py` → fix via targeted normalize/fetch.
  - Defragmentation: when and why to run it.
  - Concurrency: how locking works and how to configure it.
- **README overhaul**: (In progress — see README updates below.) Shift from
  storage-convention focus to usage-first narrative. Lead with the 5-line example,
  not library naming conventions.

### API Reference (Sphinx / MkDocs)

- Full docstrings on all public classes and methods in `fin3/`.
- Auto-generated reference docs for:
  - `MarketDataFetcher` (primary entry point)
  - `ArcticStorage` (lower-level storage ops)
  - `ClientConfig` and provider configs
  - `AssetType`, `Resolution`, `OHLCV_COLUMNS`
  - Exception hierarchy
  - Integrity checking functions
- MkDocs with `material` theme for a clean, searchable site.

### Developer / Contributor Docs

- How to add a new provider (document the protocol beyond the brief section
  in USAGE.md).
- Testing conventions and fixture patterns (LMDB-backed ArcticDB, mock providers).
- Release process.

---

## Phase 5: Declarative Configuration / Manifest System

**Goal**: Define *what data you want to exist* in a YAML/JSON manifest, then
`fin3 ensure` / `fin3 sync` to make reality match.

### Manifest Format

```yaml
libraries:
  equities-1m-primary:
    provider: databento
    resolution: 1m
    symbols:
      - AAPL
      - SPY
      - QQQ
      - MSFT
      - GOOGL
    update_policy: incremental    # incremental | full-refresh
    calendar: XNYS

  equities-1d-master:
    provider: databento
    resolution: 1d
    symbols:
      include: ["SP500"]         # symbol list or file reference
      exclude: ["BRK.A"]
    update_policy: incremental
    calendar: XNYS

  crypto-1m-binance:
    provider: binance
    resolution: 1m
    symbols: ["BTC-USD", "ETH-USD"]
    update_policy: incremental
    calendar: 24x7
```

### `fin3 ensure` / `fin3 sync` Commands

- Read manifest, discover desired vs actual state for each library.
- Create missing libraries (with correct schema, dynamic_schema=True).
- Add missing symbols with data fetch.
- Update existing symbols for their date range.
- Report summary of what was created, updated, skipped, or errored.
- Dry-run mode.

### Nuances & Edge Cases

- **Versioning**: Store a manifest hash/signature to detect drift.
- **Provider fallbacks**: Specify per-library fallback providers.
- **Diff view**: `fin3 status` shows "desired vs actual" delta.
- **Pairs well with**: The locking work — concurrent `ensure` calls are safe.
- **Symbol lists**: Support inline lists, file references (`@symbols/nyse.txt`),
  and glob patterns.
- **Update policies**:
  - `incremental`: Only fetch missing date ranges.
  - `full-refresh`: Re-fetch entire history and replace.
  - `append-only`: For providers that only support forward fills.

---

## Phase 6: High-Level Retrieval / Query API for Consumers

**Goal**: Make the data consumption experience delightful — the whole reason
you're building this.

### `DataManager` / `Query` Class

High-level interface that wraps `MarketDataFetcher` with intelligent defaults
for common consumer needs:

```python
from fin3 import DataManager

dm = DataManager(config)

# Clean, aligned multi-symbol DataFrame
df = dm.get_bars(
    symbols=["AAPL", "SPY", "QQQ"],
    resolution="1h",
    start="2025-01-01",
    end="2025-06-01",
    asset_type="equities",
    provider="databento",
    align=True,               # reindex to common calendar
    fill_nulls=True,           # fill NaN OHLC with 0-volume bars
)

# Direct ArcticDB lazy query (for large ranges)
lazy = dm.query("equities-1m-databento", symbols=["AAPL"])
```

### Intelligent Defaults

- **Calendar-aware alignment**: Reindex multi-symbol DataFrames against the
  appropriate trading calendar (NYSE for equities, 24/7 for crypto, CME for
  futures). No off-by-one gaps or phantom sessions.
- **Null-bar handling**: Configurable fill strategies — NaN, forward-fill,
  zero-volume, or raise on excessive null rate.
- **Resampling**: Request `1h` data when only `1m` is stored — resample on
  the fly with proper OHLC aggregation.
- **Return formats**: pandas DataFrame (default), Polars, Arrow table, or
  raw ArcticDB lazy query for large ETL jobs.

### Multi-Symbol Batching & Alignment

- Fetch multiple symbols in one call with proper index alignment.
- Handle symbols with different start dates (IPO, listing date) gracefully.
- Return either wide format (one column per field per symbol) or long format
  (stacked, with a `symbol` column).

### Integrity-Aware Retrieval

- Optional pre-retrieval integrity check: verify checksums before returning.
- Report known quality issues (gap fill % vs real data %) in a metadata
  attachment.
- Tie into the existing `IntegrityReport` / `IntegrityIssue` infrastructure.

---

## Phase 7: Cohesive CLI & Proper Packaging

**Goal**: `fin3` works as a first-class CLI tool after `pip install`.

### CLI (Typer)

A single `fin3` entry point with subcommands:

| Command | Description |
|---|---|
| `fin3 fetch` | Fetch data for a symbol/library (current `download_symbols.py`) |
| `fin3 audit` | Run data quality checks (`audit_library.py`) |
| `fin3 inspect` | Inspect library contents (`inspect_library.py`) |
| `fin3 status` | Show desired vs actual state from manifest |
| `fin3 ensure` | Sync manifest to reality |
| `fin3 defrag` | Defragment ArcticDB symbols |
| `fin3 normalize` | Normalize raw provider data |
| `fin3 config` | Show effective configuration |

### Packaging

- `[project.scripts]` entry point in `pyproject.toml` so `fin3` works after
  `uv pip install -e .` or `pipx install fin3`.
- Proper extras: `fin3[databento]`, `fin3[binance]`, `fin3[polygon]`.
- CI publishing to PyPI / TestPyPI.

### Polish

- Progress bars via `rich.progress` for all multi-symbol operations.
- Structured logging with `structlog` (already configured) — JSON output for
  production, human-readable for interactive use.
- Config file loading (`fin3.toml` or `~/.config/fin3/config.toml`) with
  environment variable override chain.
- Exit codes and machine-readable output (`--json`) for scripting.

**Nuances**: The locking work makes concurrent CLI calls safe — expose that
safety. Support `--dry-run` on all mutating commands.

---

## Phase 8: Data Modeling & Feature Thinking (Legacy Phase 3)

**Goal**: Move from "store data" to "create valuable data assets."

- Design clean, versioned data schemas in ArcticDB (beyond the canonical OHLCV
  schema — e.g., derived feature libraries).
- Build reusable data transformations.
- Create derived features useful for backtesting (e.g., VWAP, volatility
  regimes, rolling correlations).
- Document the data model clearly.

---

## Phase 9: AI/Agent Layer Readiness (Legacy Phase 4)

**Goal**: Prepare fin3 to be consumed by AI systems.

- Expose clean APIs or query interfaces over stored data.
- Build a simple feature store pattern.
- Experiment with making the data easily consumable by agents/LLMs.
- Add metadata and lineage tracking.

---

## Appendix: Implemented Features Not Previously on Roadmap

The following substantial features were built during development but not
captured in earlier roadmap versions. They are now complete and maintained:

### Two-Stage Validation Pipeline ✓

- ~~`fin3/utils/validation.py`: `validate_raw_provider_data()` (Stage 1) and
  `validate_storage_artifact()` (Stage 2)~~
- ~~Stage 1: Pre-reindex structural checks — duplicates, monotonicity,
  resolution fidelity, expected-column presence~~
- ~~Stage 2: Post-reindex NaN-semantics strictness — `volume=0` implies all-OHLC-NaN,
  `volume>0` implies no-OHLC-NaN, plus OHLCV constraint checks~~
- ~~12 unit tests in `tests/test_validation.py`~~

### Integrity Audit System ✓

- ~~`fin3/utils/integrity.py`: `IntegrityIssue`, `IntegrityReport`, `check_integrity()`~~
- ~~10+ vectorized checks: missing bars, extra bars, duplicates,
  non-monotonic timestamps, resolution mismatch, NaN volume, negative volume,
  NaN semantics inconsistencies, OHLCV constraint violations, negative prices~~
- ~~Non-throwing — returns a structured report suitable for dashboards~~
- ~~Exported from `fin3/__init__.py`~~
- ~~`scripts/audit_library.py` generates a dark-themed HTML audit dashboard~~

### MetadataStore with 3-Tier Lifecycle Discovery ✓

- ~~`fin3/metadata/asset_profile.py`: `MetadataStore` for per-symbol IPO/delist dates~~
- ~~3-tier bootstrap: (1) cache lookup → (2) provider `get_instrument_bounds()` →
  (3) discovery-fetch fallback~~
- ~~Used by `MarketDataFetcher._symbol_gaps()` to clamp gap detection to a
  symbol's actual trading period~~
- ~~3 tests in `tests/test_metadata.py`~~

### Bar Aggregation / Resampling ✓

- ~~`_aggregate_bars()` in `core.py` — when a provider returns finer-grained bars
  than requested (e.g. 1m for a 5m request), resamples with proper OHLCV
  aggregation: `open=first`, `high=max`, `low=min`, `close=last`, `volume=sum`~~
- ~~Tests in `tests/test_core_helpers.py`~~

### Timestamp Snap-to-Grid ✓

- ~~`_snap_to_grid()` in `core.py` — aligns provider timestamps to the
  appropriate calendar grid (daily midnight UTC → market-open, intraday
  nearest-neighbor with collision detection)~~
- ~~Tests in `tests/test_core_helpers.py`~~

### Pre-Fetch Cost Ceiling ✓

- ~~`CostLimitExceededError` in `fin3/exceptions.py` — raised when estimated
  download cost exceeds `max_cost` parameter~~
- ~~Integrated into `MarketDataFetcher.get_data()`~~

### Symbol Rename Edge-Case Fixer ✓

- ~~`scripts/fix_meta_rename.py` — handles the FB→META symbol rename gap
  (2022-06-09) by downloading FB data and writing it into the META symbol~~
- ~~Argparse with `--dry-run`, `--resolution` support~~

### Cross-Process Concurrency Integration Test ✓

- ~~`tests/integration/test_concurrency.py` — proves `SymbolLock` prevents
  double-fetch under concurrent `get_data()` calls~~
- ~~Uses `multiprocessing.spawn` with real MinIO — 186-line sophisticated test~~

### Backtest Smoke Test ✓

- ~~`tests/integration/test_backtest_smoke.py` — end-to-end SMA20/50 crossover
  on live AAPL daily data from MinIO~~
- ~~Verifies the consumer read path: returns distribution, volume validity,
  OHLCV schema~~

### Provider Byte Counting ✓

- ~~`ByteCounter` in `fin3/monitoring/collector.py` — wraps provider `fetch()`
  to track network bytes and fetch count~~
- ~~Consumed by `ResourceTracker` in `get_data()`~~
- ~~Tests in `tests/test_monitoring.py`~~

---

## Appendix: Key Architectural Strengths to Preserve

Items that are already solid and should not be regressed:

- **Canonical OHLCV schema** — Clean, consistent, well-documented.
- **Calendar alignment** — NYSE, CME, and 24/7 continuous strategies.
- **Cross-process locking** — `fcntl.flock` per `(library, symbol)`, no stale
  locks, tested.
- **Audit/inspection tooling** — HTML dashboards, integrity checks, gap
  detection.
- **Provider registry pattern** — Decorator-based registration, clean
  abstraction.
- **Config system** — Pydantic Settings with `.env` support.
- **Defragmentation utilities** — Dry-run, per-symbol reporting.
- **Resource monitoring** — Tmux split, rich.live, and CI modes.
- **Exception hierarchy** — 9 typed exceptions for precise error handling.