"""Test fixtures — alohida test DB ishlatiladi."""

import os

# Sozlamalar import qilinishidan OLDIN muhitni tayyorlaymiz
os.environ.setdefault("POSTGRES_DB", "stars_service_test")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("DEFAULT_PROVIDER", "mock")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("ENV", "development")

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import generate_api_key, hash_api_key, key_hint
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.api_client import ApiClient

# NullPool: ulanishlar hech qachon qayta ishlatilmaydi, shuning uchun har bir
# test o'z event-loop'ida muammosiz ishlaydi.
test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
async def _setup_database() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as session:
        yield session


@pytest.fixture
async def api_key(db: AsyncSession) -> str:
    key = generate_api_key(test=True)
    client = ApiClient(
        name="pytest-client",
        key_hash=hash_api_key(key),
        key_hint=key_hint(key),
    )
    db.add(client)
    await db.commit()
    return key


@pytest.fixture
async def client(monkeypatch) -> AsyncGenerator[AsyncClient, None]:
    """Redis/ARQ ga bog'liqlikni olib tashlagan test klienti."""

    async def _fake_enqueue(order_id, delay_seconds: int = 0):
        return f"test-job-{order_id}"

    monkeypatch.setattr("app.api.v1.orders.enqueue_order", _fake_enqueue)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def auth(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


@pytest.fixture
def admin_auth() -> dict[str, str]:
    return {"X-Admin-Key": settings.admin_api_key}
