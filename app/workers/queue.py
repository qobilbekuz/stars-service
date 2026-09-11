"""API tomonidan ishlatiladigan navbat (ARQ) yordamchilari."""

import uuid
from datetime import timedelta

from arq import create_pool
from arq.connections import ArqRedis

from app.core.logging import get_logger
from app.core.redis import arq_redis_settings

log = get_logger(__name__)

_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(arq_redis_settings())
    return _pool


async def close_arq_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue_order(order_id: uuid.UUID, delay_seconds: int = 0) -> str | None:
    """Buyurtmani bajarish navbatiga qo'yadi. Qaytadi: job_id.

    _job_id ATAYLAB berilmaydi (arq o'zi uuid generatsiya qiladi).

    Sabab: arq `enqueue_job` `arq:job:<id>` YOKI `arq:result:<id>` kaliti mavjud
    bo'lsa jimgina None qaytaradi, natija kaliti esa keep_result=3600 tufayli
    bir soat yashaydi. Barqaror _job_id ishlatilganda bir soat ichidagi har
    qanday qayta urinish JIMGINA TASHLAB YUBORILARDI — buyurtma QUEUED holatida
    orqasida hech qanday jobsiz abadiy muzlab qolardi.

    Dublikat jobdan himoya endi `execute_order` dagi atomik compare-and-swap
    zimmasida: bir nechta job kelsa ham, faqat bittasi buyurtmani egallaydi.
    """
    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "process_order",
        str(order_id),
        _defer_by=timedelta(seconds=delay_seconds) if delay_seconds else None,
    )
    job_id = job.job_id if job else None
    if job_id is None:
        log.error("order_enqueue_failed", order_id=str(order_id))
    else:
        log.info("order_enqueued", order_id=str(order_id), job_id=job_id, delay=delay_seconds)
    return job_id


async def enqueue_webhook_delivery(delivery_id: uuid.UUID, delay_seconds: int = 0) -> str | None:
    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "send_webhook",
        str(delivery_id),
        _defer_by=timedelta(seconds=delay_seconds) if delay_seconds else None,
    )
    return job.job_id if job else None
