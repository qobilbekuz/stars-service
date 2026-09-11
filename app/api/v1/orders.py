"""Buyurtma endpointlari: stars, premium, gift."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from app.api.deps import CurrentClient, DbSession
from app.core.config import settings
from app.models.enums import OrderStatus, OrderType
from app.models.order import Order
from app.schemas.common import Page
from app.schemas.orders import (
    GiftOrderCreate,
    OrderDetail,
    OrderRead,
    PremiumOrderCreate,
    StarsOrderCreate,
)
from app.services.orders import (
    cancel_order,
    create_order,
    get_order,
    list_orders,
    mark_queued,
)
from app.workers.queue import enqueue_order

router = APIRouter(prefix="/orders", tags=["orders"])

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="Takroriy so'rovlarni oldini olish uchun unikal kalit (tavsiya etiladi)",
    ),
]


async def _create_and_dispatch(
    db,
    client,
    order_type: OrderType,
    payload: dict,
    idempotency_key: str | None,
    response: Response,
) -> OrderRead:
    order, created = await create_order(
        db,
        client=client,
        order_type=order_type,
        payload=payload,
        idempotency_key=idempotency_key,
    )

    if not created:
        # Idempotent takroriy so'rov — mavjud buyurtma qaytariladi
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"
        return OrderRead.model_validate(order)

    job_id = await enqueue_order(order.id)
    await mark_queued(db, order, job_id)
    await db.refresh(order)

    response.status_code = status.HTTP_202_ACCEPTED
    return OrderRead.model_validate(order)


@router.post(
    "/stars",
    response_model=OrderRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Telegram Stars sotib olish",
    description="Ko'rsatilgan foydalanuvchiga Stars sotib olib beradi. "
    "Asinxron bajariladi — natijani `GET /orders/{id}` yoki webhook orqali oling.",
)
async def create_stars_order(
    body: StarsOrderCreate,
    db: DbSession,
    client: CurrentClient,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> OrderRead:
    return await _create_and_dispatch(
        db, client, OrderType.STARS, body.model_dump(), idempotency_key, response
    )


@router.post(
    "/premium",
    response_model=OrderRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Telegram Premium sovg'a qilish",
)
async def create_premium_order(
    body: PremiumOrderCreate,
    db: DbSession,
    client: CurrentClient,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> OrderRead:
    return await _create_and_dispatch(
        db, client, OrderType.PREMIUM, body.model_dump(), idempotency_key, response
    )


@router.post(
    "/gift",
    response_model=OrderRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Telegram sovg'asi jo'natish",
    description="`GET /catalog/gifts` dan olingan `gift_id` bo'yicha sovg'a jo'natadi "
    "(botapi provayderi, bot Stars balansidan to'lanadi).",
)
async def create_gift_order(
    body: GiftOrderCreate,
    db: DbSession,
    client: CurrentClient,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> OrderRead:
    return await _create_and_dispatch(
        db, client, OrderType.GIFT, body.model_dump(), idempotency_key, response
    )


@router.get("", response_model=Page[OrderRead], summary="Buyurtmalar ro'yxati")
async def get_orders(
    db: DbSession,
    client: CurrentClient,
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
    order_type: Annotated[OrderType | None, Query(alias="type")] = None,
    recipient_username: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[OrderRead]:
    items, total = await list_orders(
        db,
        client_id=client.id,
        status=order_status.value if order_status else None,
        order_type=order_type.value if order_type else None,
        recipient_username=recipient_username,
        limit=limit,
        offset=offset,
    )
    return Page[OrderRead](
        items=[OrderRead.model_validate(o) for o in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{order_id}", response_model=OrderDetail, summary="Buyurtma tafsilotlari")
async def get_order_detail(
    order_id: uuid.UUID,
    db: DbSession,
    client: CurrentClient,
) -> OrderDetail:
    order = await get_order(db, order_id, client_id=client.id)
    return OrderDetail.model_validate(order)


@router.post("/{order_id}/cancel", response_model=OrderRead, summary="Buyurtmani bekor qilish")
async def cancel(
    order_id: uuid.UUID,
    db: DbSession,
    client: CurrentClient,
) -> OrderRead:
    order = await get_order(db, order_id, client_id=client.id)
    order = await cancel_order(db, order)
    return OrderRead.model_validate(order)


@router.post(
    "/{order_id}/retry",
    response_model=OrderRead,
    summary="Muvaffaqiyatsiz buyurtmani qayta urinish",
)
async def retry(
    order_id: uuid.UUID,
    db: DbSession,
    client: CurrentClient,
) -> OrderRead:
    from sqlalchemy import update as sa_update

    from app.core.exceptions import ConflictError

    order = await get_order(db, order_id, client_id=client.id)

    # --- Tekshiruvlar UPDATE dan OLDIN: rollback'dan keyin ORM obyektining
    #     atributlari eskiradi va ularga murojaat qilish MissingGreenlet beradi.

    # PUL XAVFSIZLIGI: provider_ref mavjud bo'lsa, provayder tomonida
    # tranzaksiya allaqachon amalga oshgan. Qayta urinish ikkinchi marta
    # to'lovga olib keladi — rad etamiz.
    if order.provider_ref:
        raise ConflictError(
            "Bu buyurtma provayder tomonida allaqachon bajarilgan "
            f"(ref: {order.provider_ref}). Qayta urinish ikki marta to'lovga "
            "olib kelishi mumkin — qo'lda tekshiring."
        )

    if order.status != OrderStatus.FAILED.value:
        raise ConflictError(
            f"Faqat 'failed' holatidagi buyurtmani qayta urinish mumkin (hozir: {order.status})"
        )

    # attempts ATAYLAB nolga tushirilmaydi — aks holda order_max_attempts
    # cheklovi mazmunini yo'qotardi va mijoz /retry ni tsiklda chaqirib
    # cheksiz miqdorda haqiqiy to'lov qila olardi.
    if order.attempts >= settings.order_max_attempts:
        raise ConflictError(
            f"Urinishlar limiti tugagan ({order.attempts}/{settings.order_max_attempts}). "
            "Yangi buyurtma yarating."
        )

    # Atomik egallash: ikkita parallel /retry bir-birini bosib ketmasligi uchun
    claimed = await db.execute(
        sa_update(Order)
        .where(Order.id == order.id, Order.status == OrderStatus.FAILED.value)
        .values(
            status=OrderStatus.PENDING.value,
            error_code=None,
            error_message=None,
            completed_at=None,
        )
        .returning(Order.id)
    )
    if claimed.first() is None:
        raise ConflictError("Buyurtma holati o'zgardi — qaytadan urinib ko'ring")

    await db.commit()
    await db.refresh(order)

    await db.refresh(order)
    await db.commit()

    job_id = await enqueue_order(order.id)
    await mark_queued(db, order, job_id)
    await db.refresh(order)
    return OrderRead.model_validate(order)
