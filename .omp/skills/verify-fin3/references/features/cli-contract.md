# CLI contract

The `fin3` console script (`fin3.cli:main`) is the consumer surface for
declarative manifests: `ensure` (create missing libraries/symbols), `sync`
(reconcile stored data with the manifest), `status` (read-only preview of
`sync`). Anything a user notices about the CLI is observable here: help text,
exit codes, JSON error envelopes, secret redaction.

## How to reach it

From the repo root. Config comes from the environment with the `FIN3_` prefix
(nested fields use `__`, e.g. `FIN3_MINIO__ENDPOINT`) or the repo-root `.env`
file; real environment variables override `.env`.

## How to drive it

```
uv run python scripts/verify.py cli --human
```

That check spawns the real process four times:

1. `fin3 --help` — exit 0, lists `ensure`/`sync`/`status`.
2. `fin3 status <manifest with version 99> --json` — exit 2, stdout is one
   JSON value `{"error": {"kind": "configuration", ...}}`.
3. `fin3 frobnicate` — argparse rejects, exit 2.
4. valid manifest + `FIN3_MINIO__ENDPOINT=127.0.0.1:1` (closed port) +
   `FIN3_PROVIDERS__BINANCE__PROVIDER_TYPE=binance` — storage connect fails →
   exit 1, envelope kind `operational`.

## What to assert

- Exit codes: 0 success, 1 operational error **or** failed targets, 2
  configuration/validation errors **and** argparse usage errors.
- With `--json`, stdout is exactly one parseable JSON value — either a report
  (`manifest_id`, `totals`, `results[]`) or `{"error": {"kind": ...}}`.
- Secret values (API keys, MinIO credentials, raw `EODHD_API_KEY`) never
  appear in any rendered message; they become `***` (`cli._redact`).

## Traps

- Exit code 2 covers BOTH pydantic/validation errors and argparse errors; do
  not treat "exit 2" as proof of manifest validation alone.
- A valid manifest against unreachable storage exits 1 (operational), not 2 —
  the manifest parsed, the world failed.
- `fin3 status` never fetches market data and never creates buckets/libraries
  (see [Manifest planning](manifest-planning.md) for the offline guarantees).
- The in-process checks silence stdout at the **file-descriptor** level:
  structlog, rich, and the Databento SDK print to the real stdout, and the
  SDK holds a stream reference captured at import time, so
  `contextlib.redirect_stdout` alone does not keep JSON output parseable.
