# fin3

Financial time-series data tooling for downloading, normalizing, validating,
storing, and retrieving OHLCV bars as pandas DataFrames. Built on
[ArcticDB](https://arcticdb.io/) and [MinIO](https://min.io/), with Databento,
Binance, Yahoo Finance, and Massive (formerly Polygon.io) provider implementations.

Maintained by [Nathan Peet](https://github.com/nathanpt). Nathan uses fin3 to
download, normalize, and prepare market data for analysis in his automated
portfolio workflow. This describes data preparation and analysis, not live trade
execution or investment performance; no external adoption is claimed.

## Implemented functionality

- Canonical OHLCV columns (`open`, `high`, `low`, `close`, `volume`) with UTC timestamps.
- Exchange-session and continuous calendars, gap detection, incremental fetching,
  and calendar alignment.
- ArcticDB storage backed by MinIO/S3-compatible storage; local LMDB support for tests.
- Raw-provider and post-alignment validation, metadata bootstrap, and per-symbol
  cross-process file locking.
- Integrity auditing, HTML inspection reports, defragmentation, and resource monitoring.
- Standalone download, audit, inspection, and Databento normalization scripts.

These are implemented capabilities, not a guarantee of error-free data or universal
provider/asset coverage. Data availability, licensing, entitlements, and rate limits
remain subject to each source.

### Provider status

All four providers are registered in the public implementation. Live-verification
notes below refer to historical public PR evidence, not a fresh live-API test of
every provider on each checkout.

| Provider key | Implemented scope | Verification and limitations |
|---|---|---|
| `databento` | Historical OHLCV via the Databento SDK; dataset selection, normalization, retry/backoff, and cost estimates | Requires the `databento` extra, an API key, and applicable data entitlements. This documentation audit did not run a paid download. |
| `binance` | Public spot klines, pagination, retry/backoff, listing-bound probe; `BTC-USD` maps to `BTCUSDT` | [PR #1](https://github.com/nathanpt/fin3/pull/1) records live daily/hourly/minute downloads, including pagination. Some regions block the default host; `BinanceConfig.base_url` is configurable. USDT quotes are not USD quotes. |
| `yahoo` | Yahoo Finance OHLCV through optional `yfinance`; raw prices by default, retry/backoff, lifecycle probe | [PR #3](https://github.com/nathanpt/fin3/pull/3) records live equity and crypto downloads. Unofficial, rate-limited research source with restricted intraday history; not a sole production data source. `4h` uses hourly data and core aggregation, not native Yahoo 4h bars. |
| `massive` | Massive/Polygon REST aggregates; US-equity-focused configuration, native 4h bars, cursor pagination, raw prices by default | [PR #4](https://github.com/nathanpt/fin3/pull/4) added the implementation; [PR #5](https://github.com/nathanpt/fin3/pull/5) records live daily/minute data, pagination, and rate-limit handling. Requires an API key; history and access depend on plan. Lifecycle bounds can reflect the plan's history boundary rather than the true listing date. |

`max_cost` checks provider-reported estimates before fetching. Databento queries
its cost API; Binance and Yahoo report zero, while subscription-based Massive also
reports zero. **A zero estimate does not make subscriptions or entitlements free,
and `max_cost` does not cap Massive subscription charges.**

## Quick start (from source)

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Use this repository
checkout; these instructions do not assume that a published PyPI package is available.

```bash
git clone https://github.com/nathanpt/fin3.git
cd fin3
uv sync --extra databento
```

For Yahoo support, add `--extra yfinance`. Binance and Massive use stdlib HTTP and
need no provider-specific extra. ArcticDB storage still requires its core dependencies.

### Configure

Start a local MinIO instance and supply its credentials plus your Databento key in
an uncommitted `.env` file (the credentials below are development placeholders):

```dotenv
FIN3_MINIO__ENDPOINT=localhost:9000
FIN3_MINIO__ACCESS_KEY=minioadmin
FIN3_MINIO__SECRET_KEY=minioadmin
FIN3_PROVIDERS__DATABENTO__PROVIDER_TYPE=databento
FIN3_PROVIDERS__DATABENTO__API_KEY=db-your-key-here
```

Do not use placeholder storage credentials in production. See the
[usage guide](docs/USAGE.md) for storage configuration and other providers.

### Fetch and analyze

The following performs network requests and may incur Databento charges. Choose
symbols, dates, dataset, and entitlements appropriate to your account before running.

```python
from datetime import datetime, timezone

from fin3 import AssetType, ClientConfig, MarketDataFetcher, Resolution

fetcher = MarketDataFetcher(ClientConfig.from_env())
df = fetcher.get_data(
    asset_type=AssetType.EQUITY_US,
    provider="databento",
    resolution=Resolution.ONE_MINUTE,
    symbols=["AAPL"],
    start=datetime(2024, 1, 2, tzinfo=timezone.utc),
    end=datetime(2024, 1, 2, 23, 59, tzinfo=timezone.utc),
)
aapl = df["AAPL"]
closes = df[("AAPL", "close")]
```

`get_data()` checks existing coverage, fetches missing ranges, validates raw data,
aligns bars to a calendar, validates again, and writes to ArcticDB before returning
a symbol/field MultiIndex DataFrame. Library names follow
`{asset_prefix}-{resolution}-{provider}`, for example `equities-1m-databento` or
`crypto-1h-binance`. Missing in-session bars are represented by NaN OHLC and zero
volume. Trading sessions depend on the asset calendar and daylight-saving time;
NYSE hours are not a fixed UTC interval year-round.

## Standalone tools

A unified `fin3` command is not implemented. Run the existing scripts from the
checkout with `uv run python scripts/<name>.py --help` before using them.
Some commands download paid data or modify storage; review their options first.

| Script | Purpose |
|---|---|
| [download_symbols.py](scripts/download_symbols.py) | Download data for multiple symbols with cost estimation |
| [audit_library.py](scripts/audit_library.py) | Integrity checks and HTML dashboard |
| [inspect_library.py](scripts/inspect_library.py) | Per-symbol library overview and HTML reports |
| [defragment_library.py](scripts/defragment_library.py) | Compact ArcticDB segments, with dry-run support |
| [normalize_databento.py](scripts/normalize_databento.py) | Convert raw Databento parquet to canonical schema |
| [fix_meta_rename.py](scripts/fix_meta_rename.py) | Handle the FB-to-META symbol rename gap |

## Unfinished / planned

- Declarative YAML dataset manifests and `fin3 ensure` / `fin3 sync`.
- High-level `DataManager` retrieval, alignment, and multi-format output API.
- A unified CLI.
- Additional providers such as ThetaData; no ThetaData provider is in the public tree.

Binance, Yahoo, and Massive are implemented, not future provider stubs. The older
[Massive implementation plan](PLAN.md) is historical context, not current status.
The internal `.docs/ROADMAP.md` referenced in repository conventions is not included
in the public checkout; this README summarizes public implemented/planned status.

## Documentation and development

- [Usage guide](docs/USAGE.md): configuration, fetching, concurrency, monitoring,
  exceptions, and adding providers. For installation, use the source instructions above.
- [Provider comparison](docs/dataset-comparison.md).
- [API reference source](docs/api/index.md).
- [Resource monitoring notes](docs/resource-monitoring-notes.md).
- [Repository guidelines](AGENTS.md).

```bash
uv sync --all-extras
uv run pytest tests/ -m "not integration"
uv run ruff check .
uv run mypy fin3/
```

Live integration tests require explicit opt-in and relevant keys/infrastructure.
Mocked tests alone do not establish live provider compatibility; see the live-test
requirements in `AGENTS.md`. Do not point validation runs at production stores.

## License

Copyright 2026 Nathan Peet.

Licensed under the [Apache License, Version 2.0](LICENSE). The license covers this
repository's code; it does not grant rights to redistribute third-party market data
or replace provider agreements.
