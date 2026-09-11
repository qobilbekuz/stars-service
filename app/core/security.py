"""API-kalit generatsiyasi/hashlash va webhook HMAC imzolash."""

import hashlib
import hmac
import secrets
import time

API_KEY_PREFIX = "sk_live_"
API_KEY_PREFIX_TEST = "sk_test_"


def generate_api_key(test: bool = False) -> str:
    """Yangi ochiq API-kalit yaratadi. Faqat bir marta ko'rsatiladi."""
    prefix = API_KEY_PREFIX_TEST if test else API_KEY_PREFIX
    return f"{prefix}{secrets.token_urlsafe(32)}"


def hash_api_key(api_key: str) -> str:
    """Kalitni DB'da saqlash uchun sha256 hash."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def verify_api_key(api_key: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(api_key), key_hash)


def key_hint(api_key: str) -> str:
    """UI'da ko'rsatish uchun: sk_live_...a1b2"""
    return f"{api_key[:11]}...{api_key[-4:]}"


def generate_webhook_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"


def sign_webhook(payload: bytes, secret: str, timestamp: int | None = None) -> tuple[str, int]:
    """Stripe uslubidagi imzo. Qaytadi: (signature_header, timestamp).

    Header formati:  t=<unix_ts>,v1=<hex_hmac_sha256>
    Imzolanadigan matn: f"{timestamp}.{payload}"
    """
    ts = timestamp or int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}", ts


def verify_webhook_signature(
    payload: bytes, header: str, secret: str, tolerance_seconds: int = 300
) -> bool:
    """Mijoz tomonida webhookni tekshirish uchun yordamchi (testlarda ham ishlatiladi)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
        received = parts["v1"]
    except (ValueError, KeyError):
        return False

    if abs(int(time.time()) - ts) > tolerance_seconds:
        return False

    expected, _ = sign_webhook(payload, secret, timestamp=ts)
    expected_sig = expected.split("v1=")[1]
    return hmac.compare_digest(expected_sig, received)
