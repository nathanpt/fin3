# Concurrent Access Protection

## Context

fin3's `MarketDataFetcher.get_data()` has a documented **check-then-act race**.
When two processes call `get_data("AAPL", ...)` concurrently:

1. Both run `_symbol_gaps()` (`fin3/core.py:176`) and detect the **same gap**.
2. Both `provider.fetch(...)` the same data (wasted API cost).
3. Both call `ArcticStorage.update(...)` (`fin3/storage/arctic.py:266`).
4. Because every write hardcodes `prune_previous_versions=True` (lines 261, 282,
   384), the second write clobbers the first with **no rollback**. Recovery
   requires a MinIO infrastructure-level snapshot (`.docs/DESIGN.md:690`).

Today there is **zero** concurrency protection on the data path (the only locks
in the repo are in the unrelated `fin3/monitoring/` resource counters). This
feature adds cross-process mutual exclusion scoped per `(library, symbol)` so
that concurrent calls serialize per-symbol without blocking unrelated symbols.

**Trigger / deployment model:** the library runs on a **single shared dev
server** where multiple processes may call `get_data()` simultaneously. This is
the assumed deployment, which informs the locking choice below.

## Approach

**File-based advisory locks via `fcntl.flock`, scoped per `(library, symbol)`.**

- `flock(LOCK_EX)` provides cross-process mutual exclusion on the same host and
  **auto-releases on process exit/crash** — a crashed process never leaves a
  stale lock, so no recovery/repair path is needed. This is the decisive
  advantage over homegrown MinIO-metadata locks.
- Stdlib only (`fcntl`, `os`, `time`, `threading`) — **no new dependencies**
  (keeps `AGENTS.md`'s "no dep changes without sign-off" rule satisfied).
- Lock granularity = `(library, symbol)`. Two processes fetching **different**
  symbols never block each other; only same-symbol contention serializes.
- **No reentrancy required.** Verified: `_ensure_symbol` acquires → fills gaps →
  releases *before* the separate `defrag` loop runs (`core.py:86-88`). So
  `defragment_symbol` / `delete_symbol` can acquire their own non-nested lock.

**Why not the other roadmap options:**
- *MinIO-metadata locks* — require adding a MinIO/S3 client dependency (none
  exists today) and conditional-PUT dance; harder to test; no benefit on a
  single host. File locks are strictly simpler here.
- *ArcticDB staged writes* (`write(staged=True)` + `finalize_staged_data()`) —
  these merge concurrent writes to a symbol but do **not** prevent the redundant
  `provider.fetch()` (wasted cost), which is a core goal. Orthogonal; not chosen.

### Timeout & diagnostics
Non-blocking `flock(LOCK_EX | LOCK_NB)` in a poll loop with a configurable
`timeout`. On timeout, raise `LockAcquisitionError` whose message reports the
holder's **PID, hostname, and lock-file path** (written to the lock file on
acquire) so operators can identify a stuck holder. **Deadlock is structurally
impossible** — each operation holds at most one lock at a time, so there are no
circular waits.

### Configurability
A new `LockConfig` (env prefix `FIN3_LOCK__`): `enabled` (default `True`),
`lock_dir` (default `/tmp/fin3/locks`), `timeout_s` (default e.g. 600),
`poll_interval_s` (default 0.5). Existing tests can set `enabled=False` to avoid
any lock-file I/O if needed.

## Files to modify

| File | Change |
|---|---|
| `fin3/storage/locking.py` *(new)* | `SymbolLock` class + `LockAcquisitionError`. |
| `fin3/config/settings.py` | `LockConfig` model; add `lock: LockConfig` to `ClientConfig`. |
| `fin3/storage/arctic.py` | `ArcticStorage.__init__` accepts `lock: LockConfig`; expose `lock_symbol(library, symbol)` context manager; guard `delete_symbol` (288) + `defragment_symbol` (364). |
| `fin3/core.py` | `MarketDataFetcher.__init__` passes `config.lock` to `ArcticStorage`; wrap `_ensure_symbol` body (273) in `with self._storage.lock_symbol(...):`. |
| `tests/test_locking.py` *(new)* | Unit tests for the `SymbolLock` primitive (filesystem-only, no MinIO). |
| `tests/test_core.py` | Assert `_ensure_symbol` acquires the lock (spy/mock). |
| `tests/test_storage.py` | Assert `delete_symbol`/`defragment_symbol` acquire the lock. |
| `tests/integration/test_concurrency.py` *(new)* | Real cross-process double-fetch prevention (gated by `-m integration`). |
| `.docs/ROADMAP.md` | Mark "Concurrent access protection" item complete. |
| `docs/USAGE.md` / `.docs/DESIGN.md` | Document the locking behavior + config. |

## Reuse

- `ArcticStorage` (`fin3/storage/arctic.py:49`) — the shared collaborator both
  `MarketDataFetcher` and mutators go through; natural home for `lock_symbol()`.
- `library_name()` (`fin3/schemas.py:88`) — produces the library component of the
  lock key, keeping lock keys consistent with storage keys.
- `ClientConfig` / env-nesting pattern (`fin3/config/settings.py:53`) — reuse for
  `LockConfig` so `FIN3_LOCK__TIMEOUT_S` etc. work automatically.
- `tests/conftest.py:28` `storage` fixture (LMDB-backed) + `tests/test_core.py:19`
  `_make_fetcher()` bypass pattern — for lock integration tests without MinIO.
- `tests/integration/conftest.py:124` `unique_library` fixture — pattern for
  per-test isolation in the new integration test.

## Implementation steps (sub-agent driven)

This is a sub-agent driven implementation. Each step names the sub-agent's
**contract** (inputs, outputs, dependencies) so it can be dispatched to an
isolated worktree. Waves run where dependencies allow parallelism.

### Wave 1 — foundation (parallel, 2 isolated worktrees)

**[ ] Step 1 — Agent A: `SymbolLock` primitive + unit tests**
- *Contract:* Create `fin3/storage/locking.py` defining:
  - `class LockAcquisitionError(RuntimeError)`.
  - `class SymbolLock`: `__init__(self, lock_dir, timeout_s, poll_interval_s)`;
    context-manager `acquire(library, symbol)` / `__enter__` / `__exit__` using
    `fcntl.flock(LOCK_EX | LOCK_NB)` in a poll loop; writes `{pid}\n{hostname}\n`
    to the lock file on acquire; raises `LockAcquisitionError(timeout, holder_pid,
    hostname, path)` on timeout. Sanitize symbol names for filename safety
    (replace `/`). Create `lock_dir` with `os.makedirs(..., exist_ok=True)`.
    Unix-only (`fcntl`); guard the import so non-Unix import doesn't crash.
- *Also:* write `tests/test_locking.py` — acquire/release, timeout raises, stale
  lock auto-released when a child process exits, and same-symbol contention via
  `multiprocessing` (worker tries to acquire while parent holds; asserts it waits
  then succeeds on release). No MinIO needed (flock is pure filesystem).
- *Depends on:* nothing. Fully isolated.

**[ ] Step 2 — Agent B: `LockConfig` settings plumbing**
- *Contract:* Add `class LockConfig(BaseModel)` to `fin3/config/settings.py`
  (`enabled: bool = True`, `lock_dir: str = "/tmp/fin3/locks"`,
  `timeout_s: float = 600.0`, `poll_interval_s: float = 0.5`); add
  `lock: LockConfig = LockConfig()` to `ClientConfig`. Add a quick
  `tests/test_config.py` assertion that `FIN3_LOCK__TIMEOUT_S` parses.
- *Depends on:* nothing. Fully isolated.

> After Wave 1 merges into the base branch, Wave 2 starts.

### Wave 2 — wiring (single agent, depends on Wave 1)

**[ ] Step 3 — Agent C: wire the lock into storage + fetcher**
- *Contract:*
  - `ArcticStorage.__init__` gains a `lock: LockConfig` param (store a
    `SymbolLock` or `None` when disabled); add `lock_symbol(library, symbol)`
    returning the context manager (no-op when disabled). Update `from_lmdb()`
    (`arctic.py:131`) to default-disable locking (LMDB is single-process).
  - `MarketDataFetcher.__init__` passes `config.lock` into `ArcticStorage`.
  - Wrap the body of `_ensure_symbol` (`core.py:273`) in
    `with self._storage.lock_symbol(lib_name, symbol):`.
  - Guard `ArcticStorage.delete_symbol` (288) and `defragment_symbol` (364) with
    the same `lock_symbol(...)` context manager.
  - Add assertions to `tests/test_storage.py` (delete/defrag acquire lock) and
    `tests/test_core.py` (spy `lock_symbol`, assert called once per symbol, and
    that two distinct symbols use distinct lock keys).
- *Depends on:* Step 1 + Step 2 merged.

### Wave 3 — integration tests + docs (single agent)

**[ ] Step 4 — Agent D: cross-process integration test + docs**
- *Contract:*
  - `tests/integration/test_concurrency.py`: spawn two `multiprocessing`
    workers that both call `get_data()` on the **same** symbol against real
    MinIO; use a `threading.Event`/deadline pattern (no `pytest-timeout` dep) and
    a provider stub that records fetch-call count; assert exactly **one** fetch
    occurs and the result is correct. Use `os.getpid()`-based library naming
    (avoid the non-multiprocess-safe `_lib_counter`, `integration/conftest.py:117`).
    Inherits the `-m integration` env-gated skip automatically.
  - Update `.docs/ROADMAP.md` (check off the item), `docs/USAGE.md`,
    `.docs/DESIGN.md` Section 10 with the locking behavior + config env vars.
- *Depends on:* Step 3 merged.

## Verification

1. **Unit tests (no infrastructure):**
   `uv run pytest tests/test_locking.py tests/test_config.py -v`
2. **No regressions:**
   `uv run pytest tests/ -v` (LMDB-backed; lock auto-disabled via `from_lmdb`).
3. **Lint & types:**
   `uv run ruff check . && uv run mypy fin3/`
4. **Cross-process correctness (real MinIO, env-gated):**
   `uv run pytest tests/integration/test_concurrency.py -m integration -v`
5. **Manual end-to-end:** in two shells on the dev server, run `get_data()` for
   the same symbol concurrently; confirm the second serializes behind the first
   (logs/`tracker` phase shows waiting) and that only one provider fetch occurs.
