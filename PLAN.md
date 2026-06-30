# Massive Provider (formerly Polygon.io)

## Context

fin3's roadmap declares **provider breadth the live priority** now that the
MkDocs site has shipped (`docs/`). Binance ✓ (crypto) and Yahoo ✓ (free
equities) are done; **Massive** (the 2025-10-30 rebrand of Polygon.io) and
ThetaData remain to round out the multi-provider promise.

Three things make this the right story *today*:

1. **There is no provider yet, only a stale stub.** `PolygonConfig` exists at
   `fin3/config/settings.py:43` but is a 4-line placeholder (`api_key` only) —
   there is no `MassiveProvider`, no tests, no registry entry, and no docs.
2. **The rebrand is mid-transition and time-sensitive.** Per the roadmap, build
   against `massive` / `api.massive.com`; the legacy `polygon` / `api.polygon.io`
   names are deprecated. Since there are **no production users** of the stub
   yet, this is the closing window for a clean rename (`PolygonConfig` →
   `MassiveConfig`, key `"polygon"` → `"massive"`) instead of a
   backward-compat shim that would then live forever.
3. **A proven template exists.** `BinanceProvider` (`fin3/providers/binance.py`)
   is a fully-tested stdlib-HTTP REST provider with pagination, rate-limit
   backoff, cost, and lifecycle bounds — Massive mirrors it almost directly.

The goal is a single-branch, single-headline release: *"Add the Massive
(formerly Polygon) provider for paid US-equity OHLCV."*

## Approach

**A stdlib-HTTP REST provider wrapping Massive's aggregates endpoint, mirroring
the `BinanceProvider` pattern, with a `PolygonConfig → MassiveConfig` rename.**

- **HTTP via `urllib.request` (stdlib only).** There is no `requests`/`httpx`
  anywhere in `fin3/` today — Binance uses `urllib.request` + `urllib.error`.
  Following the same keeps the install lean and satisfies AGENTS.md's "no dep
  changes without explicit user authorization" rule. **No new dependency, no new
  optional extra.** (Unlike `databento`/`yfinance`, which wrap a PyPI SDK and
  ship as `fin3[databento]`/`fin3[yfinance]`, Massive v1 uses plain REST — so
  `pyproject.toml` is untouched.)
- **Endpoint:** `GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`
  with `adjusted`, `sort`, `limit`, and `apiKey` query params. US equities v1
  scope only (options/forex/futures/crypto out of scope — crypto is Binance's).
- **Resolution mapping → `(multiplier, timespan)`:** `1m→1×minute`,
  `5m→5×minute`, `15m→15×minute`, `1h→1×hour`, `1d→1×day`, and — unlike Yahoo —
  **`4h→4×hour` natively** (Massive supports an arbitrary multiplier), so **no
  `_aggregate_bars` fallback is needed**.
- **Raw-default price basis.** `adjusted=false` default for parity with
  Databento and fin3's store-raw-canonical philosophy (matches Yahoo's
  `auto_adjust=False`); toggle via config for split/dividend-adjusted OHLC.
- **Timestamps in milliseconds.** Massive's aggs `results[].t` is a **Unix
  epoch in milliseconds**, not seconds. *(Correction: the ROADMAP note says
  "Unix seconds, UTC" — that is inaccurate for the aggs endpoint and would
  produce 1970-era dates. Normalise with `pd.to_datetime(..., unit="ms",
  utc=True)`, exactly as Binance does.)*
- **Cursor-based pagination via `next_url`.** Unlike Binance (which advances a
  `startTime` cursor), Massive returns a `next_url` field when more pages
  remain. Page at `limit=50000` and follow `next_url` until it is absent. This
  is the one structural divergence from the Binance template and must be
  designed for explicitly (see "Pagination" below).
- **Rate limiting & retry.** Same `_request` / `_request_with_retry` split as
  Binance: 429 → `ProviderRateLimitError` (retried), socket timeout →
  `ProviderTimeoutError` (retried), other HTTP errors → `ProviderError`
  (fatal, not retried). Honor `Retry-After` when present. Cap via `max_retries`
  / `initial_backoff` / `max_backoff` on `MassiveConfig`.
- **Auth.** API key sent via the `apiKey` query parameter (the legacy
  `Authorization: Bearer` header also works; query param is the documented
  default and simplest). Paid subscription required for real data; a limited
  free tier exists.
- **Cost.** `estimate_cost()` returns `0.0` — Massive is subscription-based and
  exposes no per-query cost. Document that `MarketDataFetcher`'s `max_cost`
  ceiling is **not enforced** for this provider (it already handles this via
  `hasattr(prov, "estimate_cost")` at `core.py:84` and sums cost at `core.py:260`).
- **Lifecycle bounds.** `get_instrument_bounds()` probes the earliest daily agg
  (`/range/1/day/1970-01-01/{today}` with `sort=asc&limit=1`) for the listing
  date, mirroring Binance's first-kline probe. On any error or empty result,
  return `{ipo_date: None, delist_date: None}` so the `MetadataStore`
  3-tier bootstrap falls back to discovery.
- **Calendar.** US equities → existing `ExchangeCalendarStrategy` (XNYS) via
  `AssetType.EQUITY_US` (`schemas.py`). No new calendar work.

**Why not the alternatives on the board** (full reasoning in the story memo):
- *ThetaData (stocks-only)* — the roadmap itself recommends deferring;
  stocks-only underuses ThetaData (options is its real value) and options don't
  fit fin3's OHLCV schema (needs its own phase).
- *Declarative manifest (Phase 5)* / *`DataManager` retrieval (Phase 6)* —
  higher leverage but multi-slice stories that resist a clean one-day branch and
  sit after provider completeness in the roadmap ordering.

## Configurability

A renamed config model (drop-in replacement for the `PolygonConfig` stub):

```python
class MassiveConfig(BaseModel):
    provider_type: Literal["massive"] = "massive"
    api_key: str
    base_url: str = "https://api.polygon.io"  # see note
    adjusted: bool = False            # raw-default for Databento parity
    request_limit: int = 50000        # Massive's max page size
    max_retries: int = 3
    initial_backoff: float = 1.0
    max_backoff: float = 60.0
    timeout: float = 30.0
```

> **`base_url` default:** the roadmap says build against `api.massive.com`
> (the new brand host) while `api.polygon.io` (the legacy host) runs in
> parallel for an extended transition. Make `base_url` configurable so
> operators can target either; default to the legacy `api.polygon.io` only if
> the new host is not yet serving traffic at implementation time — otherwise
> default to `https://api.massive.com`. **Verify which host is live before
> setting the default** (this is a single `curl` check at implementation time).

**`.env` migration:** `FIN3_PROVIDERS__POLYGON__API_KEY` →
`FIN3_PROVIDERS__MASSIVE__API_KEY`. No production users exist, so no compat
shim — update `.env` and any examples/docs in the same change.

## Pagination

Massive's aggs endpoint paginates **by cursor, not by time**:

1. Request `/v2/aggs/ticker/{t}/range/{mult}/{ts}/{from}/{to}?limit=50000&sort=asc`.
2. Response JSON includes `results` (a list) and, when more pages exist, a
   `next_url` string (full URL of the next page, already carrying the cursor +
   `apiKey`). When there are no more pages, `next_url` is absent.
3. Follow `next_url` (GET) until it is absent or `results` is empty.
4. Concatenate all `results`, dedupe on `t`, sort ascending.

This differs from Binance's `startTime`-cursor loop and must be implemented as
its own `_request_with_retry`-driven loop keyed on `next_url`. Guard against an
unexpected non-advancing cursor (identical `next_url` twice → break).

## Files to modify

| File | Change |
|---|---|
| `fin3/config/settings.py` | Rename `PolygonConfig` → `MassiveConfig`; `provider_type` literal `"polygon"` → `"massive"`; add `base_url`, `adjusted`, `request_limit`, retry policy, `timeout`; update the `ProviderConfig` discriminated union (`settings.py:94`) to swap `PolygonConfig` for `MassiveConfig`. |
| `fin3/providers/massive.py` *(new)* | `MassiveProvider(DataProvider)` with `fetch()`, `estimate_cost()`, `get_instrument_bounds()`, `_request()`, `_request_with_retry()`, `_normalise()`, resolution→`(multiplier,timespan)` map, cursor pagination. Registered via `@ProviderRegistry.register("massive")`. |
| `fin3/providers/__init__.py` | Add `"massive"` to the `_register_builtin_providers()` import tuple (`providers/__init__.py`, currently `("databento", "binance", "yfinance")`). |
| `tests/providers/test_massive.py` *(new)* | Mocked-HTTP unit tests mirroring `test_binance.py` (symbol passthrough, resolution mapping incl. native 4h, ms→UTC normalisation, cursor pagination, 429 retry/backoff, fatal-error no-retry, cost=0, instrument bounds success/empty/error, adjusted flag, config). |
| `tests/test_config.py` | Assert `FIN3_PROVIDERS__MASSIVE__API_KEY` parses into a `MassiveConfig` with `provider_type="massive"`; update any existing `polygon` assertion. |
| `tests/integration/test_massive_live.py` *(new, gated)* | Live smoke behind `-m integration` + `MASSIVE_API_KEY` env guard. Fetch AAPL daily; assert Stage-1 validation + OHLCV constraints pass. Skipped without key/marker. |
| `.env` | Rename any `FIN3_PROVIDERS__POLYGON__...` key to `MASSIVE`. |
| `docs/USAGE.md` | Add Massive provider section (config, raw-default, native 4h, cost-not-enforced caveat, Polygon rebrand note). |
| `docs/dataset-comparison.md` | Add a Massive row to the provider comparison table. |
| `docs/api/` (API reference) | Add `MassiveProvider` / `MassiveConfig` reference page. |
| `.docs/ROADMAP.md` | Check off the "Massive Provider Implementation" item; note the ms-not-seconds timestamp correction. |

## Reuse

- `BinanceProvider` (`fin3/providers/binance.py`) — the structural template:
  `_request`/`_request_with_retry`/`fetch`/`estimate_cost`/`get_instrument_bounds`
  split, `ProviderRateLimitError`/`ProviderTimeoutError`/`ProviderError` usage,
  `empty_ohlcv()` for empty ranges, `_normalise()` producing a UTC
  `DatetimeIndex` canonical OHLCV DataFrame. Copy the shape; swap the
  endpoint, pagination strategy (cursor vs startTime), and timestamp unit.
- `DataProvider` abstract base (`fin3/providers/base.py`) — `fetch()` signature
  + docstring contract (UTC `DatetimeIndex`, `OHLCV_COLUMNS`, empty-not-None).
- `ProviderRegistry.register` (`fin3/providers/__init__.py`) — decorator
  registration; `_register_builtin_providers()` auto-import on package init.
- `Resolution` / `AssetType` / `OHLCV_COLUMNS` / `empty_ohlcv()` /
  `library_name()` (`fin3/schemas.py`) — resolution→timespan mapping,
  `EQUITY_US` → XNYS calendar, canonical columns, empty-frame factory.
- `ProviderConfig` discriminated union + `ClientConfig.providers` dict
  (`fin3/config/settings.py`) — env-nesting (`FIN3_PROVIDERS__MASSIVE__...`).
- `tests/providers/test_binance.py` — test layout to mirror: `agg()` fixture
  builder, `@patch("...MassiveProvider._request_with_retry")` mocking,
  `TestSymbolMapping`/`TestNormalise`/`TestFetch`/`TestRetry`/`TestCost`/
  `TestInstrumentBounds`/`TestConfig` class groupings.
- `tests/integration/test_concurrency.py` — the `-m integration` env-gated skip
  pattern to copy for the live smoke test.

## Implementation steps (sub-agent driven)

This is a sub-agent driven implementation. Each step names the sub-agent's
**contract** (inputs, outputs, dependencies) so it can be dispatched to an
isolated worktree. The config rename and provider are **sequenced, not
parallel** — the provider imports `MassiveConfig`, so it depends on the renamed
config landing first.

### Wave 1 — foundation

**[ ] Step 1 — Agent A: rename `PolygonConfig` → `MassiveConfig`**
- *Contract:* In `fin3/config/settings.py`, rename `PolygonConfig` to
  `MassiveConfig`; change `provider_type: Literal["polygon"]` →
  `Literal["massive"]`; expand the model to `api_key`, `base_url`, `adjusted`
  (default `False`), `request_limit` (default `50000`), `max_retries`,
  `initial_backoff`, `max_backoff`, `timeout`; update the `ProviderConfig`
  discriminated union to reference `MassiveConfig`; update the class docstring
  (Polygon rebrand, raw-default, native 4h). Update `tests/test_config.py` to
  parse `FIN3_PROVIDERS__MASSIVE__API_KEY` and assert `provider_type="massive"`;
  remove/replace any `polygon` assertion. Verify with
  `uv run pytest tests/test_config.py -v`.
- *Depends on:* nothing. Fully isolated.

> Step 1 must merge into the base branch before Step 2 starts (Step 2 imports
> `MassiveConfig`).

**[ ] Step 2 — Agent B: `MassiveProvider` + unit tests**
- *Contract:* Create `fin3/providers/massive.py` defining:
  - A `_RESOLUTION_TO_RANGE` map: `Resolution → (multiplier: int, timespan:
    str)` (`1m→(1,"minute")`, `5m→(5,"minute")`, `15m→(15,"minute")`,
    `1h→(1,"hour")`, `4h→(4,"hour")`, `1d→(1,"day")`).
  - `class MassiveProvider(DataProvider)` decorated
    `@ProviderRegistry.register("massive")`, storing config fields as in
    Binance (`self._base_url`, `self._api_key`, `self._adjusted`,
    `self._limit`, retry/backoff, `self._timeout`).
  - `_request(self, params, *, url=None)`: stdlib `urllib.request` GET; if
    `url` is None build it from `base_url` + path + `urlencode(params)`,
    else GET the provided `next_url` (already carries cursor+key); map HTTP 429
    → `ProviderRateLimitError`, socket timeout → `ProviderTimeoutError`, other
    HTTP/network/JSON errors → `ProviderError`. Return parsed JSON dict.
  - `_request_with_retry(...)`: same retry shape as Binance (retry
    rate-limit/timeout; propagate fatal errors).
  - `fetch(...)`: resolve `(multiplier, timespan)`; loop calling
    `_request_with_retry`, advancing via the response's `next_url` cursor until
    absent/empty/non-advancing; collect `results`; return `empty_ohlcv()` when
    empty, else `_normalise(results)` with timestamps parsed as **milliseconds**
    (`pd.to_datetime(t_ms, unit="ms", utc=True)`). Raise `ProviderError` on
    unsupported resolution.
  - `_normalise(results)`: map Massive keys `o/h/l/c/v/t` → canonical OHLCV,
    dedupe on `t`, sort ascending, UTC `DatetimeIndex`, `index.name=None`.
  - `estimate_cost(...)`: return `0.0` (subscription-based; document
    `max_cost` not enforced).
  - `get_instrument_bounds(symbol)`: probe earliest daily agg
    (`/range/1/day/1970-01-01/{today}` `sort=asc&limit=1`); return
    `{"ipo_date": <first t as UTC datetime>, "delist_date": None}`; on error or
    empty, return `{"ipo_date": None, "delist_date": None}`.
  - Add `"massive"` to `_register_builtin_providers()` in
    `fin3/providers/__init__.py`.
- *Also:* write `tests/providers/test_massive.py` mirroring
  `test_binance.py` — an `agg(t_ms, o,h,l,c,v)` row builder;
  `@patch("...MassiveProvider._request_with_retry")` for fetch/pagination/resolution;
  `@patch("...MassiveProvider._request")` for retry tests; coverage:
  symbol passthrough, all resolution mappings incl. **native 4h→(4,"hour")**,
  ms→UTC normalisation + canonical columns, **cursor pagination following
  `next_url` then stopping**, beyond-range filtering, unsupported-resolution
  error, 429 retry-with-backoff, exhaust-retries, fatal-no-retry, cost=0,
  instrument bounds (success / empty / error), `adjusted=false` default +
  toggle, config construction keyless-of-everything-but-key.
- *Depends on:* Step 1 merged (imports `MassiveConfig`).

### Wave 2 — docs + integration + roadmap

**[ ] Step 3 — Agent C: docs, ROADMAP, gated live smoke**
- *Contract:*
  - `tests/integration/test_massive_live.py`: skip unless
    `-m integration` **and** `MASSIVE_API_KEY` is set; fetch AAPL daily over a
    small recent range via a real `MarketDataFetcher` against MinIO (copy the
    gating/skip pattern from `tests/integration/test_concurrency.py`); assert
    Stage-1 validation passes, columns == `OHLCV_COLUMNS`, index is UTC.
  - Update `.docs/ROADMAP.md`: check off "Massive Provider Implementation";
    add a one-line correction note that the aggs `t` is milliseconds, not
    seconds.
  - Update `docs/USAGE.md` (Massive section), `docs/dataset-comparison.md`
    (comparison row), and the API reference (`docs/api/`) for `MassiveProvider`
    + `MassiveConfig`.
  - Update `.env` / example envs: `POLYGON` → `MASSIVE`.
- *Depends on:* Step 2 merged.

## Verification

1. **Provider + config unit tests (no key, no MinIO):**
   `uv run pytest tests/providers/test_massive.py tests/test_config.py -v`
2. **No regressions (LMDB-backed):**
   `uv run pytest tests/ -v`
3. **Lint & strict types:**
   `uv run ruff check . && uv run mypy fin3/`
4. **Gated live smoke (opt-in, needs paid key + MinIO):**
   `uv run pytest tests/integration/test_massive_live.py -m integration -v`
   (Skips cleanly without `MASSIVE_API_KEY` / `-m integration`.)
5. **Manual sanity (optional):** construct a `MassiveProvider` against a paid
   key, call `fetch("AAPL", ONE_DAY, ...)` and confirm timestamps land on real
   dates (catches the ms-vs-seconds gotcha immediately).
