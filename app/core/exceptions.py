"""Domen darajasidagi xatoliklar."""

from typing import Any


class ServiceError(Exception):
    """Barcha servis xatoliklarining bazasi."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class NotFoundError(ServiceError):
    status_code = 404
    code = "not_found"


class ValidationError(ServiceError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(ServiceError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(ServiceError):
    status_code = 403
    code = "forbidden"


class ConflictError(ServiceError):
    status_code = 409
    code = "conflict"


class RateLimitError(ServiceError):
    status_code = 429
    code = "rate_limited"


class InsufficientFundsError(ServiceError):
    status_code = 402
    code = "insufficient_funds"


# --- Provayder xatoliklari ---


class ProviderError(ServiceError):
    """Provayder tomonidagi umumiy xatolik."""

    status_code = 502
    code = "provider_error"
    retryable: bool = False


class ProviderTemporaryError(ProviderError):
    """Vaqtinchalik xatolik — qayta urinish mumkin (timeout, 5xx, network)."""

    code = "provider_temporary_error"
    retryable = True


class ProviderPermanentError(ProviderError):
    """Qayta urinish foydasiz (noto'g'ri username, mablag' yetmasligi, ban)."""

    code = "provider_permanent_error"
    retryable = False


class ProviderNotConfiguredError(ProviderError):
    status_code = 503
    code = "provider_not_configured"
    retryable = False
