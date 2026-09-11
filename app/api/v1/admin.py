"""Admin endpointlari — API-kalitlarni boshqarish (X-Admin-Key talab qilinadi)."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from app.api.deps import DbSession, require_admin
from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.security import generate_api_key, generate_webhook_secret, hash_api_key, key_hint
from app.core.urls import UnsafeUrlError, validate_webhook_url
from app.models.api_client import ApiClient
from app.models.enums import OrderStatus
from app.models.order import Order

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class WebhookUrlMixin(BaseModel):
    """webhook_url uchun umumiy SSRF validatsiyasi."""

    @field_validator("webhook_url", check_fields=False)
    @classmethod
    def _validate_webhook_url(cls, v: str | None) -> str | None:
        if not v:
            return None
        try:
            return validate_webhook_url(v, allow_private=settings.allow_private_webhook_urls)
        except UnsafeUrlError as exc:
            raise ValueError(str(exc)) from exc


class ApiClientCreate(WebhookUrlMixin):
    name: str = Field(min_length=2, max_length=120)
    webhook_url: str | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100_000)
    # Literal — ilgari bu oddiy `str` edi, ya'ni "fragmnet" kabi xato yozuv
    # o'sha mijozning HAR BIR buyurtmasini ishdan chiqarardi.
    forced_provider: Literal["mock", "fragment", "botapi"] | None = None
    test_key: bool = Field(default=False, description="sk_test_ prefiksli kalit yaratish")


class ApiClientUpdate(WebhookUrlMixin):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    webhook_url: str | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100_000)
    forced_provider: Literal["mock", "fragment", "botapi"] | None = None
    is_active: bool | None = None


class ApiClientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_hint: str
    is_active: bool
    rate_limit_per_minute: int | None
    webhook_url: str | None
    forced_provider: str | None
    created_at: datetime
    last_used_at: datetime | None


class ApiClientCreated(ApiClientRead):
    api_key: str = Field(description="⚠️ Faqat shu yerda bir marta ko'rsatiladi — saqlab qo'ying")
    webhook_secret: str = Field(description="Webhook imzosini tekshirish uchun maxfiy kalit")


@router.post(
    "/clients",
    response_model=ApiClientCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Yangi API mijoz yaratish",
)
async def create_client(body: ApiClientCreate, db: DbSession) -> ApiClientCreated:
    api_key = generate_api_key(test=body.test_key)
    webhook_secret = generate_webhook_secret()

    client = ApiClient(
        name=body.name,
        key_hash=hash_api_key(api_key),
        key_hint=key_hint(api_key),
        webhook_url=body.webhook_url,
        webhook_secret=webhook_secret,
        rate_limit_per_minute=body.rate_limit_per_minute,
        forced_provider=body.forced_provider,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)

    return ApiClientCreated(
        **ApiClientRead.model_validate(client).model_dump(),
        api_key=api_key,
        webhook_secret=webhook_secret,
    )


@router.get("/clients", response_model=list[ApiClientRead], summary="Mijozlar ro'yxati")
async def list_clients(db: DbSession) -> list[ApiClientRead]:
    stmt = select(ApiClient).order_by(ApiClient.created_at.desc())
    return [ApiClientRead.model_validate(c) for c in (await db.execute(stmt)).scalars()]


@router.patch("/clients/{client_id}", response_model=ApiClientRead, summary="Mijozni yangilash")
async def update_client(
    client_id: uuid.UUID, body: ApiClientUpdate, db: DbSession
) -> ApiClientRead:
    client = await db.get(ApiClient, client_id)
    if client is None:
        raise NotFoundError(f"Mijoz topilmadi: {client_id}")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(client, field, value)

    await db.commit()
    await db.refresh(client)
    return ApiClientRead.model_validate(client)


@router.delete(
    "/clients/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mijozni o'chirish",
)
async def delete_client(client_id: uuid.UUID, db: DbSession) -> None:
    client = await db.get(ApiClient, client_id)
    if client is None:
        raise NotFoundError(f"Mijoz topilmadi: {client_id}")
    await db.delete(client)
    await db.commit()


@router.get("/stats", summary="Umumiy statistika")
async def stats(db: DbSession) -> dict:
    by_status = dict(
        (await db.execute(select(Order.status, func.count()).group_by(Order.status))).all()
    )
    by_type = dict((await db.execute(select(Order.type, func.count()).group_by(Order.type))).all())
    by_provider = dict(
        (await db.execute(select(Order.provider, func.count()).group_by(Order.provider))).all()
    )
    clients_total = (await db.execute(select(func.count()).select_from(ApiClient))).scalar_one()

    total = sum(by_status.values())
    completed = by_status.get(OrderStatus.COMPLETED.value, 0)

    return {
        "orders_total": total,
        "orders_by_status": by_status,
        "orders_by_type": by_type,
        "orders_by_provider": by_provider,
        "success_rate": round(completed / total, 4) if total else None,
        "clients_total": clients_total,
    }
