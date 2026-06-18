# fin3 - Roadmap

A skill-development roadmap for fin3. Each phase builds a concrete capability
while producing useful infrastructure. Items marked with checkmarks are done.

---

## Phase 1: Production Data Pipeline Foundations (4–6 weeks)

**Goal**: Stop vibe-coding and build something reliable.

**Skill developed**: Production-grade data engineering habits.

### Completed

- ~~Implement proper error handling, retries, and logging~~
- ~~Add configuration management and environment separation~~
- ~~Build automated tests for the ingestion layer~~
- ~~Set up basic monitoring/logging for the pipeline~~
- ~~Pre-download cost checking via `get_cost()`~~
- ~~Dataset selection (XNAS.ITCH default, ARCX.PILLAR configurable)~~
- ~~Configurable retry policy on DatabentoConfig~~

### Remaining

(None — all Phase 1 items complete.)

---

## Phase 2: Infrastructure & Self-Hosting Mastery (4–6 weeks)

**Goal**: Get comfortable running real infrastructure under constraints.

**Skill developed**: Running production self-hosted systems with real constraints.

- **Three-way write routing (`write` / `append` / `update`)** — Phase 1 uses
  two-way routing for simplicity and integrity. A three-way router that detects
  trailing gaps and routes them to `append` avoids the read-modify-write cost of
  `update` and reduces segment fragmentation.
  - **Trigger**: Defragmentation is becoming a maintenance burden, or
    trailing-gap writes are measurably slow.
  - **Risk**: `append` has no `date_range` guard rail. Off-by-one errors in gap
    detection produce duplicate rows with no easy recovery (since
    `prune_previous_versions=True`). Must be paired with robust boundary
    assertions and integration tests before enabling.
  - **Paired with**: Defragmentation utilities (next item).

- ~~**Defragmentation maintenance utilities** — Each `update(date_range=...)`
  creates a new data segment. Over time, many small segments degrade read
  performance. ArcticDB provides `lib.defragment_symbol_data(symbol)` to merge
  segments. Expose as a utility function or CLI command, and optionally run
  automatically after bulk gap-filling operations.~~
  - **Completed**: Implemented via `ArcticStorage` helpers,
    `fin3.storage.defrag`, `MarketDataFetcher.defragment()`,
    `get_data(..., defrag=True)`, and `scripts/defragment_library.py`, with
    dry-run reporting, explicit per-symbol statuses, failure reporting, and
    documentation in `docs/USAGE.md` / `docs/DESIGN.md`.

- **Concurrent access protection** — The current design assumes single-process
  execution. If two processes call `get_data("AAPL", ...)` concurrently, both
  detect the same gap, both fetch from the provider, and the second `update` may
  overwrite the first or produce a conflicted state. With
  `prune_previous_versions=True`, there is no rollback.
  - **Trigger**: Library is used on a shared dev server where multiple processes
    may call `get_data()` simultaneously.
  - **Potential solutions**: Advisory lock (file-based or in MinIO metadata) per
    symbol; PID-scoped locking with timeout and deadlock detection; ArcticDB
    staged writes (`write(staged=True)` + `finalize_staged_data()`).

- **Crypto market data ingestion** — Add a reliable path to download and store
  cryptocurrency OHLCV/ticker data, starting with Bitcoin (e.g. `BTC-USD` or
  `BTCUSDT`). This should work with the existing `AssetType.CRYPTO` continuous
  24/7 calendar and store into provider-specific ArcticDB libraries such as
  `crypto-1m-binance`.
  - **Goal**: Make `get_data(asset_type=AssetType.CRYPTO, ...)` work end-to-end
    for at least Bitcoin, with tests proving 24/7 gap detection, provider fetch,
    validation, storage, and retrieval.
  - **Potential providers**: Binance first because it is a natural fit for crypto
    OHLCV and avoids forcing Databento equities assumptions onto crypto data.
  - **Acceptance criteria**: Download BTC data for common resolutions (`1m`,
    `1h`, `1d`), normalize to fin3 OHLCV schema, store in ArcticDB/MinIO, pass
    unit/integration tests, and document example usage.
  - **Risks**: Symbol conventions differ by provider (`BTC-USD` vs `BTCUSDT`),
    crypto trades 24/7, exchanges have outages/maintenance windows, and some APIs
    impose strict pagination/rate limits.

- ~~**Resource monitoring** — Disk, memory, network tracking for the pipeline.~~
  - **Completed**: Implemented via `fin3/monitoring/` package with
    `ResourceTracker` context manager, live tmux pane display (with
    `rich` panels), application-level network byte counting via provider
    fetch wrapping, RSS memory sampling via `psutil`, and disk delta
    tracking via ArcticDB symbol sizes. Auto-enabled in
    `MarketDataFetcher.get_data()` with tmux/TTY/non-TTY fallbacks.
    Documented in `docs/USAGE.md`.

---

## Phase 3: Data Modeling & Feature Thinking (6–8 weeks)

**Goal**: Move from "store data" to "create valuable data assets."

**Skill developed**: Thinking like a data product builder.

- Design clean, versioned data schemas in ArcticDB
- Build reusable data transformations (potential edge area)
- Create derived features useful for backtesting
- Document the data model clearly

---

## Phase 4: AI/Agent Layer Readiness (Ongoing, start after Phase 2)

**Goal**: Prepare fin3 to be consumed by AI systems.

**Skill developed**: Building data infrastructure that AI can actually use.

- Expose clean APIs or query interfaces over stored data
- Build a simple feature store pattern
- Experiment with making the data easily consumable by agents/LLMs
- Add metadata and lineage tracking
