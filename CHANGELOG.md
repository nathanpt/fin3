# Changelog

All notable changes to fin3 are documented here. Versions follow 0.x minor
bumps for new features; dates are UTC.

## 0.4.0 — 2026-11-07

### Added

- **Historical options EOD chains** (owner-selected slice of feat-020):
  US equity/ETF option chains from EODHD's UnicornBay marketplace through a
  dedicated chain pipeline that never touches `get_data()` padding,
  aggregation, or lifecycle bootstrap.
  - `OptionsDataFetcher.backfill(root, start, end, *, contracts=None,
    max_requests=None)` — the explicitly mutating operation: extracts under a
    per-run request budget, merges with prior observations (conflicting
    overlaps raise and leave the prior version intact), and publishes one
    versioned snapshot whose metadata is the coverage manifest.
  - `OptionsDataFetcher.read_chain(root, as_of, *, expiry=None)` — strictly
    read-only (never resolves keys, locks, creates, fetches, writes, or
    defrags) returning a `ChainResult` with frame + manifest and reasoned
    no-data cases (`root_not_ingested`, `outside_ingested_coverage`,
    `date_not_ingested`, `expiry_before_as_of`, `no_rows_for_expiry`, stored
    zero-day reasons).
  - `EodhdOptionsProvider` (`eodhd_options` registry name): exhaust-paging
    with continuation validation, documented offset-ceiling partitioning
    (expiration → right → strike thousandths), per-contract histories,
    `get_instrument_bounds`, quote-quality flags (zero/crossed/unresolved/
    non-session), typed credential-free errors.
  - Chain schema contract (`fin3/options_schemas.py`,
    `fin3/utils/options_validation.py`): sorted unique `("as_of", "contract")`
    MultiIndex, exact columns/dtypes, strict wire validation, coverage
    manifest conventions recording what is unverified (multiplier is
    **unavailable from the source and stays null**; clock/timezone/OI/volume/
    survivorship/adjustment conventions are recorded, not guessed).
  - Routing guards: `AssetType.OPTIONS` with loud fan-out (silent enum-value
    library-name fallback removed), options rejected by every bar-pipeline
    entry point, registry `get()`/`get_chain()` mutually exclusive.
  - `ArcticStorage.read_with_metadata()` — data + metadata from one
    `lib.read()`, so snapshots and their coverage can never tear apart.
- **Daily volatility-index close levels** (feat-022): Cboe `VIX`/`VIX3M`
  daily closes via a keyless provider (`cboe`), stored raw with
  `AssetType.INDEX_US` (XNYS session-alignment proxy, `indices-1d-cboe`,
  volume-0 observation semantics in the shared quality rules).

### Changed

- Unit-test environment isolation now also removes the raw `EODHD_API_KEY`
  variable, keeping offline runs credential-free.

### Limits

- Options: no forward scheduler, no intraday capture, no options-exchange
  calendar (XCBO), no put-call parity — feat-020 remains open. Live-verified
  against the real API (SHY root snapshot digest-equal to source; SPY bounds
  2023-12-29…2026-11-06 across real pages); the contract in
  `docs/options-eodhd.md` states the private non-commercial use terms.

## 0.3.0 — 2026-11-01

### Added

- Shared data-quality rule registry (feat-021) powering both validation
  stages and the integrity audit with write/audit error parity; provider
  golden fixtures with mutation coverage; deterministic statistical outlier
  detection (audit-only warning); `numpy` as a direct runtime dependency.

## 0.2.0 — 2026-10-31

### Added

- Declarative dataset manifests (feat-017): `fin3 ensure` / `fin3 sync` /
  `fin3 status` reconcile stored data with a YAML declaration, with drift
  state, cost ceilings, and per-symbol results.
- Test-suite remediation: real provider gating, signed-value gates,
  value-level assertions.
