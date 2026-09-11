"""FastAPI dependency'lari: autentifikatsiya, rate-limit, DB sessiyasi."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthenticationError, ForbiddenError
from app.core.security import hash_api_key
from app.db.session import get_db
from app.models.api_client import ApiClient
from app.services.rate_limit import enforce_rate_limit

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_client(
    db: DbSession,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> ApiClient:
    """API-kalit bo'yicha mijozni aniqlaydi va rate-limit qo'llaydi."""
    if not x_api_key:
        raise AuthenticationError("X-API-Key sarlavhasi majburiy")

    stmt = select(ApiClient).where(ApiClient.key_hash == hash_api_key(x_api_key))
    client = (await db.execute(stmt)).scalar_one_or_none()

    if client is None:
        raise AuthenticationError("API kalit noto'g'ri")
    if not client.is_active:
        raise ForbiddenError("API kalit faolsizlantirilgan")

    await enforce_rate_limit(str(client.id), client.rate_limit_per_minute)

    client.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    return client


CurrentClient = Annotated[ApiClient, Depends(get_current_client)]


async def require_admin(
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    """Admin endpointlari uchun statik kalit tekshiruvi."""
    import hmac

    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_api_key):
        raise ForbiddenError("Admin kalit noto'g'ri yoki berilmagan")


AdminGuard = Depends(require_admin)
