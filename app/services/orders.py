"""Buyurtma hayotiy sikli: yaratish, navbatga qo'yish, bajarish."""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ProviderError,
    ProviderPermanentError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.api_client import ApiClient
from app.models.enums import OrderStatus, OrderType
from app.models.order import Order, OrderEvent
from app.providers import get_provider
from app.services.webhooks import enqueue_webhook

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def record_event(
    db: AsyncSession,
    order: Order,
    *,
    from_status: str | None,
    to_status: str,
    note: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        OrderEvent(
            order_id=order.id,
            from_status=from_status,
            to_status=to_status,
            note=note,
            payload=payload,
        )
    )


def resolve_provider_name(client: ApiClient, requested: str | None, order_type: OrderType) -> str:
    """Provayderni tanlash: so'rov > mijoz sozlamasi > standart."""
    name = requested or client.forced_provider or settings.default_provider

    # Bot API stars sotib olishni qo'llab-quvvatlamaydi — erta xatolik beramiz
    if name == "botapi" and order_type == OrderType.STARS:
        raise ValidationError(
            "botapi provayderi 'stars' turini qo'llab-quvvatlamaydi. "
            "provider='fragment' dan foydalaning."
        )
    if name == "fragment" and order_type == OrderType.GIFT:
        raise ValidationError(
            "fragment provayderi 'gift' turini qo'llab-quvvatlamaydi. "
            "provider='botapi' dan foydalaning."
        )
    return name


async def find_by_idempotency_key(db: AsyncSession, client_id: uuid.UUID, key: str) -> Order | None:
    stmt = select(Order).where(Order.client_id == client_id, Order.idempotency_key == key)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_order(
    db: AsyncSession,
    *,
    client: ApiClient,
    order_type: OrderType,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> tuple[Order, bool]:
    """Buyurtma yaratadi. Qaytadi: (order, created).

    `created=False` bo'lsa — idempotency kaliti bo'yicha mavjud buyurtma qaytarildi.
    """
    if idempotency_key:
        existing = await find_by_idempotency_key(db, client.id, idempotency_key)
        if existing is not None:
            return existing, False

    provider_name = resolve_provider_name(client, payload.get("provider"), order_type)

    extra = dict(payload.get("extra_data") or {})
    if payload.get("webhook_url"):
        extra["webhook_url"] = payload["webhook_url"]
    if payload.get("pay_for_upgrade"):
        extra["pay_for_upgrade"] = True

    order = Order(
        client_id=client.id,
        idempotency_key=idempotency_key,
        type=order_type.value,
        status=OrderStatus.PENDING.value,
        recipient_username=payload.get("recipient_username"),
        recipient_user_id=payload.get("recipient_user_id"),
        quantity=payload.get("quantity"),
        months=payload.get("months"),
        gift_id=payload.get("gift_id"),
        message=payload.get("message"),
        provider=provider_name,
        extra_data=extra or None,
    )
    db.add(order)

    try:
        await db.flush()
    except IntegrityError:
        # Parallel so'rov ayni shu idempotency kalitini band qilib ulgurdi
        await db.rollback()
        if idempotency_key:
            existing = await find_by_idempotency_key(db, client.id, idempotency_key)
            if existing is not None:
                return existing, False
        raise ConflictError("Buyurtma yaratishda konflikt yuz berdi") from None

    await record_event(
        db, order, from_status=None, to_status=OrderStatus.PENDING.value, note="Buyurtma yaratildi"
    )
    await db.commit()
    await db.refresh(order)

    log.info(
        "order_created",
        order_id=str(order.id),
        type=order.type,
        provider=order.provider,
        client=client.name,
    )
    return order, True


async def mark_queued(db: AsyncSession, order: Order, job_id: str | None = None) -> None:
    """Buyurtmani QUEUED deb belgilaydi — faqat u hali PENDING bo'lsa.

    LOST UPDATE tuzatildi: enqueue_order() job'ni ko'rinadigan qilgach, worker
    uni millisekundlarda olib PROCESSING/COMPLETED ga o'tkazishi mumkin.
    Ilgari bu yerda shartsiz `status = 'queued'` yozilardi va to'langan,
    yakunlangan buyurtma qaytib 'queued' ga tushib qolardi — mijoz esa uni
    bajarilmagan deb hisoblab, ikkinchi marta buyurtma berardi.
    """
    previous = order.status
    result = await db.execute(
        update(Order)
        .where(Order.id == order.id, Order.status == OrderStatus.PENDING.value)
        .values(status=OrderStatus.QUEUED.value, queued_at=_now())
        .returning(Order.id)
    )

    if result.first() is None:
        # Worker bizdan oldin ulgurdi — uning holati to'g'ri, tegmaymiz
        await db.commit()
        await db.refresh(order)
        log.info("mark_queued_skipped", order_id=str(order.id), status=order.status)
        return

    await db.refresh(order)
    await record_event(
        db,
        order,
        from_status=previous,
        to_status=OrderStatus.QUEUED.value,
        note="Navbatga qo'yildi",
        payload={"job_id": job_id} if job_id else None,
    )
    await db.commit()


async def get_order(
    db: AsyncSession, order_id: uuid.UUID, client_id: uuid.UUID | None = None
) -> Order:
    stmt = select(Order).where(Order.id == order_id)
    if client_id is not None:
        stmt = stmt.where(Order.client_id == client_id)
    order = (await db.execute(stmt)).scalar_one_or_none()
    if order is None:
        raise NotFoundError(f"Buyurtma topilmadi: {order_id}")
    return order


async def list_orders(
    db: AsyncSession,
    *,
    client_id: uuid.UUID,
    status: str | None = None,
    order_type: str | None = None,
    recipient_username: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Order], int]:
    filters = [Order.client_id == client_id]
    if status:
        filters.append(Order.status == status)
    if order_type:
        filters.append(Order.type == order_type)
    if recipient_username:
        filters.append(Order.recipient_username == recipient_username.lstrip("@"))

    total = (await db.execute(select(func.count()).select_from(Order).where(*filters))).scalar_one()

    stmt = (
        select(Order).where(*filters).order_by(Order.created_at.desc()).limit(limit).offset(offset)
    )
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def cancel_order(db: AsyncSession, order: Order) -> Order:
    if OrderStatus(order.status).is_terminal:
        raise ConflictError(f"Buyurtma allaqachon yakuniy holatda: {order.status}")
    if order.status == OrderStatus.PROCESSING.value:
        raise ConflictError("Bajarilayotgan buyurtmani bekor qilib bo'lmaydi")

    previous = order.status
    order.status = OrderStatus.CANCELLED.value
    order.completed_at = _now()
    await record_event(
        db,
        order,
        from_status=previous,
        to_status=order.status,
        note="Foydalanuvchi tomonidan bekor qilindi",
    )
    await db.commit()
    await db.refresh(order)
    return order


# --------------------------------------------------------------------------
#  Worker tomonidan chaqiriladigan asosiy bajaruvchi
# --------------------------------------------------------------------------


async def execute_order(db: AsyncSession, order_id: uuid.UUID) -> Order:
    """Buyurtmani provayder orqali bajaradi.

    Bu funksiya hech qachon exception ko'tarmaydi — natija har doim
    order.status ichida aks etadi. Retry qaroriniworker `attempts` va
    xatolik turiga qarab qabul qiladi.

    PUL XAVFSIZLIGI: buyurtmani "egallash" atomik compare-and-swap orqali
    amalga oshadi. Ilgari bu `db.get()` + tekshiruv + yozuv edi, ya'ni ikkita
    worker bir buyurtmani bir vaqtda olib, provayderni IKKI MARTA chaqirishi
    va hamyondan ikki marta yechilishi mumkin edi. Endi faqat bitta worker
    UPDATE ... WHERE status IN (pending, queued) dan qator qaytara oladi.
    """
    now = _now()
    claimed = await db.execute(
        update(Order)
        .where(
            Order.id == order_id,
            Order.status.in_([OrderStatus.PENDING.value, OrderStatus.QUEUED.value]),
        )
        .values(
            status=OrderStatus.PROCESSING.value,
            started_at=now,  # har urinishda yangilanadi — stuck-sweeper
            attempts=Order.attempts + 1,  # yolg'on ishlamasligi uchun
        )
        .returning(Order.id)
    )
    if claimed.first() is None:
        # Buyurtma yo'q, allaqachon yakuniy holatda, yoki boshqa worker egallagan
        await db.rollback()
        order = await db.get(Order, order_id)
        if order is None:
            raise NotFoundError(f"Buyurtma topilmadi: {order_id}")
        log.info("order_not_claimable", order_id=str(order.id), status=order.status)
        return order

    await db.commit()

    order = await db.get(Order, order_id)
    await db.refresh(order)

    await record_event(
        db,
        order,
        from_status=OrderStatus.QUEUED.value,
        to_status=OrderStatus.PROCESSING.value,
        note=f"Bajarilmoqda (urinish {order.attempts})",
    )
    await db.commit()

    try:
        # get_provider ATAYLAB try ichida: noto'g'ri provayder nomi (masalan
        # forced_provider da xato) ilgari exception'ni yuqoriga otib yuborardi
        # va buyurtma abadiy PROCESSING da qolib ketardi.
        provider = get_provider(order.provider)

        if order.type == OrderType.STARS.value:
            result = await provider.buy_stars(order)
        elif order.type == OrderType.PREMIUM.value:
            result = await provider.buy_premium(order)
        elif order.type == OrderType.GIFT.value:
            result = await provider.send_gift(order)
        else:
            raise ProviderPermanentError(f"Noma'lum buyurtma turi: {order.type}")

    except ProviderError as exc:
        await _handle_failure(db, order, exc)
        return order
    except Exception as exc:
        log.exception("order_unexpected_error", order_id=str(order.id))
        await _handle_failure(db, order, ProviderPermanentError(f"Kutilmagan xatolik: {exc}"))
        return order

    order.status = OrderStatus.COMPLETED.value
    order.provider_ref = result.reference
    order.provider_response = result.raw
    order.amount = result.amount
    order.currency = result.currency
    order.completed_at = _now()
    order.error_code = None
    order.error_message = None

    await record_event(
        db,
        order,
        from_status=OrderStatus.PROCESSING.value,
        to_status=order.status,
        note="Muvaffaqiyatli bajarildi",
        payload={"reference": result.reference},
    )

    client = await db.get(ApiClient, order.client_id)
    if client:
        await enqueue_webhook(db, client=client, order=order, event="order.completed")

    await db.commit()
    await db.refresh(order)

    log.info(
        "order_completed",
        order_id=str(order.id),
        provider=order.provider,
        reference=result.reference,
    )
    return order


async def _handle_failure(db: AsyncSession, order: Order, exc: ProviderError) -> None:
    """Xatolikni qayd etadi; retryable bo'lsa PENDING'da qoldiradi."""
    retryable = getattr(exc, "retryable", False)
    exhausted = order.attempts >= settings.order_max_attempts

    order.error_code = exc.code
    order.error_message = str(exc.message)

    if retryable and not exhausted:
        order.status = OrderStatus.PENDING.value
        note = (
            "Vaqtinchalik xatolik, qayta urinish rejalashtirildi "
            f"({order.attempts}/{settings.order_max_attempts})"
        )
        await record_event(
            db,
            order,
            from_status=OrderStatus.PROCESSING.value,
            to_status=order.status,
            note=note,
            payload={"error": order.error_message},
        )
        await db.commit()
        log.warning(
            "order_retry_scheduled",
            order_id=str(order.id),
            attempts=order.attempts,
            error=order.error_message,
        )
        return

    order.status = OrderStatus.FAILED.value
    order.completed_at = _now()
    note = "Qayta urinishlar tugadi" if exhausted else "Doimiy xatolik"
    await record_event(
        db,
        order,
        from_status=OrderStatus.PROCESSING.value,
        to_status=order.status,
        note=note,
        payload={"error": order.error_message},
    )

    client = await db.get(ApiClient, order.client_id)
    if client:
        await enqueue_webhook(db, client=client, order=order, event="order.failed")

    await db.commit()
    log.error(
        "order_failed", order_id=str(order.id), attempts=order.attempts, error=order.error_message
    )
