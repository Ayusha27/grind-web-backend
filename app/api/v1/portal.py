# app/api/v1/portal.py
from fastapi import APIRouter

from app.api import openapi_ext
from app.api.deps import CurrentClient, DbSession
from app.cache.redis import cache_get_json, cache_set_json, portal_key
from app.core.config import settings
from app.services import portal_service

router = APIRouter(prefix="/portal", tags=["portal"])


@router.get(
    "/my-plan",
    openapi_extra=openapi_ext.query(
        {
            "token": "Client access token, e.g. GR_ALP_009001. May also be sent"
                     " as an Authorization: Bearer header."
        }
    ),
)
async def my_plan(client: CurrentClient, db: DbSession) -> dict:
    """my-plan.php. Cached per access token — this page is opened repeatedly
    during a workout and its contents change only when an admin republishes.
    """
    key = portal_key(client.access_token or str(client.id))
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    result = await portal_service.get_my_plan(db, client.access_token or "")
    await cache_set_json(key, result, settings.CACHE_TTL_WORKOUT)
    return result


@router.get(
    "/progress",
    openapi_extra=openapi_ext.query(
        {
            "token": "Client access token, e.g. GR_ALP_009001. May also be sent"
                     " as an Authorization: Bearer header."
        }
    ),
)
async def progress(client: CurrentClient, db: DbSession) -> dict:
    """workout-progress.php, PR-32. NOT cached: the completed-exercise count
    must move the instant a set is ticked, or the UI looks broken.
    """
    return await portal_service.get_progress(db, client.access_token or "")
