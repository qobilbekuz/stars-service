from fastapi import APIRouter

from app.api.v1 import admin, catalog, health, orders

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(orders.router)
api_router.include_router(catalog.router)
api_router.include_router(admin.router)
