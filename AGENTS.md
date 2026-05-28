# Repository Guidelines

## Project Overview

**fin3** is a Python library for declarative financial time-series data fetching, storage, and retrieval. It uses ArcticDB on MinIO for durable storage and supports multiple data providers (Databento, Polygon, Binance). Returns clean pandas DataFrames that any backtesting framework can consume.

## Project Structure

```
fin3/
├── __init__.py              # Public API exports
├── core.py                  # MarketDataFetcher orchestrator
├── exceptions.py            # Fin3Error hierarchy (9 classes)
├── schemas.py               # AssetType, Resolution enums, OHLCV_COLUMNS, empty_ohlcv(), library_name()
├── calendar/
│   └── exchange.py          # CalendarStrategy protocol, ExchangeCalendarStrategy, ContinuousCalendarStrategy
├── config/
│   └── settings.py          # MinioConfig, DatabentoConfig, PolygonConfig, BinanceConfig, ClientConfig
├── metadata/
│   └── asset_profile.py     # MetadataStore (IPO/delist date bootstrap with 3-tier fallback)
├── providers/
│   ├── __init__.py          # ProviderRegistry (decorator-based registration)
│   ├── base.py              # Abstract DataProvider base class
│   └── databento.py         # DatabentoProvider with retry/backoff
├── storage/
│   └── arctic.py            # ArcticStorage (MinIO/LMDB adapter with library caching)
└── utils/
    ├── date_utils.py         # ensure_utc(), detect_gaps(), _chunk_boundaries()
    ├── logging.py            # structlog configure_logging()
    └── validation.py         # validate_raw_provider_data() (Stage 1), validate_storage_artifact() (Stage 2)
tests/
├── conftest.py               # LMDB fixtures, make_ohlcv() helper
├── test_calendar.py          # Calendar strategy tests (5)
├── test_config.py            # Pydantic settings tests (5)
├── test_core.py              # MarketDataFetcher E2E tests (4)
├── test_exceptions.py        # Exception hierarchy tests (7)
├── test_gap_detection.py     # Gap detection tests (4)
├── test_metadata.py          # MetadataStore tests (3)
├── test_storage.py           # ArcticStorage tests (7)
├── test_validation.py        # Validation pipeline tests (12)
└── providers/
    └── test_databento.py     # DatabentoProvider tests (3)
docs/
├── DESIGN.md                # Full system design document
├── USAGE.md                 # Internal usage guide (install, config, API examples)
├── ROADMAP.md               # Project roadmap
└── arcticdb_docs/           # Scraped ArcticDB documentation (see scripts/scrape_arcticdb.py)
scripts/
└── scrape_arcticdb.py       # ArcticDB docs scraper
```

## Build, Test & Development Commands

This project uses **uv** for dependency and venv management. Always use `uv run` instead of activating the venv manually.

- **Setup**: `uv sync --all-extras` — install all dependencies (core + optional)
- **Add dependency**: `uv add <package>` — add to core deps
- **Add dev dep**: `uv add --dev <package>` or `uv add --optional databento <package>`
- **Run command**: `uv run <command>` — execute any command in the venv
- **Tests**: `uv run pytest tests/ -v`
- **Lint**: `uv run ruff check .`
- **Format**: `uv run ruff format .`
- **Type check**: `uv run mypy fin3/`

## Coding Style & Naming Conventions

- **Language**: Python 3.11+
- **Formatting**: ruff (Black-compatible)
- **Type hints**: Use throughout; validated by mypy
- **Naming**:
  - Libraries (ArcticDB): `{asset_type}-{resolution}-{provider}` (kebab-case, e.g. `equities-1m-databento`)
  - Symbols: Uppercase tickers (`AAPL`), crypto with pair (`BTC-USD`)
  - Files/modules: snake_case
  - Classes: PascalCase
- **Configuration**: Pydantic Settings with `.env` file support
- **Logging**: structlog for structured JSON logging

## Repository Coding Standards

When working in this repository, you must strictly adhere to the following rules:

- **Type Safety:** All Python code must be strictly typed. `mypy` must pass without errors.
- **Linting & Formatting:** We use `ruff` exclusively. Do not use `flake8`, `black`, or `isort`.
- **Testing:** All tests live in the `/tests` directory, completely separate from the `/src` directory. Use `pytest`.
- **Error Handling:** Raise explicit exceptions (e.g., `ValueError`, custom domain exceptions). **Never** use `assert` statements in the `src/` code.
- **Dependencies:** Do not modify `pyproject.toml` to add dependencies without explicit user authorization.
- Update ROADMAP.md after completing items without errors or bugs. 

## Testing Guidelines

- Framework: pytest
- Aim for high coverage on core logic: gap detection, data validation, provider routing
- Use fixtures for MinIO/ArcticDB mocks (or LMDB-backed local instances)
- Test naming: `test_<feature>_<scenario>()` (e.g. `test_get_data_downloads_missing_range`)

## Commit & Pull Request Guidelines

- Write clear, descriptive commit messages in imperative mood (e.g. "Add gap detection for multi-symbol queries")
- Keep PRs focused on a single concern
- Link PRs to relevant design decisions in `docs/DESIGN.md`

## Architecture Notes

- **Data flow**: `get_data()` → check ArcticDB coverage via `read(date_range=...)` → fetch missing ranges from provider → validate → store via `update(date_range=...)` → return DataFrame
- **Provider pattern**: Abstract `DataProvider` base class; each provider implements `fetch()`. Register via `ProviderRegistry`. Providers return DataFrames only — no ArcticDB interaction.
- **Storage**: ArcticDB libraries backed by MinIO. One library per asset_type/resolution/provider combination. `dynamic_schema=True` at library creation.
- **Version management**: `prune_previous_versions=True` on all writes. Use snapshots for auditability.
- **Output**: Standardized OHLCV DataFrames with UTC datetime index, multi-column support for multiple symbols.
