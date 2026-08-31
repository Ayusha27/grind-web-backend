# app/cache/rate_limit.py
import logging

from fastapi import Request

from app.cache.redis import get_redis
from app.core.config import settings
from app.core.exceptions import RateLimitError

logger = logging.getLogger(__name__)

# Fixed-window counter. Atomic: INCR and EXPIRE cannot be split.
_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


def client_ip(request: Request) -> str:
    """Trust X-Forwarded-For only because Nginx sets it (Phase 9.3) and
    uvicorn runs with --proxy-headers. Taking the FIRST entry is correct
    when your own proxy appends; never trust it on a directly exposed app.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Dependency factory:  Depends(RateLimiter("login", times=5, seconds=300))"""

    def __init__(self, name: str, *, times: int, seconds: int) -> None:
        self.name = name
        self.times = times
        self.seconds = seconds

    async def __call__(self, request: Request) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return

        key = f"grind:v1:rl:{self.name}:{client_ip(request)}"
        try:
            count = await get_redis().eval(_SCRIPT, 1, key, self.seconds)
        except Exception:
            # Fail OPEN. A Redis outage must not take down checkout. The
            # alternative (fail closed) turns a cache incident into a
            # revenue incident.
            logger.warning("rate_limit_unavailable", extra={"bucket": self.name})
            return

        if int(count) > self.times:
            logger.warning(
                "rate_limited",
                extra={"bucket": self.name, "ip": client_ip(request), "count": count},
            )
            raise RateLimitError("Too many requests. Please slow down.")


# Budgets, each justified by what the endpoint costs or protects:
limit_login = RateLimiter("login", times=5, seconds=300)        # brute force
limit_payment = RateLimiter("payment", times=20, seconds=60)    # paid external call
limit_coupon = RateLimiter("coupon", times=30, seconds=60)      # code enumeration
limit_intake = RateLimiter("intake", times=5, seconds=600)      # spam via SMTP
limit_write = RateLimiter("write", times=120, seconds=60)       # set-ticking is bursty
