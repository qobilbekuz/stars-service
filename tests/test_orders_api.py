"""Buyurtma API va bajarilish oqimi testlari (mock provayder)."""

import uuid

import pytest
from httpx import AsyncClient

from app.models.enums import OrderStatus
from app.services.orders import execute_order
from tests.conftest import TestSession


async def test_health_is_public(client: AsyncClient):
    response = await client.get("/v1/health/live")
    assert response.status_code == 200


async def test_orders_require_api_key(client: AsyncClient):
    response = await client.post(
        "/v1/orders/stars", json={"recipient_username": "@durov", "quantity": 100}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_invalid_api_key_rejected(client: AsyncClient):
    response = await client.post(
        "/v1/orders/stars",
        json={"recipient_username": "@durov", "quantity": 100},
        headers={"X-API-Key": "sk_live_nonexistent"},
    )
    assert response.status_code == 401


async def test_create_stars_order(client: AsyncClient, auth: dict):
    response = await client.post(
        "/v1/orders/stars",
        json={"recipient_username": "@durov", "quantity": 100},
        headers=auth,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["type"] == "stars"
    assert body["status"] == "queued"
    assert body["quantity"] == 100
    assert body["recipient_username"] == "durov"
    assert body["provider"] == "mock"


async def test_idempotency_returns_same_order(client: AsyncClient, auth: dict):
    key = f"idem-{uuid.uuid4()}"
    payload = {"recipient_username": "@durov", "quantity": 250}
    headers = {**auth, "Idempotency-Key": key}

    first = await client.post("/v1/orders/stars", json=payload, headers=headers)
    second = await client.post("/v1/orders/stars", json=payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.headers.get("Idempotent-Replay") == "true"
    assert first.json()["id"] == second.json()["id"]


async def test_validation_error_shape(client: AsyncClient, auth: dict):
    response = await client.post(
        "/v1/orders/stars", json={"recipient_username": "@durov", "quantity": 1}, headers=auth
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_botapi_rejects_stars_orders(client: AsyncClient, auth: dict):
    response = await client.post(
        "/v1/orders/stars",
        json={"recipient_username": "@durov", "quantity": 100, "provider": "botapi"},
        headers=auth,
    )
    assert response.status_code == 422
    assert "botapi" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    "username,expected_status",
    [
        ("durov", OrderStatus.COMPLETED.value),
        ("fail_user", OrderStatus.FAILED.value),  # mock: doimiy xatolik
        ("retry_user", OrderStatus.PENDING.value),  # mock: retry rejalashtiriladi
    ],
)
async def test_order_execution_outcomes(client: AsyncClient, auth: dict, username, expected_status):
    created = await client.post(
        "/v1/orders/stars", json={"recipient_username": username, "quantity": 100}, headers=auth
    )
    order_id = uuid.UUID(created.json()["id"])

    async with TestSession() as db:
        order = await execute_order(db, order_id)

    assert order.status == expected_status
    if expected_status == OrderStatus.COMPLETED.value:
        assert order.provider_ref.startswith("mock_stars_")
        assert order.amount is not None
        assert order.currency == "TON"
    if expected_status == OrderStatus.FAILED.value:
        assert order.error_code == "provider_permanent_error"


async def test_completed_order_is_readable_with_events(client: AsyncClient, auth: dict):
    created = await client.post(
        "/v1/orders/premium", json={"recipient_username": "@durov", "months": 3}, headers=auth
    )
    order_id = created.json()["id"]

    async with TestSession() as db:
        await execute_order(db, uuid.UUID(order_id))

    detail = await client.get(f"/v1/orders/{order_id}", headers=auth)
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "completed"
    statuses = [e["to_status"] for e in body["events"]]
    assert statuses == ["pending", "queued", "processing", "completed"]


async def test_order_isolation_between_clients(client: AsyncClient, auth: dict, admin_auth: dict):
    created = await client.post(
        "/v1/orders/stars", json={"recipient_username": "@durov", "quantity": 100}, headers=auth
    )
    order_id = created.json()["id"]

    other = await client.post(
        "/v1/admin/clients", json={"name": "boshqa mijoz", "test_key": True}, headers=admin_auth
    )
    other_key = other.json()["api_key"]

    response = await client.get(f"/v1/orders/{order_id}", headers={"X-API-Key": other_key})
    assert response.status_code == 404


async def test_cancel_and_retry_flow(client: AsyncClient, auth: dict):
    created = await client.post(
        "/v1/orders/stars", json={"recipient_username": "@durov", "quantity": 100}, headers=auth
    )
    order_id = created.json()["id"]

    cancelled = await client.post(f"/v1/orders/{order_id}/cancel", headers=auth)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # Ikkinchi marta bekor qilib bo'lmaydi
    again = await client.post(f"/v1/orders/{order_id}/cancel", headers=auth)
    assert again.status_code == 409

    # Retry faqat 'failed' uchun
    retried = await client.post(f"/v1/orders/{order_id}/retry", headers=auth)
    assert retried.status_code == 409


async def test_list_orders_with_filters(client: AsyncClient, auth: dict):
    await client.post(
        "/v1/orders/stars", json={"recipient_username": "@filtered", "quantity": 500}, headers=auth
    )
    response = await client.get(
        "/v1/orders", params={"type": "stars", "recipient_username": "filtered"}, headers=auth
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(o["type"] == "stars" for o in body["items"])
