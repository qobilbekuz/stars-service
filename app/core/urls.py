"""Mijoz bergan URL'larni SSRF'ga qarshi tekshirish.

Webhook manzilini mijoz o'zi beradi va uni SERVER chaqiradi. Tekshiruvsiz
har qanday API-kalit egasi serverni ichki tarmoqqa yo'naltira olardi:

    http://127.0.0.1:8710/v1/admin/...     -> ichki admin API
    http://169.254.169.254/latest/meta-data -> bulut kredensiallari
    http://10.0.0.5:5432/                   -> ichki xizmatlarni skanerlash

Javob tanasi `webhook_deliveries.response_body` ga yozilgani uchun bu ko'r
SSRF emas — natijani mijoz o'qiy oladi. Shuning uchun qat'iy bloklaymiz.
"""

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}

# Faqat shu portlar (odatiy HTTP xizmatlari)
ALLOWED_PORTS = {80, 443, 8080, 8443}


class UnsafeUrlError(ValueError):
    """URL SSRF nuqtai nazaridan xavfli."""


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local  # 169.254.x.x — bulut metadata
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_webhook_url(raw: str, *, allow_private: bool = False) -> str:
    """URL xavfsizligini tekshiradi. Xavfli bo'lsa UnsafeUrlError.

    `allow_private=True` — faqat testlar/dev uchun (localhost listener).
    """
    if not raw or len(raw) > 2048:
        raise UnsafeUrlError("webhook_url bo'sh yoki juda uzun")

    parsed = urlparse(raw.strip())

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"webhook_url sxemasi '{parsed.scheme}' qo'llab-quvvatlanmaydi "
            f"(faqat: {', '.join(sorted(ALLOWED_SCHEMES))})"
        )

    if not parsed.hostname:
        raise UnsafeUrlError("webhook_url da host ko'rsatilmagan")

    # userinfo (http://user:pass@host) parser'larni chalg'itish uchun ishlatiladi
    if parsed.username or parsed.password:
        raise UnsafeUrlError("webhook_url da login/parol bo'lishi mumkin emas")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise UnsafeUrlError(
            f"webhook_url porti {port} ruxsat etilmagan "
            f"(faqat: {', '.join(map(str, sorted(ALLOWED_PORTS)))})"
        )

    if allow_private:
        return raw.strip()

    # Hostni IP'ga aylantirib tekshiramiz — DNS orqali ichki manzilga
    # yo'naltirishning oldini oladi (masalan evil.com -> 127.0.0.1).
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"webhook_url hosti aniqlanmadi: {parsed.hostname}") from exc

    resolved = {info[4][0] for info in infos}
    if not resolved:
        raise UnsafeUrlError(f"webhook_url hosti aniqlanmadi: {parsed.hostname}")

    for ip in resolved:
        if not _is_public_ip(ip):
            raise UnsafeUrlError(
                f"webhook_url ichki manzilga ishora qilyapti ({parsed.hostname} -> {ip}). "
                "Faqat ommaviy manzillar ruxsat etiladi."
            )

    return raw.strip()
