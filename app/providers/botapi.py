"""Telegram Bot API provayderi — rasmiy `sendGift` / `giftPremiumSubscription`.

Bu provayder botning **o'z Stars balansidan** to'laydi (Fragment'dan farqli
o'laroq TON hamyon kerak emas). Bot balansini `createInvoiceLink` (XTR) orqali
to'ldirasiz.

Qo'llab-quvvatlanadigan metodlar:
  * getAvailableGifts        — sovg'alar katalogi
  * sendGift                 — sovg'a jo'natish (user_id yoki chat_id)
  * giftPremiumSubscription  — Premium sovg'a (3/6/12 oy)

Cheklov: Bot API orqali "N dona Stars sotib olib berish" mumkin emas —
buning uchun `fragment` provayderidan foydalaning.
"""

from decimal import Decimal
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import (
    ProviderNotConfiguredError,
    ProviderPermanentError,
    ProviderTemporaryError,
)
from app.core.logging import get_logger
from app.models.order import Order
from app.providers.base import BaseProvider, GiftItem, ProviderResult

log = get_logger(__name__)

# giftPremiumSubscription uchun Telegram belgilagan qat'iy narxlar
PREMIUM_STAR_COST = {3: 1000, 6: 1500, 12: 2500}

# Qayta urinish foydasiz bo'lgan Telegram xatoliklari
PERMANENT_DESCRIPTIONS = (
    "user not found",
    "chat not found",
    "user_id_invalid",
    "gift_invalid",
    "gift_id_invalid",
    "balance_too_low",
    "not enough stars",
    "bot was blocked",
    "forbidden",
    "gift_sold_out",
    "peer_id_invalid",
    "user_is_bot",
)


class BotApiProvider(BaseProvider):
    name = "botapi"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        return bool(settings.botapi_enabled and settings.bot_token)

    @property
    def _base(self) -> str:
        return f"{settings.botapi_base_url.rstrip('/')}/bot{settings.bot_token}"

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.is_configured:
            raise ProviderNotConfiguredError(
                "Bot API provayderi sozlanmagan: BOTAPI_ENABLED va BOT_TOKEN ni to'ldiring"
            )

        client = await self._http()
        url = f"{self._base}/{method}"
        body = {k: v for k, v in (payload or {}).items() if v is not None}

        # PUL XAVFSIZLIGI: sendGift/giftPremiumSubscription yuborilgandan keyin
        # timeout bo'lsa, Telegram uni qabul qilgan bo'lishi mumkin. Avtomatik
        # qayta urinish ikkinchi sovg'ani jo'natib yuborardi. Faqat o'qish
        # metodlari (getMe, getAvailableGifts) xavfsiz qayta uriniladi.
        is_mutating = method in {"sendGift", "giftPremiumSubscription", "transferGift"}
        retry_unsafe = is_mutating and not settings.retry_on_network_timeout

        try:
            response = await client.post(url, json=body)
        except httpx.TimeoutException as exc:
            if retry_unsafe:
                raise ProviderPermanentError(
                    f"botapi: timeout ({method}) — sovg'a jo'natilgan bo'lishi mumkin, "
                    "avtomatik qayta urinish o'chirilgan (qo'lda tekshiring)"
                ) from exc
            raise ProviderTemporaryError(f"botapi: timeout ({method})") from exc
        except httpx.HTTPError as exc:
            if retry_unsafe:
                raise ProviderPermanentError(
                    f"botapi: tarmoq uzilishi ({method}) — holat noma'lum, "
                    f"qo'lda tekshiring: {exc}"
                ) from exc
            raise ProviderTemporaryError(f"botapi: tarmoq xatosi ({method}): {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderTemporaryError(
                f"botapi: JSON bo'lmagan javob (HTTP {response.status_code})"
            ) from exc

        if data.get("ok"):
            return data.get("result")

        description = str(data.get("description", "")).lower()
        error_code = data.get("error_code")
        log.warning("botapi_error", method=method, code=error_code, description=description)

        # 429 -> retry_after bilan vaqtinchalik
        if error_code == 429 or "too many requests" in description:
            retry_after = (data.get("parameters") or {}).get("retry_after", 1)
            raise ProviderTemporaryError(
                f"botapi: rate limit, {retry_after}s dan keyin qayta urining"
            )

        if any(marker in description for marker in PERMANENT_DESCRIPTIONS):
            raise ProviderPermanentError(f"botapi: {data.get('description')}")

        if error_code and 400 <= int(error_code) < 500:
            raise ProviderPermanentError(f"botapi: {data.get('description')}")

        raise ProviderTemporaryError(f"botapi: {data.get('description')}")

    # --- Public API ---

    async def buy_stars(self, order: Order) -> ProviderResult:
        raise ProviderPermanentError(
            "Bot API orqali foydalanuvchiga Stars sotib olib berish mumkin emas. "
            "Buning uchun `fragment` provayderini ishlating (provider=fragment)."
        )

    async def buy_premium(self, order: Order) -> ProviderResult:
        if not order.recipient_user_id:
            raise ProviderPermanentError(
                "botapi provayderi uchun recipient_user_id majburiy (username qo'llanmaydi)"
            )
        months = order.months or 3
        star_count = PREMIUM_STAR_COST.get(months)
        if star_count is None:
            raise ProviderPermanentError(
                f"months faqat {list(PREMIUM_STAR_COST)} dan biri bo'lishi mumkin"
            )

        result = await self._call(
            "giftPremiumSubscription",
            {
                "user_id": order.recipient_user_id,
                "month_count": months,
                "star_count": star_count,
                "text": order.message,
            },
        )
        return ProviderResult(
            success=True,
            reference=f"premium_{order.recipient_user_id}_{months}m",
            amount=Decimal(star_count),
            currency="XTR",
            raw={"result": result, "star_count": star_count, "month_count": months},
        )

    async def send_gift(self, order: Order) -> ProviderResult:
        if not order.recipient_user_id and not order.recipient_username:
            raise ProviderPermanentError("recipient_user_id yoki recipient_username majburiy")

        payload: dict[str, Any] = {
            "gift_id": order.gift_id,
            "text": order.message,
            "pay_for_upgrade": (order.extra_data or {}).get("pay_for_upgrade", False),
        }
        # sendGift: user_id YOKI chat_id — ikkalasi birga bo'lmasligi kerak
        if order.recipient_user_id:
            payload["user_id"] = order.recipient_user_id
        else:
            payload["chat_id"] = f"@{order.recipient_username}"

        result = await self._call("sendGift", payload)
        return ProviderResult(
            success=True,
            reference=f"gift_{order.gift_id}",
            currency="XTR",
            raw={"result": result},
        )

    async def list_gifts(self) -> list[GiftItem]:
        result = await self._call("getAvailableGifts")
        gifts = (result or {}).get("gifts", [])
        return [
            GiftItem(
                id=str(g.get("id")),
                sticker_emoji=(g.get("sticker") or {}).get("emoji"),
                star_count=int(g.get("star_count", 0)),
                upgrade_star_count=g.get("upgrade_star_count"),
                total_count=g.get("total_count"),
                remaining_count=g.get("remaining_count"),
            )
            for g in gifts
        ]

    async def get_balance(self) -> dict[str, Any]:
        me = await self._call("getMe")
        return {"provider": "botapi", "bot": me}

    async def health(self) -> str:
        if not self.is_configured:
            return "not_configured"
        try:
            me = await self._call("getMe")
            return f"ok (@{me.get('username')})"
        except Exception as exc:
            return f"error: {exc}"
