# Resource Monitoring — Implementation Notes

**Date**: 2026-06-18
**Commits**: `a06542b` (feature) → `b72ed44` (disk-scan optimization)

Reference notes on the resource-monitoring feature and the disk-scan
optimization that followed. Captured for future work that touches
ArcticDB size queries or the monitoring pipeline.

---

## What Was Built

Phase 2 roadmap item **"Resource monitoring — disk/memory/network tracking"**.

A `ResourceTracker` context manager that instruments every `get_data()` call
and surfaces resource usage via a **live info bar** plus a summary panel on
completion. **On by default** — wired into `MarketDataFetcher.get_data()`, no
flags needed. Display is environment-aware:

- **Inside tmux**: a dedicated monitor pane opens (separate process reading
  a shared JSON metrics file at 2 fps).
- **Native terminal (no tmux)**: an inline `rich.live` bar redraws in place
  on stderr; `redirect_stderr=True` routes structlog JSON lines above the bar
  so they interleave cleanly.
- **Piped / CI / non-TTY**: no live display; summary panel to stderr only.

- `fin3/monitoring/` — `tracker.py`, `collector.py`, `render.py`, `tmux.py`, `display.py`
- Memory: `psutil` RSS sampling (background thread, peak delta)
- Disk: ArcticDB symbol sizes (before/after delta + library total)
- Network: application-level byte counting via provider `fetch()` wrapping
- tmux pane runs `python -m fin3.monitoring.display <metrics.json>`, tails
  the file at 2 fps
- `tmux.py` uses `sys.executable` so the pane runs under the same venv as
  the parent (critical when fin3 lives in a project venv, not system-wide)

See `docs/USAGE.md` → "Resource Monitoring" for usage and example output.

---

## The Disk-Scan Optimization (why it matters for future work)

### The problem

The first implementation computed the "library total" display line by summing
`ArcticStorage.get_symbol_size()` across **every** symbol in the library —
once on enter, once on exit. For the 190-symbol `equities-1d-databento`
library over network MinIO this added **~1.5s on enter + ~1.5s on exit (~3s
total)**, blocking startup before any user-visible progress.

It was also **inaccurate**: `get_sizes_for_symbol` always returns
library-global key types (e.g. `VERSION_REF`) per symbol, so summing across
N symbols double-counts them (reported 14.3 MB vs the true 11.9 MB).

### The fix

ArcticDB exposes a single-call library-level size API:
`lib.admin_tools().get_sizes()` — documented in
`docs/arcticdb_docs/tutorials-library_sizes.md`.

- Added `ArcticStorage.get_library_size()` — one `get_sizes()` scan for the
  whole-library total. One backend round-trip instead of N, and the correct
  total (no double-counting).
- Split `compute_disk_delta` into:
  - `compute_symbol_sizes(storage, library, symbols)` — affected symbols
    only (cheap; a few symbols for a typical `get_data` call)
  - `compute_library_size(storage, library)` — single scan
- `ResourceTracker` now measures **only affected symbols on enter** (fast
  startup) and **defers the single library scan to exit** (the library total
  is only shown in the final summary, never the live panel).

### Measured result (190-symbol library, network MinIO)

| | Before | After |
|---|---|---|
| Library total method | N × `get_sizes_for_symbol` | 1 × `get_sizes()` |
| Network round-trips | ~190 | 1 |
| Enter overhead | ~1.5s (whole-library scan) | ~0.28s (affected only) |
| Exit overhead | ~1.5s (whole-library scan) | ~0.19s (affected + 1 scan) |
| Total disk overhead | ~3s | ~0.5s (~6x) |
| Library total accuracy | Over-counted (14.3 MB) | Accurate (11.9 MB) |

Empirical micro-benchmark of `get_sizes()` alone showed **13.7x** speedup
(1.53s → 0.11s) for the library total.

---

## Lessons For Future Work

1. **Prefer library-level ArcticDB admin APIs over per-symbol loops.**
   `admin_tools().get_sizes()` (whole library) and
   `admin_tools().get_sizes_by_symbol()` (all symbols at once) each cost one
   round-trip. Looping `get_sizes_for_symbol` over N symbols costs N
   round-trips — prohibitively slow over network backends.

2. **`get_sizes_for_symbol` is the wrong tool for library totals.** It always
   includes library-global key types (`VERSION_REF`, etc.) per call, so
   summing across symbols double-counts them. Use `get_sizes()` for totals.

3. **Defer expensive reads to when they're actually displayed.** The library
   total only appears in the final summary, so computing it on enter was pure
   waste. Measure affected symbols (cheap) up front; measure the library total
   once, at exit.

4. **`scripts/*.py` can't `import fin3` when run directly** because Python
   prepends the script's dir to `sys.path`, not the cwd. `fin3` isn't
   installed in the venv (resolves via cwd). Run from project root, or install
   editable (`uv pip install -e .`). Pre-existing issue, not introduced here.

5. **`databento` is an optional extra** (`pip install fin3[databento]`) and
   isn't installed in the default dev env. `tests/test_providers.py` fails on
   import for this reason — unrelated to monitoring work.

---

## Verification

- `uv run python -m pytest tests/ --ignore=tests/integration --ignore=tests/test_providers.py`
  → 202 passed
- `uv run ruff check fin3/` → clean
- `uv run mypy fin3/` → clean
- Live tmux smoke test confirmed: monitor pane opens, updates in real time
  (fetch count, net bytes, duration, phase all ticking), closes on exit,
  summary panel renders to main pane with correct metrics.
