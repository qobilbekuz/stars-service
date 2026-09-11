"""API mijozi — har bir integratsiya uchun bitta API-kalit."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class ApiClient(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "api_clients"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(40), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Webhook sozlamalari
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Shu mijoz uchun majburiy provayder (None -> DEFAULT_PROVIDER)
    forced_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)

    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ApiClient {self.name} {self.key_hint}>"
