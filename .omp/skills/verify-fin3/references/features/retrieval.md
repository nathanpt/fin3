# Retrieval

`MarketDataFetcher.get_data()` (`fin3/core.py`) is the library entry point:
coverage check → gap fetch → validate → store → read back as one aligned
frame. The user-visible invariant: **a repeated call over a fully covered
range is a read-only no-op** (feat-001), and the returned frame is canonical.

## How to reach it

Precondition: an LMDB store seeded with `VERIFY` equity bars at every XNYS
session in 2026-01-01..2026-01-10 (`equities-1d-databento`), plus a metadata
cache entry for `VERIFY`. The check builds this in a temp dir and deletes it.

## How to drive it

```
uv run python scripts/verify.py idempotent --human
```

The check constructs `MarketDataFetcher`, swaps its storage to the LMDB
instance (the documented seam: `fetcher._storage` / `fetcher._metadata._storage`),
then calls `get_data(EQUITY_US, "databento", 1d, [VERIFY], start, end)` twice.

## What to assert

- Columns are a `(symbol, field)` MultiIndex spanning exactly
  `open high low close volume`; index is UTC; every seeded bar is returned.
- After the second call the ArcticDB version count is unchanged (no write
  happened) and the frames compare equal.

## Traps

- **Crypto daily bars never "cover" a range.** Gap detection chunks
  continuous assets *per hour* (`detect_gaps._chunk_boundaries`), so a 1d bar
  covers only its own hour chunk and the fetcher plans ~23 refetches/day. Use
  an exchange asset (equity_us/XNYS) for covered-range tests; crypto is fine
  for the manifest planner, which chunks per UTC day.
- **Metadata bootstrap can hit the network even on a covered range:**
  `MetadataStore.bootstrap_metadata` runs before the coverage check and, on
  cache miss, calls the provider's bounds API and possibly a discovery fetch.
  Pre-seed the cache (`MetadataStore.set_lifecycle_bounds(symbol, None,
  None)`) for any offline `get_data` run.
- Provider *construction* is offline (Databento's SDK `Historical` init prints
  a line but makes no request); `api_key` can be any non-empty string when no
  fetch is planned.
- `index_us` supports only `1d`; `options` raises (use `OptionsDataFetcher`).
