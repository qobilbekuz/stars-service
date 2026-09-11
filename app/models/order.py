"""Buyurtma va uning hodisalar tarixi."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import OrderStatus


class Order(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        # Bir mijoz doirasida idempotency kaliti yagona bo'lishi shart
        UniqueConstraint("client_id", "idempotency_key", name="uq_orders_client_idempotency"),
        Index("ix_orders_client_status", "client_id", "status"),
        Index("ix_orders_status_created", "status", "created_at"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("api_clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # --- Buyurtma tavsifi ---
    type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OrderStatus.PENDING.value, index=True
    )

    # Qabul qiluvchi: username yoki telegram user id (kamida bittasi)
    recipient_username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    recipient_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Tur bo'yicha parametrlar
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)  # stars soni
    months: Mapped[int | None] = mapped_column(Integer, nullable=True)  # premium oylar
    gift_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # gift identifikatori
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # sovg'a matni

    # --- Bajarilish ---
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Narx (ixtiyoriy, hisob-kitob uchun) ---
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 9), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)

    extra_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list["OrderEvent"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderEvent.created_at",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Order {self.id} {self.type} {self.status}>"


class OrderEvent(Base, UUIDMixin):
    """Status o'zgarishlari audit izi."""

    __tablename__ = "order_events"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True, server_default=func.now()
    )

    order: Mapped[Order] = relationship(back_populates="events")
