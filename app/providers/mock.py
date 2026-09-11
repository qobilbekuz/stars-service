"""Mock provayder — dev/test uchun. Hech qanday tashqi chaqiruv qilmaydi.

Deterministik xatolik ssenariylari (test yozish uchun qulay):
  * username 'fail_' bilan boshlansa  -> doimiy xatolik
  * username 'retry_' bilan boshlansa -> vaqtinchalik xatolik (retry bo'ladi)
  * username 'slow_' bilan boshlansa  -> 3 soniya kechikadi
"""

import asyncio
import uuid
from decimal import Decimal

from app.core.exceptions import ProviderPermanentError, ProviderTemporaryError
from app.models.order import Order
from app.providers.base import BaseProvider, GiftItem, ProviderResult

# Taxminiy narxlar (TON) — faqat demo uchun
STAR_PRICE_TON = Decimal("0.0043")
PREMIUM_PRICE_TON = {3: Decimal("11.5"), 6: Decimal("15.9"), 12: Decimal("28.5")}


class MockProvider(BaseProvider):
    name = "mock"

    @property
    def is_configured(self) -> bool:
        return True

    async def _simulate(self, order: Order) -> None:
        username = (order.recipient_username or "").lower()
        if username.startswith("slow_"):
            await asyncio.sleep(3)
        if username.startswith("retry_"):
            raise ProviderTemporaryError("mock: vaqtinchalik xatolik (simulyatsiya)")
        if username.startswith("fail_"):
            raise ProviderPermanentError("mock: doimiy xatolik (simulyatsiya)")
        await asyncio.sleep(0.2)

    async def buy_stars(self, order: Order) -> ProviderResult:
        await self._simulate(order)
        qty = order.quantity or 0
        return ProviderResult(
            success=True,
            reference=f"mock_stars_{uuid.uuid4().hex[:16]}",
            amount=STAR_PRICE_TON * qty,
            currency="TON",
            raw={"provider": "mock", "stars": qty, "recipient": order.recipient_username},
        )

    async def buy_premium(self, order: Order) -> ProviderResult:
        await self._simulate(order)
        months = order.months or 3
        return ProviderResult(
            success=True,
            reference=f"mock_premium_{uuid.uuid4().hex[:16]}",
            amount=PREMIUM_PRICE_TON.get(months, Decimal("0")),
            currency="TON",
            raw={"provider": "mock", "months": months, "recipient": order.recipient_username},
        )

    async def send_gift(self, order: Order) -> ProviderResult:
        await self._simulate(order)
        return ProviderResult(
            success=True,
            reference=f"mock_gift_{uuid.uuid4().hex[:16]}",
            amount=Decimal("0"),
            currency="XTR",
            raw={"provider": "mock", "gift_id": order.gift_id},
        )

    async def list_gifts(self) -> list[GiftItem]:
        return [
            GiftItem(id="mock_gift_15", sticker_emoji="🎁", star_count=15, upgrade_star_count=100),
            GiftItem(id="mock_gift_25", sticker_emoji="🌹", star_count=25, upgrade_star_count=200),
            GiftItem(id="mock_gift_50", sticker_emoji="🎂", star_count=50, upgrade_star_count=300),
        ]

    async def get_balance(self) -> dict:
        return {"provider": "mock", "balance": "999.999", "currency": "TON"}
