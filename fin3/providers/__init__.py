"""Provider registry — hardcoded mapping from name to provider class."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable, TypeVar

from fin3.exceptions import ConfigurationError
from fin3.providers.base import ChainProvider

if TYPE_CHECKING:
    from fin3.providers.base import DataProvider

_ProviderT = TypeVar("_ProviderT", bound="DataProvider | ChainProvider")
"""Concrete provider class type preserved through the register decorator."""

_PROVIDER_TYPE_ERROR = "provider {name!r} is a {kind}; use {getter} for chain providers"


class ProviderRegistry:
    """Registry mapping provider names to their implementations.

    Providers register themselves via the ``@ProviderRegistry.register(name)``
    classmethod decorator. The registry validates configured names eagerly at
    ``__init__`` but instantiates each provider lazily on first ``get`` /
    ``get_chain``: the heavy, optional SDK import happens inside the provider
    class's ``__init__``, so constructing a ``MarketDataFetcher`` never
    imports a provider SDK or opens a client — a missing extra (e.g.
    ``databento`` not installed) surfaces only when a fetch actually needs
    that provider (GitHub issue #12). OHLCV ``DataProvider`` instances are
    resolved via ``get``; chain ``ChainProvider`` instances only via
    ``get_chain`` — ``get`` rejects chain providers so bar pipelines cannot
    silently consume chain data.
    """

    _PROVIDER_MAP: dict[str, type[Any]] = {}

    def __init__(self, configs: dict[str, Any]) -> None:
        """Validate configured provider names; defer instantiation to first use.

        Parameters
        ----------
        configs : dict[str, Any]
            Mapping of provider name to its Pydantic config model.

        Raises
        ------
        ConfigurationError
            If a provider name is not registered.
        """
        self._configs = dict(configs)
        self._providers: dict[str, DataProvider | ChainProvider] = {}
        self._instantiation_lock = threading.Lock()
        for name in self._configs:
            if name not in self._PROVIDER_MAP:
                raise ConfigurationError(
                    f"Unknown provider '{name}'. Available: {list(self._PROVIDER_MAP.keys())}"
                )

    def _resolve(self, name: str) -> DataProvider | ChainProvider:
        """Return the instance for *name*, instantiating it on first use.

        Double-checked under a lock so concurrent ``get`` calls on a shared
        registry construct the provider exactly once.
        """
        provider = self._providers.get(name)
        if provider is not None:
            return provider
        with self._instantiation_lock:
            provider = self._providers.get(name)
            if provider is None:
                provider = self._PROVIDER_MAP[name](self._configs[name])
                self._providers[name] = provider
        return provider

    def get(self, name: str) -> DataProvider:
        """Return a configured bar (OHLCV) provider instance by name.

        Instantiates the provider on first resolution (lazy; see the class
        docstring).

        Parameters
        ----------
        name : str
            Provider name as used in ``@ProviderRegistry.register(name)``.

        Returns
        -------
        DataProvider
            The configured provider instance.

        Raises
        ------
        ConfigurationError
            If the provider is not in the configured set, or is a
            ``ChainProvider`` (those require ``get_chain``).
        """
        provider = self._providers.get(name)
        if provider is None:
            if name not in self._configs:
                raise ConfigurationError(
                    f"Provider '{name}' not configured. "
                    f"Add FIN3_PROVIDERS__{name.upper()}__API_KEY to your .env. "
                    f"Available: {list(self._configs)}"
                )
            provider = self._resolve(name)
        if isinstance(provider, ChainProvider):
            raise ConfigurationError(
                _PROVIDER_TYPE_ERROR.format(
                    name=name, kind="ChainProvider", getter="get_chain()"
                )
            )
        return provider

    def get_chain(self, name: str) -> ChainProvider:
        """Return a configured chain provider instance by name.

        Instantiates the provider on first resolution (lazy; see the class
        docstring).

        Raises
        ------
        ConfigurationError
            If the provider is not configured or is not a ``ChainProvider``.
        """
        provider = self._providers.get(name)
        if provider is None:
            if name not in self._configs:
                raise ConfigurationError(
                    f"Provider '{name}' not configured. "
                    f"Add FIN3_PROVIDERS__{name.upper()}__API_KEY to your .env. "
                    f"Available: {list(self._configs)}"
                )
            provider = self._resolve(name)
        if not isinstance(provider, ChainProvider):
            raise ConfigurationError(
                f"provider {name!r} is not a ChainProvider; use get() for "
                "bar (OHLCV) providers"
            )
        return provider

    @classmethod
    def register(cls, name: str) -> Callable[[type[_ProviderT]], type[_ProviderT]]:
        """Class-level decorator to register provider classes.

        Generic over the concrete provider class so decorators retain the
        ``DataProvider``/``ChainProvider`` subclass type.
        """

        def decorator(provider_cls: type[_ProviderT]) -> type[_ProviderT]:
            cls._PROVIDER_MAP[name] = provider_cls
            return provider_cls

        return decorator


# Builtin provider modules register themselves via the ``@register`` decorator
# at import time. Importing them here ensures the registry is populated for
# any entry point (e.g. ``MarketDataFetcher``) without callers having to import
# each provider module explicitly. Each module performs its heavy/optional
# SDK import lazily inside ``__init__``, so importing the module is cheap and
# side-effect-free beyond registration.
def _register_builtin_providers() -> None:
    """Import builtin provider modules to trigger registration side-effects.

    Module file names are decoupled from the registry key each provider uses
    in ``@register(...)`` — e.g. ``yfinance.py`` registers under ``"yahoo"``.
    """
    from importlib import import_module

    for module_name in (
        "databento",
        "binance",
        "yfinance",
        "massive",
        "cboe",
        "eodhd_options",
    ):
        import_module(f"fin3.providers.{module_name}")


_register_builtin_providers()
