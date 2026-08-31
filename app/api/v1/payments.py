# app/api/v1/payments.py
from fastapi import APIRouter, Depends

from app.api import openapi_ext
from app.api.deps import BodyParams, DbSession
from app.cache.rate_limit import limit_payment
from app.services import payment_service

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post(
    "/order",
    dependencies=[Depends(limit_payment)],
    openapi_extra=openapi_ext.body(
        {
            "plan": "Plan name. Discounts apply only to the three eligible plans.",
            "price": "Original price. PHP floatval semantics.",
            "coupon": "Coupon code. NOTE: this route uses coupon, not code or coupon_code.",
        }
    ),
)
async def create_order(payload: BodyParams, db: DbSession) -> dict:
    """PR-13..PR-18."""
    return await payment_service.create_order(
        db,
        raw_plan=payload.get("plan"),
        raw_price=payload.get("price"),
        raw_coupon=payload.get("coupon"),
    )


@router.post(
    "/verify",
    dependencies=[Depends(limit_payment)],
    openapi_extra=openapi_ext.body(
        {
            "razorpay_order_id": "Order id returned by /payments/order.",
            "razorpay_payment_id": "Payment id from the Razorpay checkout.",
            "razorpay_signature": "HMAC-SHA256 of order_id|payment_id. Verified server-side.",
            "name": "Customer name.",
            "email": "Customer email.",
            "phone": "Customer phone.",
            "plan": "Plan name.",
            "original_price": "Price before discount.",
            "final_price": "Price actually charged.",
            "discount_percent": "Discount applied.",
            "coupon_code": "Coupon code. NOTE: this route uses coupon_code, not coupon.",
        },
        required=["razorpay_order_id", "razorpay_payment_id", "razorpay_signature"],
    ),
)
async def verify_payment(payload: BodyParams, db: DbSession) -> dict:
    """PR-19, PR-20 + the Decision 2 signature check."""
    return await payment_service.verify_payment(db, payload)
