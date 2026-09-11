"""Chiquvchi webhooklar: HMAC imzo + eksponensial backoff bilan qayta urinish."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import sign_webhook
from app.models.api_client import ApiClient
from app.models.enums import WebhookStatus
from app.models.order import Order
from app.models.webhook import WebhookDelivery

log = get_logger(__name__)

MAX_RESPONSE_BODY = 2000


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | uuid.UUID):
        return str(value)
    return str(value)


def build_order_payload(order: Order, event: str) -> dict[str, Any]:
    return {
        "event": event,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": {
            "id": str(order.id),
            "type": order.type,
            "status": order.status,
            "recipient_username": order.recipient_username,
            "recipient_user_id": order.recipient_user_id,
            "quantity": order.quantity,
            "months": order.months,
            "gift_id": order.gift_id,
            "provider": order.provider,
            "provider_ref": order.provider_ref,
            "attempts": order.attempts,
            "error_code": order.error_code,
            "error_message": order.error_message,
            "amount": str(order.amount) if order.amount is not None else None,
            "currency": order.currency,
            "extra_data": order.extra_data,
            "completed_at": order.completed_at.isoformat() if order.completed_at else None,
        },
    }


async def enqueue_webhook(
    db: AsyncSession,
    *,
    client: ApiClient,
    order: Order,
    event: str,
) -> WebhookDelivery | None:
    """Webhook yozuvini yaratadi (yuborish workerda amalga oshadi)."""
    url = (order.extra_data or {}).get("webhook_url") or client.webhook_url
    if not url:
        return None

    delivery = WebhookDelivery(
        client_id=client.id,
        order_id=order.id,
        event=event,
        url=url,
        payload=build_order_payload(order, event),
        status=WebhookStatus.PENDING.value,
        next_attempt_at=datetime.now(timezone.utc),
    )
    db.add(delivery)
    await db.flush()
    return delivery


def _backoff_delay(attempt: int) -> timedelta:
    """1m, 2m, 4m, 8m, 16m, 32m (max 1 soat)."""
    return timedelta(seconds=min(60 * (2 ** (attempt - 1)), 3600))


async def deliver_webhook(db: AsyncSession, delivery_id: uuid.UUID) -> bool:
    """Bitta webhookni yetkazishga urinadi. Muvaffaqiyat bo'lsa True."""
    delivery = await db.get(WebhookDelivery, delivery_id)
    if delivery is None or delivery.status == WebhookStatus.DELIVERED.value:
        return True

    client = await db.get(ApiClient, delivery.client_id)
    secret = (client.webhook_secret if client else None) or settings.webhook_secret

    body = json.dumps(delivery.payload, default=_json_default, separators=(",", ":")).encode()
    signature, timestamp = sign_webhook(body, secret)

    delivery.attempts += 1
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"{settings.app_name}/webhook",
        "X-Webhook-Signature": signature,
        "X-Webhook-Timestamp": str(timestamp),
        "X-Webhook-Event": delivery.event,
        "X-Webhook-Delivery": str(delivery.id),
        "X-Webhook-Attempt": str(delivery.attempts),
    }

    try:
        async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as http:
            response = await http.post(delivery.url, content=body, headers=headers)
        delivery.response_status = response.status_code
        delivery.response_body = response.text[:MAX_RESPONSE_BODY]
        succeeded = 200 <= response.status_code < 300
        if not succeeded:
            delivery.last_error = f"HTTP {response.status_code}"
    except Exception as exc:
        succeeded = False
        delivery.last_error = str(exc)[:MAX_RESPONSE_BODY]
        log.warning("webhook_failed", delivery_id=str(delivery.id), error=str(exc))

    now = datetime.now(timezone.utc)
    if succeeded:
        delivery.status = WebhookStatus.DELIVERED.value
        delivery.delivered_at = now
        delivery.next_attempt_at = None
        log.info("webhook_delivered", delivery_id=str(delivery.id), attempts=delivery.attempts)
    elif delivery.attempts >= settings.webhook_max_attempts:
        delivery.status = WebhookStatus.FAILED.value
        delivery.next_attempt_at = None
        log.error("webhook_exhausted", delivery_id=str(delivery.id), attempts=delivery.attempts)
    else:
        delivery.status = WebhookStatus.PENDING.value
        delivery.next_attempt_at = now + _backoff_delay(delivery.attempts)

    await db.flush()
    return succeeded


async def pending_webhook_ids(db: AsyncSession, limit: int = 100) -> list[uuid.UUID]:
    """Yuborilishi kerak bo'lgan webhooklar (cron uchun)."""
    stmt = (
        select(WebhookDelivery.id)
        .where(
            WebhookDelivery.status == WebhookStatus.PENDING.value,
            WebhookDelivery.next_attempt_at <= datetime.now(timezone.utc),
        )
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())
