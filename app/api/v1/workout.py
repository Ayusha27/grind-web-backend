# app/api/v1/workout.py
from fastapi import APIRouter, Depends, Request

from app.api import openapi_ext
from app.api.deps import BodyParams, DbSession
from app.cache.rate_limit import limit_write
from app.cache.redis import cache_get_json, cache_set_json, workout_key
from app.core.compat import php_intval
from app.core.config import settings
from app.services import workout_service

router = APIRouter(tags=["workout"])


@router.get(
    "/workout",
    openapi_extra=openapi_ext.query(
        {"client_id": "Client id. Absent or empty resolves to 1; non-numeric resolves to 0."}
    ),
)
async def get_workout(request: Request, db: DbSession) -> dict:
    """PR-01..PR-05. Cached: this is the highest-traffic endpoint in the app.

    The cache key uses the RESOLVED client_id, so ?client_id=abc and
    ?client_id=0 share one entry (both resolve to 0 under PR-01) — which is
    correct, because they must return the same body.
    """
    raw = request.query_params.get("client_id")
    resolved = settings.LEGACY_DEFAULT_CLIENT_ID if raw is None or raw == "" else php_intval(raw)

    key = workout_key(resolved)
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    result = await workout_service.get_workout(db, raw)
    await cache_set_json(key, result, settings.CACHE_TTL_WORKOUT)
    return result


@router.post(
    "/workout/complete",
    dependencies=[Depends(limit_write)],
    openapi_extra=openapi_ext.body(
        {
            "exercise_id": "Exercise row id. Must resolve > 0.",
            "day_id": "Day row id. Must resolve > 0.",
            "user_email": "Client email. Must be non-empty after trim.",
        },
        required=["exercise_id", "day_id", "user_email"],
    ),
)
async def complete_workout(payload: BodyParams, db: DbSession) -> dict:
    """PR-06..PR-09."""
    return await workout_service.complete_workout(db, payload)


@router.post(
    "/workout/logs",
    dependencies=[Depends(limit_write)],
    openapi_extra=openapi_ext.body(
        {
            "email": "Client email. NOTE: this route uses email, not user_email.",
            "month": "Month number. Defaults to 1.",
            "week": "Week number. Defaults to 1.",
            "day": "Day row id. NOTE: day, not day_id. Defaults to 0.",
            "exercise": "Exercise row id. NOTE: exercise, not exercise_id. Defaults to 0.",
            "set": "Set number. Defaults to 1.",
            "completed": "Truthy value marks the set complete.",
        },
        required=["email"],
    ),
)
async def save_log(payload: BodyParams, db: DbSession) -> dict:
    """Repairs the dead save-progress.php."""
    return await workout_service.save_log(db, payload)
