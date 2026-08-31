# app/api/legacy.py
"""Deprecated `.php` aliases.

Every route here delegates to the same service as its /api/v1 counterpart —
there is no duplicated logic. Delete this module once the frontend and any
external callers have moved, tracked by the deprecation metric below.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response

from app.api.deps import BodyParams, DbSession
from app.cache.rate_limit import limit_coupon, limit_payment, limit_write
from app.cache.redis import cache_get_json, cache_set_json, workout_key
from app.core.compat import php_intval
from app.core.config import settings
from app.integrations.mailer import send_intake_email
from app.services import affiliate_service, intake_service, payment_service, workout_service

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)  # keep the OpenAPI page clean


def _deprecated(response: Response, replacement: str, path: str) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f'<{replacement}>; rel="successor-version"'
    # Grep this in the logs to know when it is safe to delete the module.
    logger.info("legacy_path_used", extra={"legacy_path": path, "replacement": replacement})


@router.get("/api/workout.php")
async def legacy_workout(request: Request, response: Response, db: DbSession) -> dict:
    _deprecated(response, "/api/v1/workout", "/api/workout.php")
    raw = request.query_params.get("client_id")
    resolved = settings.LEGACY_DEFAULT_CLIENT_ID if raw in (None, "") else php_intval(raw)

    key = workout_key(resolved)
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    result = await workout_service.get_workout(db, raw)
    await cache_set_json(key, result, settings.CACHE_TTL_WORKOUT)
    return result


@router.post("/api/complete-workout.php", dependencies=[Depends(limit_write)])
async def legacy_complete(response: Response, payload: BodyParams, db: DbSession) -> dict:
    _deprecated(response, "/api/v1/workout/complete", "/api/complete-workout.php")
    return await workout_service.complete_workout(db, payload)


@router.post("/api/validate_affiliate.php", dependencies=[Depends(limit_coupon)])
async def legacy_validate(response: Response, payload: BodyParams, db: DbSession) -> dict:
    _deprecated(response, "/api/v1/affiliate/validate", "/api/validate_affiliate.php")
    return await affiliate_service.validate_code(db, payload.get("code"))


@router.post("/Payment/create_order.php", dependencies=[Depends(limit_payment)])
async def legacy_create_order(
    response: Response, payload: BodyParams, db: DbSession
) -> dict:
    _deprecated(response, "/api/v1/payments/order", "/Payment/create_order.php")
    return await payment_service.create_order(
        db,
        raw_plan=payload.get("plan"),
        raw_price=payload.get("price"),
        raw_coupon=payload.get("coupon"),
    )


@router.post("/Payment/verify_payment.php", dependencies=[Depends(limit_payment)])
async def legacy_verify(response: Response, payload: BodyParams, db: DbSession) -> dict:
    _deprecated(response, "/api/v1/payments/verify", "/Payment/verify_payment.php")
    return await payment_service.verify_payment(db, payload)


@router.post("/save-progress.php", dependencies=[Depends(limit_write)])
async def legacy_save_progress(
    response: Response, payload: BodyParams, db: DbSession
) -> dict:
    _deprecated(response, "/api/v1/workout/logs", "/save-progress.php")
    return await workout_service.save_log(db, payload)


@router.post("/start-your-journey.php")
async def legacy_intake(
    response: Response, payload: BodyParams, background: BackgroundTasks
) -> dict:
    _deprecated(response, "/api/v1/intake", "/start-your-journey.php")
    subject, body, reply_to = intake_service.build_intake_email(payload)
    background.add_task(send_intake_email, subject, body, reply_to)
    return {"success": True, "message": "Submission received"}
