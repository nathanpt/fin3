# fin3

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/nathanpt/fin3)
[![Docs](https://github.com/nathanpt/fin3/actions/workflows/docs.yml/badge.svg)](https://github.com/nathanpt/fin3/actions/workflows/docs.yml)

**Declarative financial time-series data** — fetch, store, validate, and retrieve
OHLCV bars with production-grade integrity guarantees.

Built on [ArcticDB](https://arcticdb.io/) + [MinIO](https://min.io/)
(S3-compatible storage) with pluggable data providers.

<div class="grid" markdown>

[Get started :material-rocket-launch:](#quick-start){ .md-button .md-button--primary }
[API reference :material-api:](api/index.md){ .md-button }
[Usage guide :material-book-open:](USAGE.md){ .md-button }

</div>

---

## Why fin3

<div class="grid cards" markdown>

- :material-database-check: **Durable, canonical storage**

    ---

    OHLCV bars in ArcticDB on MinIO with a single canonical schema and
    UTC-aligned timestamps. One symbol per symbol — no accidental clobbered
    writes.

- :material-calendar-clock: **Calendar-aligned by design**

    ---

    NYSE, CME, and 24/7 continuous calendars. Intraday bars snap to trading
    sessions; no-trade minutes become explicit null bars instead of gaps.

- :material-sync: **Incremental & idempotent**

    ---

    `get_data()` checks existing coverage and fetches only the gaps. Calling it
    twice for the same range is a no-op — no wasted API credits.

- :material-shield-check: **Two-stage validation**

    ---

    Structural checks on raw provider data, then strict NaN-semantics after
    calendar reindex. Bad data never lands in storage.

- :material-lock: **Concurrency-safe**

    ---

    `flock`-based per-symbol locking serializes concurrent fetches across
    processes — no double-fetch, no clobbered writes, auto-release on crash.

- :material-chart-box: **Auditable & observable**

    ---

    10+ vectorized integrity checks with HTML dashboards, plus live resource
    monitoring (tmux pane, rich info bar, or CI summary).

- :material-swap-horizontal: **Multi-provider**

    ---

    Pluggable providers behind one registry: Databento for equities/futures,
    Massive (formerly Polygon.io) for paid US equities, Binance for crypto
    (free, keyless public klines), and Yahoo Finance for free US equity/ETF
    data. Same declarative `get_data()` regardless of source.

</div>

## Quick start

=== "1 · Install"

    ```bash
    # with pip
    pip install "fin3[databento]"

    # or with uv
    uv pip install "fin3[databento]"
    ```

=== "2 · Configure"

    Create a `.env` (or set environment variables):

    ```bash
    FIN3_MINIO__ENDPOINT=localhost:9000
    FIN3_MINIO__ACCESS_KEY=minioadmin
    FIN3_MINIO__SECRET_KEY=minioadmin
    FIN3_PROVIDERS__DATABENTO__PROVIDER_TYPE=databento
    FIN3_PROVIDERS__DATABENTO__API_KEY=db-your-key-here
    ```

    All settings use the `FIN3_` prefix with `__` as the nested delimiter.

=== "3 · Fetch"

    ```python
    from datetime import datetime, timezone
    from fin3 import MarketDataFetcher, AssetType, Resolution, ClientConfig

    fetcher = MarketDataFetcher(ClientConfig.from_env())

    df = fetcher.get_data(
        asset_type=AssetType.EQUITY_US,
        provider="databento",
        resolution=Resolution.ONE_HOUR,
        symbols=["AAPL", "MSFT", "GOOG"],
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 3, 31, tzinfo=timezone.utc),
    )
    ```

    Returns a clean, calendar-aligned `MultiIndex` DataFrame — ready to feed
    into any backtesting framework.

!!! tip "What you get back"
    A pandas DataFrame with a UTC `DatetimeIndex` and `MultiIndex` columns of
    `(symbol, field)` where fields are `open`, `high`, `low`, `close`, `volume`.
    Extract one symbol with `df["AAPL"]`, or a single series with
    `df[("AAPL", "close")]`.

---

## Explore

<div class="grid cards" markdown>

- :material-book-open: **Usage Guide**

    ---

    Installation, configuration, fetching data, crypto, futures, concurrency,
    monitoring, exception handling, and adding new providers.

    [:octicons-arrow-right-24: Open](USAGE.md)

- :material-api: **API Reference**

    ---

    Auto-generated reference for every public class and function across all
    modules.

    [:octicons-arrow-right-24: Open](api/index.md)

- :material-chart-line: **Data Sources**

    ---

    Provider comparison (Databento, Massive, Yahoo, Binance) plus the
    Databento dataset cost analysis: XNAS.ITCH vs ARCX.PILLAR vs XNAS.BASIC
    vs EQUS.SUMMARY.

    [:octicons-arrow-right-24: Open](dataset-comparison.md)

</div>
