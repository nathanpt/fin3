# Manifest planning

`load_manifest` (`fin3/manifest.py`) turns a strict YAML/JSON declaration
(`version: 1`, `id`, `libraries: {dataset_id: {asset_type, provider,
resolution, symbols.include, start, end, update_policy}}`) into resolved
datasets with canonical library names
(`{asset_type}-{resolution}-{provider}`, e.g. `crypto-1d-binance`) and a
stable `declaration_hash`. `ManifestRunner` (`fin3/sync.py`) compares stored
coverage against each declaration and plans one action per symbol.

## How to reach it

Precondition: none — the check builds an ephemeral LMDB store (temp dir,
auto-deleted) seeded with `VERIFYUSDT` daily bars for 2026-01-01..2026-01-10
in `crypto-1d-binance`. Nothing persists.

## How to drive it

```
uv run python scripts/verify.py plan --human
```

The check drives `fin3.cli.main()` in-process through its documented seams
(`cli._load_config`, `cli._build_runner` — "tests monkeypatch this"), injecting
the LMDB `ArcticStorage`, then runs:

1. `status` on a manifest declaring exactly the seeded window → plans `skip`.
2. `status` on a manifest declaring 2026-01-01..2026-01-20 → plans `fill`
   whose first fetch range starts `2026-01-11T00:00:00+00:00`.
3. `sync --dry-run --json` on the gapped manifest → exit 0, report says
   `dry_run: true`, and row count + ArcticDB version count are unchanged.
4. `ensure --dry-run` (the creation-planning path, never fetches):
   - on the existing covered symbol → `skip` reason `already_exists`
     (ensure never fills; that is sync's job);
   - on a manifest declaring `NEWSYM` → `create`, status `planned`, fetch
     ranges from the window start, `created_libraries` empty, and NEWSYM
     still absent afterwards;
   - on a manifest with an absent library (`crypto-1h-binance`) →
     `planned_libraries == ["crypto-1h-binance"]` and the library is still
     absent afterwards.

## What to assert

- Action vocabulary is fixed: `skip | fill | create | append | refresh`.
- A covered range plans `skip`; a missing tail plans `fill` starting at the
  first uncovered UTC day.
- Dry-run is self-describing (`dry_run: true`) and provably writes nothing
  (version count unchanged).
- Exit 0 with `totals.failed == 0` for a healthy plan.

## Traps

- `ensure` and `sync` plan differently for the SAME fixture: an existing
  symbol is `skip(already_exists)` under ensure but `skip(covered)` or
  `fill` under sync. The reason string is the discriminator — assert it.
- Dry-run/status preview results carry `status: "planned"` and
  `created_libraries: []`; `planned_libraries` lists libraries ensure
  *would* create (absent at plan time), computed as
  `{library for plan if not plan.library_exists}`.

- `totals.created/updated/skipped/failed` are *application* counters; in
  `status`/`--dry-run` mode they do NOT count planned actions (a planned
  `fill` reports `totals.updated: 0`). Assert on `results[].action`.
- `status` and `ensure/sync --dry-run` are offline: no provider fetch, and no
  cost estimator call (the estimator only runs when `--max-cost` is passed,
  and Databento's estimator is a network call).
- Provider preflight (`ManifestRunner._preflight_providers`) only requires the
  provider to be *configured* (`FIN3_PROVIDERS__<NAME>__PROVIDER_TYPE=...`),
  not reachable. `binance` is keyless and safe to register for offline runs.
- Crypto gap planning happens per UTC day with an unfinished-bar exclusion
  (bar close <= resolved_at); fixed past windows keep plans deterministic.
- The manifest loader is strict: extra keys, wrong `version`, duplicate YAML
  keys, and naive timestamps are all configuration errors (exit 2).
