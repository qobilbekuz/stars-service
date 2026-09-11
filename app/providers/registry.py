"""Provayder registri — singleton instansiyalar."""

from app.core.exceptions import ProviderNotConfiguredError
from app.models.enums import ProviderName
from app.providers.base import BaseProvider
from app.providers.botapi import BotApiProvider
from app.providers.fragment import FragmentProvider
from app.providers.mock import MockProvider

_REGISTRY: dict[str, BaseProvider] = {
    ProviderName.MOCK.value: MockProvider(),
    ProviderName.FRAGMENT.value: FragmentProvider(),
    ProviderName.BOTAPI.value: BotApiProvider(),
}


def registered_providers() -> dict[str, BaseProvider]:
    return dict(_REGISTRY)


def get_provider(name: str) -> BaseProvider:
    provider = _REGISTRY.get(name)
    if provider is None:
        raise ProviderNotConfiguredError(
            f"'{name}' nomli provayder mavjud emas. Mavjudlari: {sorted(_REGISTRY)}"
        )
    return provider


async def provider_health() -> dict[str, str]:
    return {name: await provider.health() for name, provider in _REGISTRY.items()}


async def close_providers() -> None:
    for provider in _REGISTRY.values():
        await provider.close()
