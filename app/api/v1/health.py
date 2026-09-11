"""Sog'liq tekshiruvi.

Ikki daraja:
  * GET /health       — ommaviy, MINIMAL. Faqat ok/degraded.
  * GET /health/full  — X-Admin-Key talab qiladi, to'liq diagnostika.

Nima uchun ajratildi: ilgari yagona ommaviy endpoint xom exception matnini
qaytarardi (asyncpg xatoliklari host/port/user/dbname'ni o'z ichiga oladi) va
har chaqiruvda Telegram'ga jonli `getMe` yuborardi — ya'ni autentifikatsiyasiz
foydalanuvchi infratuzilma tafsilotlarini yig'a olardi va botning API limitini
yoqib yubora olardi.
"""


from fastapi import APIRouter, Depends
from sqlalchemy import text

from app import __version__
from app.api.deps import require_admin
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.session import engine
from app.providers import provider_health, registered_providers
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])
log = get_logger(__name__)


async def _check_database() -> tuple[bool, str]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        log.error("health_db_failed", error=str(exc))
        return False, f"error: {exc}"


async def _check_redis() -> tuple[bool, str]:
    try:
        await get_redis().ping()
        return True, "ok"
    except Exception as exc:
        log.error("health_redis_failed", error=str(exc))
        return False, f"error: {exc}"


@router.get("/health", response_model=HealthResponse, summary="Servis holati (ommaviy)")
async def health() -> HealthResponse:
    """Yuk balanslagichlar uchun. Xatolik tafsilotlari OSHKOR QILINMAYDI."""
    db_ok, _ = await _check_database()
    redis_ok, _ = await _check_redis()

    # Provayderlar uchun jonli tarmoq chaqiruvi QILINMAYDI — faqat
    # konfiguratsiya holati (mahalliy tekshiruv, tashqi so'rovsiz).
    providers = {
        name: ("configured" if p.is_configured else "not_configured")
        for name, p in registered_providers().items()
    }

    return HealthResponse(
        status="ok" if (db_ok and redis_ok) else "degraded",
        version=__version__,
        env=settings.env,
        database="ok" if db_ok else "error",
        redis="ok" if redis_ok else "error",
        providers=providers,
    )


@router.get(
    "/health/full",
    dependencies=[Depends(require_admin)],
    summary="To'liq diagnostika (X-Admin-Key)",
)
async def health_full() -> dict:
    """Xatolik matnlari va jonli provayder tekshiruvi — faqat admin uchun."""
    db_ok, db_detail = await _check_database()
    redis_ok, redis_detail = await _check_redis()
    providers = await provider_health()  # jonli chaqiruv (getMe / ping)

    return {
        "status": "ok" if (db_ok and redis_ok) else "degraded",
        "version": __version__,
        "env": settings.env,
        "database": db_detail,
        "redis": redis_detail,
        "providers": providers,
    }


@router.get("/health/live", summary="Liveness probe")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}
