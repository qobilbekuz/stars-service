"""Fragment provayderi — `fragment-api-lib` orqali Stars/Premium sotib olish.

Kutubxona sinxron ishlaydi, shuning uchun har bir chaqiruv `asyncio.to_thread`
ichida bajariladi va event-loop bloklanmaydi.

O'rnatish:
    pip install fragment-api-lib

Kerakli sozlamalar (.env):
    FRAGMENT_ENABLED=true
    FRAGMENT_SEED="24 so'zli TON seed fraza"
    FRAGMENT_COOKIES='{...}'   # faqat KYC rejimida
    FRAGMENT_KYC=false

Eslatma: Fragment'ning rasmiy API'si yo'q. `fragment-api-lib` uchinchi tomon
xizmati (fragment-api.net) ustidan ishlaydi — production'da o'z riskingiz bilan
foydalaning va seed-frazani faqat ishonchli muhitda saqlang.
"""

import asyncio
import json
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.core.exceptions import (
    ProviderNotConfiguredError,
    ProviderPermanentError,
    ProviderTemporaryError,
)
from app.core.logging import get_logger
from app.models.order import Order
from app.providers.base import BaseProvider, ProviderResult

log = get_logger(__name__)

# Qayta urinish foydasiz bo'lgan xatolik belgilari
PERMANENT_MARKERS = (
    "not found",
    "invalid username",
    "username",
    "recipient",
    "insufficient",
    "not enough",
    "balance",
    "banned",
    "restricted",
    "unsupported",
    "invalid amount",
)


class FragmentProvider(BaseProvider):
    name = "fragment"

    def __init__(self) -> None:
        self._client: Any = None
        self._lock = asyncio.Lock()

    @property
    def is_configured(self) -> bool:
        if not settings.fragment_enabled or not settings.fragment_seed:
            return False
        return not (settings.fragment_kyc and not settings.fragment_cookies)

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is not None:
                return self._client
            if not self.is_configured:
                raise ProviderNotConfiguredError(
                    "Fragment provayderi sozlanmagan: FRAGMENT_ENABLED / FRAGMENT_SEED "
                    "(va KYC rejimida FRAGMENT_COOKIES) ni to'ldiring"
                )
            try:
                from fragment_api_lib.client import (
                    FragmentAPIClient,  # type: ignore[import-not-found]
                )
            except ImportError as exc:  # pragma: no cover
                raise ProviderNotConfiguredError(
                    "fragment-api-lib o'rnatilmagan. `pip install fragment-api-lib` bajaring"
                ) from exc
            self._client = FragmentAPIClient()
            return self._client

    @property
    def _cookies(self) -> Any:
        raw = settings.fragment_cookies
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw  # kutubxona xom string ham qabul qilishi mumkin

    @staticmethod
    def _classify(exc: Exception) -> Exception:
        """Kutubxona xatoligini retryable/permanent ga ajratadi.

        PUL XAVFSIZLIGI: ilgari bu metod TANIB BO'LMAGAN har qanday xatolikni
        retryable deb hisoblardi. To'lov so'rovi yuborilgandan keyin sodir
        bo'lgan timeout ham shunga tushardi — natijada TON tranzaksiyasi
        allaqachon tarqatilgan bo'lsa ham, servis uni qaytadan yuborardi va
        hamyondan 3 martagacha yechilardi.

        Endi tarmoq/timeout xatoliklari standart holatda XAVFSIZ TO'XTASH
        deb qaraladi: buyurtma `failed` bo'ladi va operator qo'lda tekshiradi.
        """
        text = str(exc).lower()

        if any(marker in text for marker in PERMANENT_MARKERS):
            return ProviderPermanentError(f"fragment: {exc}")

        is_network = any(
            marker in text
            for marker in ("timeout", "timed out", "connection", "read error", "eof", "reset")
        )
        if is_network and not settings.retry_on_network_timeout:
            return ProviderPermanentError(
                f"fragment: tarmoq uzilishi — tranzaksiya holati NOMA'LUM, "
                f"avtomatik qayta urinish o'chirilgan (qo'lda tekshiring): {exc}"
            )

        return ProviderTemporaryError(f"fragment: {exc}")

    @staticmethod
    def _as_dict(result: Any) -> dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        for attr in ("model_dump", "dict", "__dict__"):
            candidate = getattr(result, attr, None)
            if callable(candidate):
                try:
                    return dict(candidate())
                except Exception:
                    continue
            elif isinstance(candidate, dict):
                return dict(candidate)
        return {"result": str(result)}

    @staticmethod
    def _ensure_success(data: dict[str, Any]) -> None:
        """Kutubxona ba'zan xato o'rniga success=False qaytaradi."""
        if data.get("success") is False or data.get("ok") is False:
            message = str(data.get("error") or data.get("message") or "noma'lum xatolik")
            lowered = message.lower()
            if any(marker in lowered for marker in PERMANENT_MARKERS):
                raise ProviderPermanentError(f"fragment: {message}")
            raise ProviderTemporaryError(f"fragment: {message}")

    async def _call(self, method_name: str, **kwargs: Any) -> dict[str, Any]:
        client = await self._get_client()
        method = getattr(client, method_name, None)
        if method is None:
            raise ProviderPermanentError(
                f"fragment-api-lib da `{method_name}` metodi topilmadi — "
                "kutubxona versiyasini tekshiring"
            )
        try:
            raw = await asyncio.to_thread(method, **kwargs)
        except (ProviderPermanentError, ProviderTemporaryError):
            raise
        except Exception as exc:
            log.warning("fragment_call_failed", method=method_name, error=str(exc))
            raise self._classify(exc) from exc

        data = self._as_dict(raw)
        self._ensure_success(data)
        return data

    @staticmethod
    def _extract_ref(data: dict[str, Any]) -> str | None:
        for key in ("transaction_hash", "tx_hash", "hash", "id", "order_id", "reference"):
            value = data.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _extract_amount(data: dict[str, Any]) -> tuple[Decimal | None, str | None]:
        for key in ("amount", "price", "total", "cost"):
            value = data.get(key)
            if value is None:
                continue
            try:
                return Decimal(str(value)), str(data.get("currency") or "TON")
            except (ValueError, ArithmeticError):
                continue
        return None, None

    # --- Public API ---

    async def buy_stars(self, order: Order) -> ProviderResult:
        username = self._require_username(order)
        amount = order.quantity or 0

        if settings.fragment_kyc:
            data = await self._call(
                "buy_stars",
                username=username,
                amount=amount,
                show_sender=settings.fragment_show_sender,
                fragment_cookies=self._cookies,
                seed=settings.fragment_seed,
            )
        else:
            data = await self._call(
                "buy_stars_without_kyc",
                username=username,
                amount=amount,
                seed=settings.fragment_seed,
            )

        price, currency = self._extract_amount(data)
        return ProviderResult(
            success=True,
            reference=self._extract_ref(data),
            amount=price,
            currency=currency,
            raw=data,
        )

    async def buy_premium(self, order: Order) -> ProviderResult:
        username = self._require_username(order)
        months = order.months or 3

        if settings.fragment_kyc:
            data = await self._call(
                "buy_premium",
                username=username,
                duration=months,
                show_sender=settings.fragment_show_sender,
                fragment_cookies=self._cookies,
                seed=settings.fragment_seed,
            )
        else:
            data = await self._call(
                "buy_premium_without_kyc",
                username=username,
                duration=months,
                fragment_cookies=self._cookies,
                seed=settings.fragment_seed,
            )

        price, currency = self._extract_amount(data)
        return ProviderResult(
            success=True,
            reference=self._extract_ref(data),
            amount=price,
            currency=currency,
            raw=data,
        )

    async def get_balance(self) -> dict[str, Any]:
        return await self._call("get_balance", seed=settings.fragment_seed)

    async def health(self) -> str:
        if not self.is_configured:
            return "not_configured"
        try:
            await self._call("ping")
            return "ok"
        except Exception as exc:
            return f"error: {exc}"
