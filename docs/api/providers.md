# Providers

Pluggable data provider abstraction. Providers are registered via the
decorator-based `ProviderRegistry` and implement the `DataProvider` ABC.
Options chain providers implement `ChainProvider` and resolve only via
`ProviderRegistry.get_chain`, consumed through `OptionsDataFetcher` — see
[Options EOD chains](../options-eodhd.md).

::: fin3.providers.base.DataProvider
::: fin3.providers.ProviderRegistry
::: fin3.providers.base.ChainProvider
::: fin3.providers.eodhd_options.EodhdOptionsProvider
::: fin3.providers.databento.DatabentoProvider
    options:
      show_root_heading: true
::: fin3.providers.massive.MassiveProvider
    options:
      show_root_heading: true
::: fin3.providers.binance.BinanceProvider
    options:
      show_root_heading: true
::: fin3.providers.yfinance.YahooProvider
    options:
      show_root_heading: true