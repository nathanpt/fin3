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

- ~~**Concurrent access protection** — The original design assumed single-process
  execution; two processes calling `get_data()` for the same symbol concurrently
  would both detect the same gap, both fetch, and the second `update` (with
  `prune_previous_versions=True`) would silently clobber the first with no
  rollback.~~
  - **Completed**: Implemented via `fin3.storage.locking.SymbolLock` — file-based
    `fcntl.flock` advisory locks scoped per `(library, symbol)`. Stdlib-only
    (no new dependencies); locks auto-release on process exit — clean return,
    crash, or `SIGKILL` — because `flock` is tied to the open file description,
    so there are no stale locks to clean up. Configured via `LockConfig`
    (`FIN3_LOCK__ENABLED`, `FIN3_LOCK__LOCK_DIR`, `FIN3_LOCK__TIMEOUT_S`,
    `FIN3_LOCK__POLL_INTERVAL_S`); on by default. Guards the check-then-act body
    of `MarketDataFetcher._ensure_symbol` plus `ArcticStorage.delete_symbol`
    and `defragment_symbol`; contention is reported via `LockAcquisitionError`
    with the holder's PID/host. Covered by unit tests and a cross-process
    integration test (`tests/integration/test_concurrency.py`) proving two
    concurrent `get_data()` calls for the same symbol serialize to a single
    provider fetch. Documented in `docs/USAGE.md` / `docs/DESIGN.md`.

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
  - **Completed**: Implemented via `fin3/monitoring/` package with a
    `ResourceTracker` context manager, auto-enabled in
    `MarketDataFetcher.get_data()` (no flags needed). Environment-aware
    display: a live tmux split pane, an inline `rich.live` info bar for
    native terminals, or a stderr summary for piped/CI. Tracks RSS memory
    (psutil), application-level network bytes (via provider `fetch`
    wrapping), disk deltas + library total (one `get_sizes()` scan),
    duration, and rows. Per-symbol phases surface cost-estimation and
    fetch progress live. Implementation notes in
    `docs/resource-monitoring-notes.md`; usage in `docs/USAGE.md`.

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
