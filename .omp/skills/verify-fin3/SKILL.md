---
name: verify-fin3
description: End-to-end verification for fin3 — drive the real fin3 CLI process and the MarketDataFetcher library API against ephemeral LMDB fixtures (and a read-only MinIO check) to prove features work, before claiming done.
---

# Verify fin3

Proves behavior on the real surfaces, not unit-mock proxies. Run from the repo
root.

## Paths

- Control script: `scripts/verify.py` — one entry point, JSON output by
  default, `--human` for text.
- Feature map: `.omp/skills/verify-fin3/references/features/README.md` — what
  each feature does, how to drive it, what to assert, and its traps.

## Commands

```
uv run python scripts/verify.py all        # offline set: cli, plan, idempotent
uv run python scripts/verify.py cli        # real fin3 process: help, envelopes, exit codes
uv run python scripts/verify.py plan       # manifest planning: skip/fill/create, ensure vs sync, dry-run no-write
uv run python scripts/verify.py idempotent # get_data covered-range read-only no-op
uv run python scripts/verify.py store      # read-only status vs configured MinIO (needs .env)
```

Exit 0 = all passed, 1 = a check failed, 2 = setup error. Each assertion
carries `actual`/`expected` and, on failure, a `fix` hint. Nothing persistent
is written (temp LMDB dirs are deleted; `store` is read-only).

## Rules

- Run `all` before claiming any CLI/planner/retrieval change done; run
  `store` when touching config, storage, or anything the MinIO path sees.
- A red check means the feature regressed; a crashed check means the driver
  broke — read `assertions[].fix`, don't rerun blindly.
- Unit suite + this script are complements, not substitutes: the script covers
  component boundaries the unit suite cannot see.
