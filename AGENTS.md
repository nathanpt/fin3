# fin3 — Agent Guide

Declarative financial time-series library: fetch, validate, store, and retrieve
OHLCV bars (ArcticDB on MinIO/S3; LMDB for tests). Pluggable providers return
clean pandas DataFrames for backtesting. Python 3.11+, uv-managed.

## Commands

| Task | Command |
|---|---|
| Setup | `uv sync --all-extras` |
| Unit tests | `uv run pytest tests/ -v` |
| Integration (gated) | `uv run pytest tests/ -m integration -v` — needs real MinIO + provider API keys |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Types | `uv run mypy fin3/` (strict) |
| Docs site | `uv run mkdocs serve` |

## Hard constraints

1. **Strict typing**: `uv run mypy fin3/` must stay clean. Lint/format via ruff
   only — never flake8/black/isort.
2. **No new dependencies** without explicit user authorization (never touch
   `pyproject.toml` dependencies unprompted).
3. **Explicit exceptions only**; never `assert` in `fin3/`. Domain errors live
   in `fin3/exceptions.py`.
4. **Providers are pure**: return DataFrames only, no ArcticDB/storage access;
   register via `@ProviderRegistry.register("<name>")`.
5. **Real-download verification is mandatory for any network provider** before
   it is called done: run a real `fetch()` and inspect actual response shapes,
   exercise pagination across ≥ 2 pages, and check `get_instrument_bounds()`
   against a known symbol. Mocked tests are necessary but never sufficient.
   Anything not live-verified must be reported as "code-complete but NOT
   verified against the live API" — never as done/shipped.
6. **Update `.docs/ROADMAP.md` and `.docs/PROGRESS.md`** after completing items.

## Internal docs (`.docs/` — gitignored, local-only)

`.docs/` is intentionally untracked (see `.docs/decisions/0001-*.md`). On a
fresh clone it may be absent: restore the roadmap with
`git show d536593^:docs/ROADMAP.md > .docs/ROADMAP.md` and recreate the rest
from this guide.

- `.docs/ARCHITECTURE.md` — module map, data flow, invariants, verification
  ladder. **Read before changing system boundaries or module structure.**
- `.docs/ROADMAP.md` — phased roadmap. **Read before selecting work; check off
  completed items.**
- `.docs/PROGRESS.md` — session state, verification results. **Update at
  session end.**
- `.docs/feature-list.json` — machine-readable feature contract. **Pick one
  highest-priority non-passing feature; only change status/evidence fields.**
- `.docs/exec-plans/` — `active/` for in-flight plans, `completed/` for
  finished ones. **Archive plans when done.**
- `.docs/decisions/` — MADR records. **Read before changing a decision they
  constrain; write an ADR for choices affecting architecture, interfaces,
  storage, security, dependencies, or major user-visible behavior.**
- `.docs/references/` — pointers to external reference material.
- `README.md` + `docs/` — published MkDocs site (GitHub Pages). **Keep
  user-facing docs in sync with behavior changes.**

## Change discipline

- Before adding code or scaffolding, inspect the relevant paths and ask whether
  the requested outcome can be met by deleting, reusing, configuring, or
  extending something already present.
- Check existing implementations, platform facilities, standard-library
  functions, and installed dependencies before adding new code or dependencies.
- Implement the smallest complete change that satisfies the requirement. Do not
  add speculative abstractions, generality, files, or configuration.
- Do not reduce validation, error handling, security checks, accessibility,
  tests, observability, or readability merely to reduce lines.

## Completion & reporting

- Run the verification level matching the changed surface (see
  `.docs/ARCHITECTURE.md` § Verification): focused units for isolated logic,
  full unit suite + ruff + mypy for module boundaries, real end-to-end download
  for any provider network path.
- Record exact commands and observed results in `.docs/PROGRESS.md`.
- State skipped or unavailable verification as `unverified`, never as passing.
- Keep `README.md`, `docs/USAGE.md`, and `docs/dataset-comparison.md` in sync
  when provider or config behavior changes.
