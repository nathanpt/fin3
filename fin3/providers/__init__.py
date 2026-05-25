"""Provider registry — hardcoded mapping from name to provider class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from fin3.exceptions import ConfigurationError

if TYPE_CHECKING:
    from fin3.providers.base import DataProvider


class ProviderRegistry:
    _PROVIDER_MAP: dict[str, type[Any]] = {}

    def __init__(self, configs: dict[str, Any]) -> None:
        self._providers: dict[str, DataProvider] = {}
        for name, config in configs.items():
            if name not in self._PROVIDER_MAP:
                raise ConfigurationError(
                    f"Unknown provider '{name}'. Available: {list(self._PROVIDER_MAP.keys())}"
                )
            self._providers[name] = self._PROVIDER_MAP[name](config)

    def get(self, name: str) -> DataProvider:
        if name not in self._providers:
            raise ConfigurationError(
                f"Provider '{name}' not configured. "
                f"Add FIN3_PROVIDERS__{name.upper()}__API_KEY to your .env. "
                f"Available: {list(self._providers.keys())}"
            )
        return self._providers[name]

    @classmethod
    def register(cls, name: str) -> Callable[[type[DataProvider]], type[DataProvider]]:
        """Class-level decorator to register provider classes."""

        def decorator(provider_cls: type[DataProvider]) -> type[DataProvider]:
            cls._PROVIDER_MAP[name] = provider_cls
            return provider_cls

        return decorator
