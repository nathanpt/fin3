# API Reference

This section documents the public API of fin3. Each page covers a module group.

- [**MarketDataFetcher**](core.md) — the primary entry point for fetching and managing data
- [**Schemas**](schemas.md) — `AssetType`, `Resolution`, `OHLCV_COLUMNS`, `library_name()`
- [**Exceptions**](exceptions.md) — full exception hierarchy under `Fin3Error`
- [**Providers**](providers.md) — `DataProvider`, `ProviderRegistry`, `DatabentoProvider`
- [**Storage**](storage.md) — `ArcticStorage`, `SymbolLock`, defrag utilities
- [**Configuration**](config.md) — `ClientConfig`, `MinioConfig`, provider configs
- [**Validation & Integrity**](validation.md) — validation pipeline, integrity auditing
- [**Metadata**](metadata.md) — `MetadataStore` for lifecycle bound discovery
- [**Calendar**](calendar.md) — calendar strategies for trading session alignment
- [**Inspection**](inspect.md) — library inspection and HTML reporting
- [**Monitoring**](monitoring.md) — resource tracking and live display
- [**Utilities**](utilities.md) — date utilities, logging