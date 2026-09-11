"""FastAPI ilovasi — kirish nuqtasi.

Ishga tushirish:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import ServiceError
from app.core.logging import get_logger, setup_logging
from app.core.redis import close_redis, get_redis
from app.db.session import engine
from app.providers.registry import close_providers
from app.workers.queue import close_arq_pool, get_arq_pool

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log.info("starting", app=settings.app_name, version=__version__, env=settings.env)

    try:
        await get_redis().ping()
        await get_arq_pool()
    except Exception as exc:
        log.error("redis_unavailable", error=str(exc))

    yield

    await close_arq_pool()
    await close_providers()
    await close_redis()
    await engine.dispose()
    log.info("stopped")


app = FastAPI(
    title="Stars Service API",
    description=(
        "Telegram **Stars**, **Premium** va **Gift** xaridlarini avtomatlashtirish servisi.\n\n"
        "### Autentifikatsiya\n"
        "Barcha endpointlar `X-API-Key` sarlavhasini talab qiladi.\n"
        "Admin endpointlari qo'shimcha `X-Admin-Key` talab qiladi.\n\n"
        "### Idempotentlik\n"
        "Buyurtma yaratishda `Idempotency-Key` sarlavhasini yuboring — takroriy "
        "so'rov yangi buyurtma yaratmaydi, mavjudini qaytaradi.\n\n"
        "### Asinxronlik\n"
        "Buyurtmalar `202 Accepted` bilan qabul qilinadi va fonda bajariladi. "
        "Natijani `GET /v1/orders/{id}` yoki webhook orqali oling."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Har bir so'rovga request_id biriktiradi va davomiyligini loglaydi."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)

    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = str(duration_ms)

    if not request.url.path.startswith("/health"):
        log.info(
            "request",
            method=request.method,
            status=response.status_code,
            duration_ms=duration_ms,
        )
    structlog.contextvars.clear_contextvars()
    return response


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "So'rov ma'lumotlari noto'g'ri",
                "details": {"fields": jsonable_errors(exc)},
            }
        },
    )


def jsonable_errors(exc: RequestValidationError) -> list[dict]:
    return [
        {
            "field": ".".join(str(p) for p in err.get("loc", [])[1:]),
            "message": err.get("msg"),
            "type": err.get("type"),
        }
        for err in exc.errors()
    ]


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", path=request.url.path)
    message = str(exc) if settings.debug else "Ichki server xatoligi"
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": message, "details": {}}},
    )


app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "health": f"{settings.api_prefix}/health",
    }
