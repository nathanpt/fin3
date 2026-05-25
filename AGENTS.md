# Repository Guidelines

## Project Overview

**fin3** is a Python library for declarative financial time-series data fetching, storage, and retrieval. It uses ArcticDB on MinIO for durable storage and supports multiple data providers (Databento, Polygon, Binance). Returns clean pandas DataFrames that any backtesting framework can consume.

## Project Structure

```
fin3/
├── __init__.py              # Public API
├── core.py                  # MarketDataFetcher orchestrator
├── config/settings.py       # Pydantic settings
├── providers/               # Data provider plugins (base, databento, polygon, binance)
├── storage/                 # ArcticDB + MinIO adapter
├── utils/                   # Date/gap helpers, validation, logging
├── schemas.py               # Data schemas + shared models
├── vb_integration.py        # VectorBT Pro helpers
├── cli.py                   # Optional CLI
└── exceptions.py
docs/
├── DESIGN.md                # Full system design document
└── arcticdb_docs/           # Scraped ArcticDB documentation (see scripts/scrape_arcticdb.py)
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
