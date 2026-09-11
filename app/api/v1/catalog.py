"""Katalog va provayder ma'lumotlari."""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import CurrentClient
from app.core.config import settings
from app.providers import get_provider, registered_providers

router = APIRouter(tags=["catalog"])


class GiftRead(BaseModel):
    id: str
    sticker_emoji: str | None = None
    star_count: int
    upgrade_star_count: int | None = None
    total_count: int | None = None
    remaining_count: int | None = None


class ProviderInfo(BaseModel):
    name: str
    configured: bool
    supports: list[str]


class LimitsRead(BaseModel):
    min_stars: int
    max_stars: int
    allowed_premium_months: list[int]
    default_provider: str


SUPPORTED_TYPES = {
    "mock": ["stars", "premium", "gift"],
    "fragment": ["stars", "premium"],
    "botapi": ["premium", "gift"],
}


@router.get("/catalog/gifts", response_model=list[GiftRead], summary="Mavjud sovg'alar")
async def list_gifts(
    client: CurrentClient,
    provider: Annotated[str | None, Query(description="mock | botapi")] = None,
) -> list[GiftRead]:
    name = provider or client.forced_provider or settings.default_provider
    gifts = await get_provider(name).list_gifts()
    return [GiftRead(**asdict(g)) for g in gifts]


@router.get("/providers", response_model=list[ProviderInfo], summary="Provayderlar ro'yxati")
async def list_providers(client: CurrentClient) -> list[ProviderInfo]:
    return [
        ProviderInfo(
            name=name,
            configured=provider.is_configured,
            supports=SUPPORTED_TYPES.get(name, []),
        )
        for name, provider in registered_providers().items()
    ]


@router.get("/limits", response_model=LimitsRead, summary="Biznes limitlari")
async def get_limits(client: CurrentClient) -> LimitsRead:
    return LimitsRead(
        min_stars=settings.min_stars,
        max_stars=settings.max_stars,
        allowed_premium_months=settings.allowed_premium_months,
        default_provider=client.forced_provider or settings.default_provider,
    )


@router.get("/balance", summary="Provayder balansi")
async def get_balance(
    client: CurrentClient,
    provider: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    name = provider or client.forced_provider or settings.default_provider
    return await get_provider(name).get_balance()
