"""Admin endpointlari va katalog testlari."""

from httpx import AsyncClient


async def test_admin_requires_key(client: AsyncClient):
    response = await client.get("/v1/admin/clients")
    assert response.status_code == 403


async def test_admin_rejects_wrong_key(client: AsyncClient):
    response = await client.get("/v1/admin/clients", headers={"X-Admin-Key": "noto'g'ri"})
    assert response.status_code == 403


async def test_create_client_returns_key_once(client: AsyncClient, admin_auth: dict):
    response = await client.post(
        "/v1/admin/clients",
        json={"name": "yangi mijoz", "webhook_url": "https://example.com/hook"},
        headers=admin_auth,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["api_key"].startswith("sk_live_")
    assert body["webhook_secret"].startswith("whsec_")

    # Ro'yxatda kalit ko'rinmaydi — faqat hint
    listing = await client.get("/v1/admin/clients", headers=admin_auth)
    entry = next(c for c in listing.json() if c["id"] == body["id"])
    assert "api_key" not in entry
    assert entry["key_hint"].endswith(body["api_key"][-4:])


async def test_created_client_can_authenticate(client: AsyncClient, admin_auth: dict):
    created = await client.post("/v1/admin/clients", json={"name": "auth-test"}, headers=admin_auth)
    key = created.json()["api_key"]

    response = await client.get("/v1/limits", headers={"X-API-Key": key})
    assert response.status_code == 200


async def test_deactivated_client_is_blocked(client: AsyncClient, admin_auth: dict):
    created = await client.post(
        "/v1/admin/clients", json={"name": "bloklanadi"}, headers=admin_auth
    )
    body = created.json()

    await client.patch(
        f"/v1/admin/clients/{body['id']}", json={"is_active": False}, headers=admin_auth
    )
    response = await client.get("/v1/limits", headers={"X-API-Key": body["api_key"]})
    assert response.status_code == 403


async def test_providers_and_limits(client: AsyncClient, auth: dict):
    providers = await client.get("/v1/providers", headers=auth)
    assert providers.status_code == 200
    names = {p["name"] for p in providers.json()}
    assert names == {"mock", "fragment", "botapi"}

    mock_entry = next(p for p in providers.json() if p["name"] == "mock")
    assert mock_entry["configured"] is True

    limits = await client.get("/v1/limits", headers=auth)
    assert limits.json()["allowed_premium_months"] == [3, 6, 12]


async def test_gift_catalog(client: AsyncClient, auth: dict):
    response = await client.get("/v1/catalog/gifts", headers=auth)
    assert response.status_code == 200
    gifts = response.json()
    assert len(gifts) > 0
    assert all("star_count" in g for g in gifts)


async def test_admin_stats(client: AsyncClient, admin_auth: dict):
    response = await client.get("/v1/admin/stats", headers=admin_auth)
    assert response.status_code == 200
    assert "orders_by_status" in response.json()
