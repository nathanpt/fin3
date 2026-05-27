# fin3 - Roadmap

Phase 2 items surfaced during design review. Each entry is deferred from Phase 1
with a clear trigger condition that justifies implementation.

---

## 1. Three-Way Write Routing (`write` / `append` / `update`)

**Trigger**: Defragmentation is becoming a maintenance burden, or trailing-gap
writes are measurably slow.

**Rationale**: Phase 1 uses two-way routing (`write` / `update`) for simplicity
and integrity — `update(date_range=...)` is self-correcting by construction. A
three-way router that detects trailing gaps and routes them to `append` avoids
the read-modify-write cost of `update` and reduces segment fragmentation. This
is a performance optimization, not a correctness fix.

**Risk**: `append` has no `date_range` guard rail. Off-by-one errors in gap
detection produce duplicate rows with no easy recovery (since
`prune_previous_versions=True`). Must be paired with robust boundary assertions
and integration tests before enabling.

**Paired with**: Defragmentation utilities (item 2).

---

## 2. Defragmentation Maintenance Utilities

**Trigger**: Read performance degrades after frequent small `update` or `append`
operations, or as a scheduled maintenance task.

**Rationale**: Each `update(date_range=...)` creates a new data segment.
Over time, many small segments degrade read performance. ArcticDB provides
`lib.defragment_symbol_data(symbol)` to merge segments. Phase 2 should expose
this as a utility function or CLI command, and optionally run it automatically
after bulk gap-filling operations.

**Paired with**: Three-way write routing (item 1).

---

## 3. Concurrent Access Protection

**Trigger**: Library is used on a shared dev server where multiple processes may
call `get_data()` simultaneously.

**Problem (document clearly)**: The current design assumes single-process
execution. If two processes call `get_data("AAPL", ...)` concurrently on the
same server:

1. Both processes read the symbol from ArcticDB for gap detection.
2. Both detect the same gap.
3. Both fetch from the provider and call `update(date_range=...)`.
4. The second `update` may overwrite the first (since the first may not have
   committed yet), or produce a conflicted state.
5. With `prune_previous_versions=True`, there is no rollback.

This is not hypothetical — it is the default failure mode on any shared server.
Recovery requires reverting to a MinIO infrastructure-level snapshot.

**Potential solutions (evaluate at implementation time)**:
- Advisory lock (file-based or in MinIO metadata) per symbol.
- PID-scoped locking with timeout and deadlock detection.
- ArcticDB staged writes (`write(staged=True)` + `finalize_staged_data()`).

**Phase 1 mitigation**: Document the constraint clearly. Operator is responsible
for ensuring single-process access per library/symbol.

---

## 4. Configurable Retry Policy

**Trigger**: Users hitting rate limits on specific providers need to tune retry
behavior without code changes.

**Rationale**: Phase 1 uses hardcoded retry constants (`MAX_RETRIES=3`,
`INITIAL_BACKOFF_SECONDS=1.0`, `MAX_BACKOFF_SECONDS=30.0`). These are
reasonable defaults but not universally appropriate. Some providers (e.g.,
free-tier Polygon) require longer backoff; others (Binance WebSocket) rarely
need retries at all.

**Implementation**: Add retry policy fields to per-provider config objects
(e.g., `DatabentoConfig(max_retries=5, initial_backoff=2.0)`). Use provider
defaults when not explicitly set.

---

## 5. ~~Switch 1m Databento Data to ARCX.PILLAR~~ ✅ Done

**Trigger**: Re-download of 60 symbols for `equities-1m-databento`.

**Rationale**: `XNAS.ITCH` only captures Nasdaq trades, causing 10-20% null bars
for symbols that primarily trade on NYSE Arca (SLV, XLRE, SMCI, MSTR, META, etc.).
`ARCX.PILLAR` covers NYSE Arca, the leading ETF venue (~10% of US equities ADV).
Cost is ~$35 for all 60 symbols across 7 years. See
`docs/databento/dataset-comparison.md` for full cost analysis.

**Implementation**:
- Hard-code `dataset="ARCX.PILLAR"` in `DatabentoProvider` when resolution is 1m
  and asset type is equities. Keep `XNAS.ITCH` as fallback or for other resolutions.
- Note: ARCX.PILLAR uses CMS symbol convention (`BRK B`) not Nasdaq (`BRK.B`).
  May require symbology resolution via `client.symbol.resolve()`.

---

## 6. ~~Pre-Download Cost Checking via `get_cost()`~~ ✅ Done

**Trigger**: Next release after dataset switching.

**Rationale**: Databento charges per request. Before downloading large date ranges
or many symbols, fin3 should optionally query `client.metadata.get_cost()` to
show the estimated cost and require explicit confirmation (or enforce a budget
limit). This prevents accidental expensive downloads.

**Implementation**:
- Add `estimate_cost()` method to `DatabentoProvider` wrapping
  `client.metadata.get_cost()`. Returns a `float` (USD).
- Add optional `max_cost` parameter to `get_data()` / `MarketDataFetcher`.
  If set, raise before fetching when estimated cost exceeds the limit.
- Note: `get_cost()` returns a `float`, not a dict. The `billable_cost` key
  pattern from the docs example is incorrect.
