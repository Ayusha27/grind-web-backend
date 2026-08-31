# app/cache/redis.py
import json
import logging
from typing import Any

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: redis.ConnectionPool | None = None
_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _pool, _client
    if _client is None:
        _pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,           # never let Redis stall a request
            health_check_interval=30,
        )
        _client = redis.Redis(connection_pool=_pool)
    return _client


async def close_redis() -> None:
    global _client, _pool
    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def cache_get_json(key: str) -> Any | None:
    """A cache failure must NEVER fail a request — it degrades to a DB read."""
    if not settings.CACHE_ENABLED:
        return None
    try:
        raw = await get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception:
        logger.warning("cache_get_failed", extra={"key": key})
        return None


async def cache_set_json(key: str, value: Any, ttl: int) -> None:
    if not settings.CACHE_ENABLED:
        return
    try:
        await get_redis().setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        logger.warning("cache_set_failed", extra={"key": key})


async def cache_delete(*keys: str) -> None:
    if not settings.CACHE_ENABLED or not keys:
        return
    try:
        await get_redis().delete(*keys)
    except Exception:
        logger.warning("cache_delete_failed", extra={"keys": list(keys)})


# ── domain keys ────────────────────────────────────────────────────

def workout_key(client_id: int) -> str:
    # The version prefix lets you invalidate the whole namespace by bumping
    # it, without a SCAN over production Redis.
    return f"grind:v1:workout:{client_id}"


def portal_key(token: str) -> str:
    return f"grind:v1:portal:{token}"


EXERCISE_COUNT_KEY = "grind:v1:exercise_count"


async def cached_exercise_count(db: AsyncSession) -> int:
    """PR-28's global COUNT(*), memoised for CACHE_TTL_EXERCISE_COUNT.

    This is a sequential scan over workout_exercises that the legacy ran on
    EVERY portal load. It only changes when an admin imports a plan, so a
    300 s TTL is generous. Caching it is what makes keeping the bug free.
    """
    from app.repositories import workout_repo

    cached = await cache_get_json(EXERCISE_COUNT_KEY)
    if isinstance(cached, int):
        return cached

    count = await workout_repo.count_all_exercises(db)
    await cache_set_json(EXERCISE_COUNT_KEY, count, settings.CACHE_TTL_EXERCISE_COUNT)
    return count


async def invalidate_client_caches(client_id: int, access_token: str | None = None) -> None:
    """Called after any admin write that changes what a client sees.

    Also drops the global exercise count, because importing a plan changes
    PR-28's denominator for EVERY client.
    """
    keys = [workout_key(client_id), EXERCISE_COUNT_KEY]
    if access_token:
        keys.append(portal_key(access_token))
    await cache_delete(*keys)
