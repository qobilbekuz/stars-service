"""Buyurtma so'rov/javob sxemalari va biznes validatsiyasi."""

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings
from app.core.urls import UnsafeUrlError, validate_webhook_url
from app.models.enums import OrderStatus, OrderType

USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{3,31}$")


def normalize_username(value: str) -> str:
    """'@user', 'https://t.me/user', 't.me/user' -> 'user'."""
    v = value.strip()
    v = re.sub(r"^(https?://)?(www\.)?t\.me/", "", v, flags=re.IGNORECASE)
    v = v.lstrip("@").strip()
    if not USERNAME_RE.match(v):
        raise ValueError(
            "username noto'g'ri formatda: harf bilan boshlanib, 4-32 ta harf/raqam/pastki chiziq"
        )
    return v


class RecipientMixin(BaseModel):
    recipient_username: str | None = Field(
        default=None, description="Qabul qiluvchi username (@ bilan yoki usiz)"
    )
    recipient_user_id: int | None = Field(
        default=None, description="Telegram user ID (botapi provayderi uchun)"
    )

    @field_validator("recipient_username")
    @classmethod
    def _validate_username(cls, v: str | None) -> str | None:
        return normalize_username(v) if v else None

    @model_validator(mode="after")
    def _require_recipient(self):
        if not self.recipient_username and not self.recipient_user_id:
            raise ValueError("recipient_username yoki recipient_user_id dan biri majburiy")
        return self


class OrderBase(RecipientMixin):
    message: str | None = Field(default=None, max_length=255, description="Sovg'aga izoh matni")
    provider: Literal["mock", "fragment", "botapi"] | None = Field(
        default=None, description="Majburiy provayder (bo'sh -> standart)"
    )
    webhook_url: str | None = Field(
        default=None, description="Shu buyurtma uchun alohida webhook manzili"
    )
    extra_data: dict[str, Any] | None = Field(default=None, description="Ixtiyoriy meta-ma'lumot")

    @field_validator("webhook_url")
    @classmethod
    def _validate_webhook_url(cls, v: str | None) -> str | None:
        """SSRF himoyasi — app/core/urls.py dagi izohga qarang."""
        if not v:
            return None
        try:
            return validate_webhook_url(v, allow_private=settings.allow_private_webhook_urls)
        except UnsafeUrlError as exc:
            raise ValueError(str(exc)) from exc


class StarsOrderCreate(OrderBase):
    """Telegram Stars sotib olish."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"recipient_username": "@durov", "quantity": 100}}
    )

    quantity: int = Field(description="Stars soni")

    @field_validator("quantity")
    @classmethod
    def _validate_quantity(cls, v: int) -> int:
        if not settings.min_stars <= v <= settings.max_stars:
            raise ValueError(
                f"stars soni {settings.min_stars} va {settings.max_stars} orasida bo'lishi kerak"
            )
        return v


class PremiumOrderCreate(OrderBase):
    """Telegram Premium obunasi sovg'a qilish."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"recipient_username": "@durov", "months": 3}}
    )

    months: int = Field(description="Obuna muddati: 3, 6 yoki 12 oy")

    @field_validator("months")
    @classmethod
    def _validate_months(cls, v: int) -> int:
        if v not in settings.allowed_premium_months:
            raise ValueError(
                f"months faqat {settings.allowed_premium_months} dan biri bo'lishi mumkin"
            )
        return v


class GiftOrderCreate(OrderBase):
    """Telegram sovg'asi (getAvailableGifts dan olingan gift_id bo'yicha)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"recipient_user_id": 123456789, "gift_id": "5170233102089322756"}
        }
    )

    gift_id: str = Field(min_length=1, max_length=64)
    pay_for_upgrade: bool = Field(
        default=False, description="Upgrade to'lovini jo'natuvchi hisobidan to'lash"
    )


class OrderEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_status: str | None
    to_status: str
    note: str | None
    created_at: datetime


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: OrderType
    status: OrderStatus

    recipient_username: str | None
    recipient_user_id: int | None
    quantity: int | None
    months: int | None
    gift_id: str | None
    message: str | None

    provider: str
    provider_ref: str | None
    attempts: int
    error_code: str | None
    error_message: str | None

    amount: Decimal | None
    currency: str | None
    extra_data: dict[str, Any] | None

    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class OrderDetail(OrderRead):
    events: list[OrderEventRead] = Field(default_factory=list)
    provider_response: dict[str, Any] | None = None


class OrderListQuery(BaseModel):
    status: OrderStatus | None = None
    type: OrderType | None = None
    recipient_username: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
