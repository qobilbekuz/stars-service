"""Redis asosidagi sliding-window rate limiter."""

import secrets
import time

from app.core.config import settings
from app.core.exceptions import RateLimitError
from app.core.redis import get_redis

# Atomik: window ichidagi eskilarni tozalab, yangisini qo'shib, sanaydi
_LUA_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    return {1, count}
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window))
return {0, count + 1}
"""


async def enforce_rate_limit(identifier: str, limit: int | None = None) -> None:
    """Limit oshib ketsa RateLimitError ko'taradi."""
    if not settings.rate_limit_enabled:
        return

    effective_limit = limit or settings.rate_limit_per_minute
    redis = get_redis()
    now = time.time()
    key = f"ratelimit:{identifier}"

    # ZSET a'zosi noyob bo'lishi SHART. Ilgari bu `f"{now}:{id(now)}"` edi —
    # id() bu vaqtinchalik float obyektining manzili va CPython bo'shagan
    # slotlarni qayta ishlatgani uchun ketma-ket chaqiruvlar bir xil manzilni
    # olishi mumkin. Bir xil a'zo ZADD'da yangi element qo'shmay, mavjudining
    # ballini yangilaydi — ya'ni so'rov hisobga olinmay qolardi.
    member = f"{now}:{secrets.token_hex(8)}"

    blocked, count = await redis.eval(
        _LUA_SLIDING_WINDOW, 1, key, str(now), "60", str(effective_limit), member
    )

    if int(blocked) == 1:
        raise RateLimitError(
            f"So'rovlar limiti oshdi: daqiqasiga {effective_limit} ta",
            details={"limit": effective_limit, "current": int(count), "window_seconds": 60},
        )
