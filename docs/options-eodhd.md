# EODHD historical options EOD chains

fin3 ingests US equity/ETF historical end-of-day option chains from
[EODHD's UnicornBay options marketplace API](https://eodhd.com/api/mp/unicornbay/options)
through a dedicated chain provider, stores one snapshot per underlying
root, and serves read-only chain queries. This page is the behavior
contract for the `options-eod-eodhd` dataset: schema, units, null
semantics, configuration, and the writer/read split.

Options chains never pass through fin3's OHLCV machinery: no padding, no
aggregation, no calendar reindex, and no IPO/delisting metadata bootstrap.
Every bar-pipeline entry point (`MarketDataFetcher`, manifest sync,
`inspect_library`, `observation_mask`, the OHLCV validators) rejects options
data loudly.

## Public API

```python
from fin3 import ClientConfig, OptionsDataFetcher
from fin3.options_schemas import ChainResult, ChainCoverageManifest
from datetime import date

fetcher = OptionsDataFetcher(config)          # no provider, storage, or key work here

manifest = fetcher.backfill(                  # the explicitly mutating operation
    "SHY",
    date(2026, 11, 6), date(2026, 11, 6),
    contracts=None,                           # or an explicit OSI contract selection
    max_requests=None,                        # overrides the configured per-run cap
)

result = fetcher.read_chain("SHY", date(2026, 11, 6), expiry=None)
result.status    # "observed" | "no_data"
result.reason    # machine-readable reason when no_data
result.data      # chain DataFrame (see schema below)
result.manifest  # ChainCoverageManifest of the stored snapshot
```

`fin3.RequestLimitExceededError` (attributes `requests_used`,
`max_requests`) is raised before work that would breach the per-run cap.
Reads never resolve an API key, construct a provider, acquire a write
lock, or create anything; a read of an absent root issues no writes at
all.

## DataFrame schema

Index: sorted, unique `MultiIndex("as_of", "contract")`. `as_of` is a
UTC-midnight label parsed from the source EOD id `CONTRACT-YYYY-MM-DD` —
never from `tradetime` or quote timestamps. `contract` is the compact
vendor OCC/OSI identifier. Columns, in order:

| Column | Type | Semantics |
|---|---|---|
| `root` | object str | Source `underlying_symbol`. |
| `osi_root` | object str | OSI root parsed from the contract id; may differ from `root` for adjusted symbols and is **never rewritten**. |
| `expiry` | object str | ISO date parsed from the contract id (cross-checked against `exp_date`). |
| `right` | object str | `C`/`P`, from source `type=call/put`. |
| `expiration_type` | object str/None | Vendor field, verbatim; may be null. |
| `currency` | object str/None | Vendor field, verbatim; may be null. |
| `strike` | float64 | OSI 8-digit field / 1,000 (exact thousandths). |
| `multiplier` | float64 | **Always NaN** — the source has no multiplier field. Never defaulted to 100. |
| `is_standard_contract` | bool | Heuristic only: OSI root equals the letters-only underlying root **and** vendor `expiration_type` is `weekly`/`monthly`. False positives (adjusted contracts with unchanged root) and false negatives (null/odd vendor labels) both exist. It is not a deliverable guarantee and must not be used to fill a multiplier. |
| `open high low last bid ask bid_size ask_size volume open_interest implied_volatility midpoint theoretical delta gamma theta vega rho` | float64 | Missing = NaN. `volatility` maps to `implied_volatility` (documented decimal IV). Greeks are vendor units whose time/scaling conventions are unverified; signed Greeks are legal. Nulls mean source null, never zero. |
| `bid_timestamp_raw`, `ask_timestamp_raw`, `tradetime` | object str/None | Source `bid_date`/`ask_date`/`tradetime` verbatim. |
| `bid_timestamp_utc`, `ask_timestamp_utc` | datetime64[ns, UTC] | Converted **only** when the raw string carries an explicit offset (`…Z`). Naive strings (e.g. `2025-03-20 20:14:59`) stay NaT with the raw string retained; the naive timezone is unverified. |
| `has_zero_bid`, `has_zero_ask`, `has_crossed_quote`, `has_unresolved_quote_time`, `has_non_session_label` | bool | Zero = non-null price equal to 0. Crossed = both non-null and `bid > ask`. Unresolved = non-null bid/ask without a normalized UTC timestamp. Non-session = the `as_of` label is absent from the XNYS calendar proxy (a session-label proxy only — not an options-hours claim). Rows are retained and flagged, never dropped, clamped, or substituted. |

Quotes/strikes/IV/volume/OI are validated finite, nonnegative, and
integral where applicable. An expired contract's zero-bid / 0.01-ask row
with IV 2.0228 is valid data — there is no IV ≤ 1 ceiling.

## Stored snapshot and coverage manifest

One ArcticDB symbol per underlying root in library `options-eod-eodhd`;
frame and `ChainCoverageManifest` are committed in one version, so data
and coverage can never tear apart. The manifest (`schema_version=1`)
carries per-date coverage records: `contract_count` (strict int, from
merged stored observations), `scope` (`"root"` or `"contracts"`),
`selected_contracts`, `reference_contract_count`, `extraction_complete`,
and a `reason` for zero-count dates (`unknown_underlying`,
`source_returned_no_contracts`, `expiry_before_window`, `non_session`,
`source_returned_no_rows`). `conventions` records what is known versus
explicitly unverified (multiplier unavailable, snapshot clock
unverified, OI clearing date unverified, price basis
as-published-unverified-adjustments, and so on).

**A day with `scope="contracts"` is a partial selection.** `read_chain`
still returns `status="observed"` for it — inspect the day's coverage
record and do not treat it as a full chain. Even `scope="root"` means
all source queries were exhausted, not independently proven as-of
listing completeness; survivorship/omitted-listing behavior of the
source is unverified.

## Backfill semantics

- `backfill` is the only mutating entry point. It holds the per-symbol
  lock through extract → old-state read → merge → validate → one
  versioned write. Failures before `write` leave existing data and
  coverage intact; a failed publication may still have advanced the
  native version (no rollback claim is made).
- Merging retains all previous observations. Equal overlapping rows are
  harmless; conflicting overlaps raise `DataValidationError` naming the
  key, leaving storage unchanged. Source revisions are not silently
  overwritten in this build.
- A repeated explicit run re-extracts and publishes a new version; the
  request budget (`per_run_max_requests`, default 200, plus 0.15 s
  pacing) applies per run. A `RequestLimitExceededError` leaves the old
  snapshot untouched — raise the cap explicitly if you need a wider
  extraction; fin3 never substitutes a partial chain.
- Source queries: max-expiration discovery (one page), an over-bound
  guard query, full reference enumeration over `[start, max]`, then
  exhausted per-contract `/eod` histories. A query crossing the source's
  documented offset ceiling (10,000 records) is split by expiration,
  then right, then strike range — never truncated. A single contract
  history exceeding the ceiling fails explicitly.

## No-data results

`read_chain` returns `empty_chain_frame()` plus a reason, never invented
rows: `expiry_before_as_of` (immediate, no snapshot read),
`root_not_ingested`, a stored zero-day `reason` from the manifest
(checked before any min/max range test), `outside_ingested_coverage`
(date outside the span of ingested coverage), `date_not_ingested`
(inside the span but never requested), `no_rows_for_expiry` (optional
expiry filter selects nothing). Reads of a stored non-session
observation return it observed and flagged.

## Configuration

No new dependency and no separate secret plumbing: options ingestion
reuses the raw `EODHD_API_KEY` secret, caller-anchored:

```bash
# .env next to the calling project (fin3 loads dotenv from the process cwd)
EODHD_API_KEY=...              # raw secret, hidden from dumps/errors/reprs
```

```python
config = ClientConfig()        # picks up EODHD_API_KEY (validation alias)

# or pass it explicitly:
config = ClientConfig(EODHD_API_KEY="...")

# or pin provider behavior and let the raw key fill in:
config = ClientConfig(providers={
    "eodhd_options": EodhdOptionsConfig(api_key=None, page_size=500,
                                        per_run_max_requests=512),
})
```

Precedence: an explicit `eodhd_options` config key wins over the raw
secret; otherwise the raw secret fills the default config; if neither
exists, reads still work and ingestion raises
`ConfigurationError("EODHD_API_KEY is required for options backfill")`.
The key is collected by the CLI's secret redaction and excluded from
model dumps, reprs, and error rendering.

Provider settings (`EodhdOptionsConfig`): `base_url` (absolute HTTPS,
trailing slash trimmed), `timeout=30.0`, `per_run_max_requests=200`,
`request_pacing_s=0.15`, `page_size=1000` (source maximum).

## Source, rights, and provenance

- Endpoint family: `https://eodhd.com/api/mp/unicornbay/options`
  (`/contracts`, `/eod`), paginated with literal `page[offset]` /
  `page[limit]` keys and `links.next` continuations; `compact=0` is sent
  on `/eod`. Filters used: `filter[underlying_symbol]`,
  `filter[contract]`, `filter[exp_date_from/to]`, `filter[strike_from/to]`,
  `filter[type]`. Equal from/to bounds are inclusive.
- Observation labels come from the per-row EOD id suffix; there is no
  observation-date filter on the source, and `tradetime` is last
  recorded market activity, not necessarily a trade.
- EODHD's [terms](https://eodhd.com/financial-apis/terms-conditions)
  permit private, non-commercial storage and analysis and forbid
  redistribution, public display, and access grants; the
  [storage help page](https://eodhd.com/help/storage) permits retaining
  personal-plan downloads after cancellation. Marketplace-supplier
  applicability and permission for public short-quote emission are
  **unverified**; fin3 therefore publishes no paid quotes and claims no
  redistribution rights beyond private non-commercial use.
- Explicitly unverified (recorded as such in the manifest): exact
  snapshot clock, naive quote timezone, OI clearing-date convention,
  volume convention, zero-quote meaning, survivorship status, and the
  vendor's adjustment convention for historical prices/strikes.

finlab (or any consumer) points at this dataset purely via its own
`ClientConfig` — its own MinIO endpoint/credentials and the movable
bucket — by importing fin3 and calling `OptionsDataFetcher.read_chain`.
Two fresh-client reads return frame-equal data with zero writes and zero
vendor calls.
