"""ARQ worker — buyurtmalarni bajaradi va webhooklarni yuboradi.

Ishga tushirish:
    arq app.workers.worker.WorkerSettings
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from arq import cron
from sqlalchemy import select, update

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.redis import arq_redis_settings, close_redis
from app.db.session import SessionLocal, engine
from app.models.enums import OrderStatus
from app.models.order import Order
from app.providers.registry import close_providers
from app.services.orders import execute_order
from app.services.webhooks import deliver_webhook, pending_webhook_ids

log = get_logger(__name__)


# --------------------------------------------------------------------------
#  Tasklar
# --------------------------------------------------------------------------


async def process_order(ctx: dict[str, Any], order_id: str) -> str:
    """Bitta buyurtmani bajaradi; retryable xatolikda o'zini qayta navbatga qo'yadi."""
    oid = uuid.UUID(order_id)

    async with SessionLocal() as db:
        order = await execute_order(db, oid)
        status = order.status
        attempts = order.attempts

    # execute_order retryable xatolikda statusni PENDING'da qoldiradi
    if status == OrderStatus.PENDING.value:
        delay = settings.order_retry_backoff_seconds * (2 ** (attempts - 1))
        redis = ctx["redis"]
        await redis.enqueue_job(
            "process_order",
            order_id,
            # Barqaror _job_id yo'q — queue.enqueue_order() dagi izohga qarang
            # (arq:result:* kaliti tufayli retry jimgina yo'qolib ketardi).
            _defer_by=timedelta(seconds=delay),
        )
        log.info("order_requeued", order_id=order_id, attempts=attempts, delay=delay)
        return f"retry_scheduled:{delay}s"

    # Webhook yozuvlari yaratilgan bo'lsa — darhol yuborishga urinamiz
    async with SessionLocal() as db:
        for delivery_id in await pending_webhook_ids(db, limit=20):
            await ctx["redis"].enqueue_job("send_webhook", str(delivery_id))

    return status


async def send_webhook(ctx: dict[str, Any], delivery_id: str) -> bool:
    """Bitta webhookni yetkazadi; muvaffaqiyatsiz bo'lsa backoff bilan qayta rejalashtiradi."""
    did = uuid.UUID(delivery_id)
    async with SessionLocal() as db:
        ok = await deliver_webhook(db, did)
        await db.commit()
    return ok


async def retry_pending_webhooks(ctx: dict[str, Any]) -> int:
    """Cron: vaqti kelgan webhooklarni qayta yuboradi."""
    async with SessionLocal() as db:
        ids = await pending_webhook_ids(db, limit=200)
    for delivery_id in ids:
        await ctx["redis"].enqueue_job("send_webhook", str(delivery_id))
    if ids:
        log.info("webhooks_requeued", count=len(ids))
    return len(ids)


async def sweep_stuck_orders(ctx: dict[str, Any]) -> int:
    """Cron: PROCESSING holatida osilib qolgan buyurtmalarni qayta navbatga qo'yadi.

    Worker qulab tushsa (OOM, deploy) buyurtma PROCESSING'da qolib ketishi mumkin.
    """
    threshold = datetime.now(timezone.utc) - timedelta(
        seconds=settings.order_job_timeout_seconds * 3
    )

    async with SessionLocal() as db:
        stmt = select(Order.id, Order.attempts).where(
            Order.status == OrderStatus.PROCESSING.value,
            Order.started_at < threshold,
        )
        rows = list((await db.execute(stmt)).all())

        for order_id, attempts in rows:
            if attempts >= settings.order_max_attempts:
                await db.execute(
                    update(Order)
                    .where(Order.id == order_id)
                    .values(
                        status=OrderStatus.FAILED.value,
                        error_code="stuck_timeout",
                        error_message="Buyurtma bajarilish vaqtida osilib qoldi",
                        completed_at=datetime.now(timezone.utc),
                    )
                )
            else:
                await db.execute(
                    update(Order)
                    .where(Order.id == order_id)
                    .values(status=OrderStatus.PENDING.value)
                )
                await ctx["redis"].enqueue_job("process_order", str(order_id))
        await db.commit()

    if rows:
        log.warning("stuck_orders_swept", count=len(rows))
    return len(rows)


# --------------------------------------------------------------------------
#  Worker sozlamalari
# --------------------------------------------------------------------------


async def startup(ctx: dict[str, Any]) -> None:
    setup_logging()
    log.info("worker_started", env=settings.env, default_provider=settings.default_provider)


async def shutdown(ctx: dict[str, Any]) -> None:
    await close_providers()
    await close_redis()
    await engine.dispose()
    log.info("worker_stopped")


class WorkerSettings:
    functions: ClassVar[list] = [process_order, send_webhook]
    cron_jobs: ClassVar[list] = [
        cron(retry_pending_webhooks, minute=set(range(0, 60, 2)), run_at_startup=False),
        cron(sweep_stuck_orders, minute={0, 15, 30, 45}, run_at_startup=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = arq_redis_settings()
    max_jobs = 20
    job_timeout = settings.order_job_timeout_seconds
    keep_result = 3600
    max_tries = 1  # retry logikasi bizning qo'limizda (order.attempts)
