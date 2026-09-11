"""Xavfsizlik/mantiq tuzatishlarini isbotlovchi regressiya testlari.

Har bir test aynan bitta tuzatilgan nuqsonga bog'langan — tuzatish qaytarib
olinsa, mos test yiqiladi.
"""

import asyncio

import pytest
from sqlalchemy import select

from app.core.security import hash_api_key
from app.core.urls import UnsafeUrlError, validate_webhook_url
from app.models.api_client import ApiClient
from app.models.enums import OrderStatus, OrderType
from app.models.order import Order
from app.services.orders import create_order, execute_order
from tests.conftest import TestSession


async def _client_row(session, api_key: str) -> ApiClient:
    stmt = select(ApiClient).where(ApiClient.key_hash == hash_api_key(api_key))
    return (await session.execute(stmt)).scalar_one()


async def _seed_order(api_key: str, *, username: str, **overrides):
    """Buyurtma yaratadi va (order_id, ) qaytaradi — sessiya darhol yopiladi."""
    async with TestSession() as session:
        client_obj = await _client_row(session, api_key)
        order, _ = await create_order(
            session,
            client=client_obj,
            order_type=OrderType.STARS,
            payload={"recipient_username": username, "quantity": 100},
        )
        order_id = order.id
        if overrides:
            for k, v in overrides.items():
                setattr(order, k, v)
            await session.commit()
    return order_id


# ---------------------------------------------------------------------------
#  SSRF: mijoz webhook_url'ni ichki manzilga yo'naltira olmasligi kerak
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8710/v1/admin/clients",    # ichki API
        "http://localhost/hook",
        "http://169.254.169.254/latest/meta-data/",  # bulut metadata
        "http://10.0.0.5/hook",
        "http://192.168.1.1/hook",
        "http://172.16.0.1/hook",
        "http://[::1]/hook",
        "http://0.0.0.0/hook",
    ],
)
def test_webhook_url_blocks_internal_targets(url):
    with pytest.raises(UnsafeUrlError):
        validate_webhook_url(url, allow_private=False)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "gopher://evil.com/", "ftp://evil.com/", "redis://127.0.0.1:6379"],
)
def test_webhook_url_blocks_non_http_schemes(url):
    with pytest.raises(UnsafeUrlError):
        validate_webhook_url(url, allow_private=False)


def test_webhook_url_blocks_credentials_in_url():
    with pytest.raises(UnsafeUrlError, match="login/parol"):
        validate_webhook_url("http://user:pass@example.com/hook", allow_private=True)


def test_webhook_url_blocks_unusual_ports():
    """5432/6379 kabi portlarni skanerlashning oldini oladi."""
    with pytest.raises(UnsafeUrlError, match="porti"):
        validate_webhook_url("http://example.com:5432/hook", allow_private=True)


def test_webhook_url_allows_normal_https():
    assert validate_webhook_url("https://example.com/hook", allow_private=True)
    assert validate_webhook_url("https://example.com:443/hook", allow_private=True)


async def test_order_api_rejects_ssrf_webhook_url(client, auth):
    r = await client.post(
        "/v1/orders/stars",
        headers=auth,
        json={
            "recipient_username": "@durov",
            "quantity": 100,
            "webhook_url": "http://169.254.169.254/latest/meta-data/",
        },
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
#  Ikki marta to'lov: bir buyurtma faqat BIR MARTA bajarilishi kerak
# ---------------------------------------------------------------------------


async def test_concurrent_execution_runs_provider_only_once(api_key, monkeypatch):
    """Compare-and-swap ikki workerning bir buyurtmani olishiga yo'l qo'ymaydi.

    Tuzatishdan oldin `execute_order` `db.get()` + tekshiruv + yozuv qilardi,
    PROCESSING esa terminal holat emas — shuning uchun ikkala worker ham
    tekshiruvdan o'tib, provayderni ikki marta chaqirardi va hamyondan ikki
    marta yechilardi.
    """
    order_id = await _seed_order(api_key, username="concurrent_test")

    calls: list = []
    from app.providers.mock import MockProvider

    original = MockProvider.buy_stars

    async def counting_buy_stars(self, order):
        calls.append(order.id)
        await asyncio.sleep(0.2)  # provayder chaqiruvi davom etayotgan oyna
        return await original(self, order)

    monkeypatch.setattr(MockProvider, "buy_stars", counting_buy_stars)

    async def run_worker():
        async with TestSession() as session:
            return await execute_order(session, order_id)

    await asyncio.gather(run_worker(), run_worker(), return_exceptions=True)

    assert len(calls) == 1, f"provayder {len(calls)} marta chaqirildi — IKKI MARTA TO'LOV!"

    async with TestSession() as session:
        final = await session.get(Order, order_id)
        assert final.status == OrderStatus.COMPLETED.value
        assert final.attempts == 1


async def test_execute_order_ignores_already_completed(api_key):
    """Yakuniy holatdagi buyurtma qayta bajarilmaydi."""
    order_id = await _seed_order(api_key, username="twice_test")

    async with TestSession() as s1:
        first = await execute_order(s1, order_id)
        assert first.status == OrderStatus.COMPLETED.value
        ref = first.provider_ref

    async with TestSession() as s2:
        second = await execute_order(s2, order_id)
        assert second.status == OrderStatus.COMPLETED.value
        assert second.attempts == 1, "attempts oshdi — qayta bajarishga urinildi"
        assert second.provider_ref == ref, "provider_ref o'zgardi — qayta to'lov!"


# ---------------------------------------------------------------------------
#  /retry — allaqachon to'langan buyurtmani qayta urinishga yo'l qo'ymaslik
# ---------------------------------------------------------------------------


async def test_retry_refuses_when_provider_already_paid(client, auth, api_key):
    """provider_ref bo'lsa, pul allaqachon ketgan — /retry rad etilishi shart."""
    # To'lov o'tgan, ammo javobni qayta ishlashda xatolik bo'lgan holat
    order_id = await _seed_order(
        api_key,
        username="paid_but_failed",
        status=OrderStatus.FAILED.value,
        provider_ref="tx_already_settled_on_chain",
    )

    r = await client.post(f"/v1/orders/{order_id}/retry", headers=auth)
    assert r.status_code == 409, r.text
    assert "allaqachon bajarilgan" in r.text


async def test_retry_rejects_non_failed_order(client, auth, api_key):
    order_id = await _seed_order(api_key, username="pending_order")
    r = await client.post(f"/v1/orders/{order_id}/retry", headers=auth)
    assert r.status_code == 409, r.text


async def test_retry_does_not_reset_attempts(client, auth, api_key):
    """attempts nolga tushmasligi kerak — aks holda limit mazmunini yo'qotadi."""
    order_id = await _seed_order(
        api_key, username="attempt_keeper", status=OrderStatus.FAILED.value, attempts=1
    )
    r = await client.post(f"/v1/orders/{order_id}/retry", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["attempts"] == 1, "attempts nolga tushirildi"


async def test_retry_blocked_when_attempts_exhausted(client, auth, api_key):
    order_id = await _seed_order(
        api_key, username="exhausted", status=OrderStatus.FAILED.value, attempts=99
    )
    r = await client.post(f"/v1/orders/{order_id}/retry", headers=auth)
    assert r.status_code == 409
    assert "limiti tugagan" in r.text


# ---------------------------------------------------------------------------
#  Health endpoint infratuzilma tafsilotlarini oshkor qilmasligi kerak
# ---------------------------------------------------------------------------


async def test_public_health_hides_error_details(client):
    r = await client.get("/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["database"] in ("ok", "error"), "xom exception matni qaytdi"
    assert data["redis"] in ("ok", "error")
    # Provayder holati faqat konfiguratsiya darajasida, jonli chaqiruvsiz
    assert set(data["providers"].values()) <= {"configured", "not_configured"}


async def test_full_health_requires_admin_key(client):
    r = await client.get("/v1/health/full")
    assert r.status_code == 403


async def test_full_health_works_with_admin_key(client, admin_auth):
    r = await client.get("/v1/health/full", headers=admin_auth)
    assert r.status_code == 200
    assert "database" in r.json()


# ---------------------------------------------------------------------------
#  Noto'g'ri provayder nomi admin API darajasida rad etiladi
# ---------------------------------------------------------------------------


async def test_admin_rejects_unknown_forced_provider(client, admin_auth):
    r = await client.post(
        "/v1/admin/clients",
        headers=admin_auth,
        json={"name": "Typo Client", "forced_provider": "fragmnet"},
    )
    assert r.status_code == 422, r.text
