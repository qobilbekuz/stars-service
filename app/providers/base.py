"""Provayder kontrakti.

Har bir provayder (fragment, botapi, mock) shu interfeysni amalga oshiradi,
shuning uchun order-service provayderdan mustaqil ishlaydi.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.models.order import Order


@dataclass(slots=True)
class ProviderResult:
    """Provayder muvaffaqiyatli bajargandagi natija."""

    success: bool
    reference: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GiftItem:
    id: str
    sticker_emoji: str | None
    star_count: int
    upgrade_star_count: int | None = None
    total_count: int | None = None
    remaining_count: int | None = None


class BaseProvider(ABC):
    """Provayder bazasi."""

    name: str = "base"

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Kerakli kredensiallar mavjudmi."""

    @abstractmethod
    async def buy_stars(self, order: Order) -> ProviderResult:
        """Stars sotib olish."""

    @abstractmethod
    async def buy_premium(self, order: Order) -> ProviderResult:
        """Premium obuna sovg'a qilish."""

    async def send_gift(self, order: Order) -> ProviderResult:
        """Sovg'a jo'natish (hamma provayder qo'llab-quvvatlamaydi)."""
        raise NotImplementedError(f"{self.name} provayderi gift'ni qo'llab-quvvatlamaydi")

    async def list_gifts(self) -> list[GiftItem]:
        """Mavjud sovg'alar katalogi."""
        return []

    async def get_balance(self) -> dict[str, Any]:
        """Provayder hisobidagi balans."""
        return {}

    async def health(self) -> str:
        """'ok' | 'not_configured' | 'error: ...'"""
        return "ok" if self.is_configured else "not_configured"

    async def close(self) -> None:
        """Resurslarni bo'shatish."""
        return None

    # --- Yordamchi ---

    def _require_username(self, order: Order) -> str:
        from app.core.exceptions import ProviderPermanentError

        if not order.recipient_username:
            raise ProviderPermanentError(
                f"{self.name} provayderi uchun recipient_username majburiy"
            )
        return order.recipient_username
