from enum import Enum


class StrEnum(str, Enum):
    """Python 3.10 mosligi uchun (3.11+ dagi enum.StrEnum o'rniga)."""

    def __str__(self) -> str:  # pragma: no cover
        return str(self.value)


class OrderType(StrEnum):
    STARS = "stars"  # Telegram Stars sotib olish
    PREMIUM = "premium"  # Telegram Premium obunasi (3/6/12 oy)
    GIFT = "gift"  # Telegram sovg'asi (gift_id bo'yicha)


class OrderStatus(StrEnum):
    PENDING = "pending"  # yaratildi, hali navbatga qo'yilmagan
    QUEUED = "queued"  # navbatda
    PROCESSING = "processing"  # provayder bajarayapti
    COMPLETED = "completed"  # muvaffaqiyatli
    FAILED = "failed"  # yakuniy muvaffaqiyatsizlik
    CANCELLED = "cancelled"  # bekor qilindi
    REFUNDED = "refunded"  # pul qaytarildi

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset(
    {
        OrderStatus.COMPLETED,
        OrderStatus.FAILED,
        OrderStatus.CANCELLED,
        OrderStatus.REFUNDED,
    }
)


class ProviderName(StrEnum):
    MOCK = "mock"
    FRAGMENT = "fragment"
    BOTAPI = "botapi"


class WebhookStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
