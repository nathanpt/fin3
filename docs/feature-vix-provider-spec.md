# Feature spec — daily volatility-index data (VIX family)

Status: proposed · Date: 2026-11-05 · Requested by: finlab (sibling repo,
`/mnt/dev/projects/finlab`) · **Implementation plan: separate document,
produced by a planning agent after repo investigation — deliberately not
included here.**

## Summary

fin3 gains daily volatility-index data — at minimum the CBOE VIX index and
VIX3M — retrievable through fin3's standard access path, so finlab can compute
volatility-level and term-structure regime indicators (e.g., VIX level buckets,
VIX3M/VIX contango/backwardation) as pure functions over data it already reads
through the `db` boundary. finlab-side code changes: symbol configuration only.

## Acceptance criteria (implementation-agnostic)

1. **Access:** fin3's standard data-access call returns daily index closes for
   VIX (and VIX3M) over a defined date range, through the same call pattern
   used for equity bars. The exact call and observed output are recorded.
2. **Resolution and shape:** resolution `ONE_DAY`; values are index close
   levels (not returns); history extends back at least far enough to cover a
   decade of regime lookback, or the maximum the chosen source allows —
   whichever is less; the achieved range is recorded.
3. **Provider pattern:** data arrives via a provider registered in fin3's
   standard provider registry; the storage endpoint comes from configuration
   only — no hardcoded host, no hardcoded path (same invariant as finlab
   ADR 0002).
4. **Provenance:** the chosen data source (CBOE published CSVs, FRED `VIXCLS`
   and related series, or a databento dataset if the current plan covers CBOE
   indices) is recorded with retrieval date, source URL or API name, and
   license/redistribution terms for personal research use.
5. **Licensing check is explicit:** the planning agent verifies the source's
   terms for personal, non-redistributed research use and records the finding;
   if terms are unclear, the source is flagged rather than silently adopted.
6. **Quality/validation:** the data passes fin3's existing validation and
   quality machinery on the same terms as other providers (fixtures follow the
   existing `tests/fixtures/quality/` pattern).
7. **Tests:** an integration test exercises the real access path with recorded
   output; unit/integrity tests follow fin3's existing suite conventions.
8. **finlab consumption:** finlab reads VIX/VIX3M through its `db` boundary
   with configuration-only changes (symbol names); no finlab code changes are
   required by this feature, and nothing in finlab hardcodes a symbol list
   beyond its own config.

## Non-goals

- VX futures or futures term structure; intraday resolution; any sub-daily
  granularity.
- Computing derived indices (VIX9D, VVIX) unless the chosen source provides
  them for free as part of the same mechanism — optional bonus, not required.
- Any redistribution of CBOE-derived data beyond the owner's personal
  research use.
- Redistribution of this data to any third party or service.
- The implementation plan itself — owned by the fin3-side planning agent.

## Context

finlab's regime analysis (2026-11-05 design session) identified
volatility-index regimes as the standard, best-documented regime classifier
and the only regime family not computable from equity bars alone. fin3 today
carries equity bars only. This feature unblocks finlab's volatility-index
regime indicators; the indicators themselves are finlab-side work and stay
pure functions over retrieved data.

---

## Source provenance and inspection record (2026-11-05)

Recorded during planning via read-only inspection of the actual endpoints.

### Cboe full-history CSVs (selected source)

|Symbol|Source URL|Rows at inspection|First date|Last date at inspection|
|---|---|---:|---|---|
|VIX|https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv|9309|1990-01-02|2026-11-04|
|VIX3M|https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv|4309|2009-09-18|2026-11-04|

- Both endpoints returned HTTP 200 with header `DATE,OPEN,HIGH,LOW,CLOSE`; no
  volume column and no pagination. Dates were unique and fields finite.
- VIX contained **47 rows violating OHLC inequalities**; VIX3M none. This
  feature consumes `DATE` and `CLOSE` only; it does not promise usable
  opens/highs/lows and does not repair published OHLC.
- Cboe history includes some non-equity-session dates (e.g. 2023-07-04);
  standard-path alignment excludes such rows rather than shifting them.
- Official history page:
  https://www.cboe.com/tradable_products/vix/vix_historical_data
- Terms: https://www.cboe.com/terms (§2, last updated 2022-11-16) permit one
  personal, non-commercial copy in connection with Cboe products/services and
  then restrict other copying, electronic-retrieval storage, derivative
  works, and redistribution without consent. **Recurring programmatic
  retrieval and private ArcticDB/research storage rights remain unclear; this
  is not a license-clearance finding.** Copyright notices and attribution are
  preserved. No data redistribution, public data fixtures, third-party data
  service, or automated acceptance of terms.

### Rejected alternatives

- **FRED** (`VIXCLS`; `VXVCLS` from 2007-12-04): https://fred.stlouisfed.org/legal/terms
  restricts storage/cache/database incorporation in general prohibitions (p)
  and API prohibition (l). Its personal-use summary and "Citation Required"
  series tags do not override those restrictions.
- **Databento DBIX indices** (https://databento.com/indices): VIX3M coverage,
  entitlements, and storage rights not established. Databento routing
  unchanged.

### Owner-approved interpretations (acceptance-contract bindings)

- **Pagination:** the CSV source has no pages — recorded as *not applicable*.
  Two distinct live range checks replace the repository's two-page
  requirement for this provider. Date slices and the two symbols are never
  described as pagination.
- **finlab consumption:** finlab has empty source packages, no implemented
  `db` module, no runtime dependency on fin3, and no integration
  implementation. Its accepted decision
  (`finlab/docs/decisions/0002-access-market-data-through-fin3.md`) specifies
  direct fin3 imports. The proof runs from the finlab working directory with
  finlab's own configuration and the fin3 interpreter and demonstrates that
  documented import contract; it is not a test of an existing finlab `db`
  implementation and adds no finlab dependency.
- **Rights gate:** build the Cboe code and synthetic verification, but require
  documented rights clearance covering automated retrieval, private durable
  storage, and personal non-redistributed research before live ingestion or
  live verification. Without it, report "code-complete but NOT verified
  against the live API". A test opt-in is not legal permission, and
  planning-time source inspection is not proof of the eventual
  provider/storage implementation.
