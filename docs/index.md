# fin3

[![lint](https://github.com/yourname/fin3/actions/workflows/lint.yml/badge.svg)](https://github.com/yourname/fin3/actions/workflows/lint.yml)
[![CI](https://github.com/yourname/fin3/actions/workflows/ci.yml/badge.svg)](https://github.com/yourname/fin3/actions/workflows/ci.yml)

Declarative financial time-series data fetching, storage, and retrieval. Built on
[ArcticDB](https://arcticdb.io/) + [MinIO](https://min.io/) (S3-compatible storage)
with support for multiple data providers.

```python
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

## Quick Links

- [Usage Guide](USAGE.md) — install, configure, fetch data, concurrency, monitoring
- [API Reference](api/index.md) — full API documentation

## Features

- **Production-grade storage** — ArcticDB on MinIO with canonical OHLCV schema
- **Calendar alignment** — NYSE, CME, and 24/7 continuous calendars
- **Incremental, idempotent fetching** — only gaps are fetched; redundant calls are no-ops
- **Two-stage validation** — structural checks on raw data, NaN-semantics after calendar reindex
- **Cross-process concurrency safety** — `flock`-based per-symbol locking, auto-release
- **Integrity auditing** — 10+ vectorized checks with HTML dashboard output
- **Resource monitoring** — live tmux pane, rich info bar, or CI summary
- **Defragmentation** — compact ArcticDB segments with dry-run support
- **Metadata discovery** — 3-tier IPO/delist date bootstrap
- **Pluggable providers** — decorator-based registry (Databento shipping; Polygon, Binance on roadmap)