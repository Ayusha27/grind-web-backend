# app/api/v1/affiliate.py
from fastapi import APIRouter, Depends

from app.api import openapi_ext
from app.api.deps import AdminUser, BodyParams, DbSession
from app.cache.rate_limit import limit_coupon
from app.services import affiliate_service

router = APIRouter(tags=["affiliate"])


@router.post(
    "/affiliate/validate",
    dependencies=[Depends(limit_coupon)],
    openapi_extra=openapi_ext.body({"code": "Coupon code, e.g. DUMMY20"}),
)
async def validate_affiliate(payload: BodyParams, db: DbSession) -> dict:
    """PR-10, PR-11. Rate-limited because it is a coupon-enumeration oracle."""
    return await affiliate_service.validate_code(db, payload.get("code"))


@router.get("/admin/affiliates")
async def affiliate_dashboard(db: DbSession, _: AdminUser) -> dict:
    """PR-12. Decision 3: this was PUBLIC in the legacy system and exposed
    affiliate names, emails, revenue and commission. Now admin-only.
    """
    return await affiliate_service.get_dashboard(db)
