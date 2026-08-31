# app/services/payment_service.py
import logging
import time
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_floatval, php_intval, php_round_int, php_trim
from app.core.config import settings
from app.core.exceptions import ValidationFailure
from app.core.security import verify_razorpay_signature
from app.integrations.razorpay_client import razorpay_client
from app.repositories import affiliate_repo, enrollment_repo

logger = logging.getLogger(__name__)

# PR-13 — an EXACT, case-sensitive whitelist. Any other plan name pays full
# price even with a valid coupon. Do not lowercase, strip or fuzzy-match this.
DISCOUNT_ELIGIBLE_PLANS: frozenset[str] = frozenset(
    {
        "3 MONTH KICKSTART",
        "6 MONTH TRANSFORMATION",
        "12 MONTH LIFESTYLE EVOLUTION",
    }
)


async def create_order(
    db: AsyncSession, *, raw_plan: Any, raw_price: Any, raw_coupon: Any
) -> dict[str, Any]:
    """PR-13 .. PR-18 — create_order.php."""
    plan = php_trim(raw_plan)
    price = php_floatval(raw_price)
    coupon = php_trim(raw_coupon)

    discount_percent = 0

    # PR-13 — both conditions required: non-empty coupon AND eligible plan.
    if coupon != "" and plan in DISCOUNT_ELIGIBLE_PLANS:
        # PR-14 — THIS path checks expiry, unlike validate (PR-10).
        row = await affiliate_repo.get_active_unexpired_code(db, coupon)
        if row is not None:
            discount_percent = php_intval(row.discount_percent)

    # PR-15 — float arithmetic, mirroring PHP exactly. Do NOT switch to
    # Decimal: it would produce different paise at half-unit boundaries and
    # break parity with the amounts Razorpay already has on file.
    final_price = price - (price * discount_percent / 100)

    # PR-16 — the guard runs AFTER final_price is computed and tests the
    # ORIGINAL price, not the discounted one. A 100% coupon therefore passes
    # this check and creates a zero-amount order. That is the legacy behaviour.
    if settings.LEGACY_ZERO_PRICE_CHECKS_ORIGINAL:
        zero_check_value = price
    else:
        zero_check_value = final_price

    if zero_check_value <= 0:
        # PHP echoed this at HTTP 200 with no status change.
        return {"success": False, "message": "Price received is zero"}

    # PR-17 — receipt is 'GRIND_' + unix seconds; amount is paise.
    receipt = f"GRIND_{int(time.time())}"
    amount = php_round_int(final_price * 100)

    order = await razorpay_client.create_order(
        amount=amount, currency="INR", receipt=receipt
    )

    logger.info(
        "order_created",
        extra={
            "order_id": order.get("id"), "amount": amount,
            "plan": plan, "discount_percent": discount_percent,
        },
    )

    # PR-18 — `amount` is rounded paise, `final_price` is the UNROUNDED float.
    # The frontend displays final_price and sends amount to Razorpay; keeping
    # both, unrounded and rounded respectively, is what the PHP did.
    return {
        "success": True,
        "order_id": order["id"],
        "amount": amount,
        "final_price": final_price,
        "discount_percent": discount_percent,
    }


async def verify_payment(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """PR-19, PR-20 — verify_payment.php, plus the Decision 2 security fix."""
    name = php_trim(payload.get("name"))
    email = php_trim(payload.get("email"))
    phone = php_trim(payload.get("phone"))
    plan = php_trim(payload.get("plan"))
    original_price = php_floatval(payload.get("original_price"))
    final_price = php_floatval(payload.get("final_price"))
    discount_percent = php_intval(payload.get("discount_percent"))
    coupon_code = php_trim(payload.get("coupon_code"))
    payment_id = php_trim(payload.get("razorpay_payment_id"))
    order_id = php_trim(payload.get("razorpay_order_id"))
    signature = php_trim(payload.get("razorpay_signature"))

    # ── Decision 2: the security fix ────────────────────────────────
    # The PHP receives razorpay_signature from the browser and IGNORES it,
    # so a hand-crafted POST creates a 'Paid' enrollment for free. The
    # frontend already sends the signature, so switching this on breaks no
    # legitimate flow. Set RAZORPAY_VERIFY_SIGNATURE=false to restore the
    # legacy behaviour exactly.
    if settings.RAZORPAY_VERIFY_SIGNATURE:
        if not verify_razorpay_signature(
            order_id=order_id, payment_id=payment_id, signature=signature
        ):
            logger.warning(
                "razorpay_signature_mismatch",
                extra={"order_id": order_id, "payment_id": payment_id, "email": email},
            )
            raise ValidationFailure("Payment verification failed")

    # ── Decision 2b: price provenance ───────────────────────────────
    # PR-20 — the legacy trusts original_price / final_price / discount as
    # sent by the browser. Recomputing server-side is correct but changes
    # outcomes, so it is off by default. When off we still LOG a mismatch,
    # which gives you the evidence to turn it on safely.
    if plan in DISCOUNT_ELIGIBLE_PLANS and coupon_code:
        row = await affiliate_repo.get_active_unexpired_code(db, coupon_code)
        server_discount = php_intval(row.discount_percent) if row else 0
        server_final = original_price - (original_price * server_discount / 100)
        if php_round_int(server_final * 100) != php_round_int(final_price * 100):
            logger.warning(
                "price_mismatch",
                extra={
                    "client_final": final_price, "server_final": server_final,
                    "plan": plan, "coupon": coupon_code, "order_id": order_id,
                },
            )
            if settings.PAYMENTS_RECOMPUTE_PRICE:
                final_price = server_final
                discount_percent = server_discount

    # Duplicate detection is LOG-ONLY. Blocking would change PR-19.
    existing = await enrollment_repo.get_by_payment_id(db, payment_id)
    if existing is not None:
        logger.warning(
            "duplicate_payment_id",
            extra={"payment_id": payment_id, "existing_enrollment_id": existing.id},
        )

    await enrollment_repo.insert(
        db,
        name=name,
        email=email,
        phone=phone,
        plan_name=plan,
        original_price=Decimal(str(original_price)),
        discount_percent=discount_percent,
        coupon_code=coupon_code,
        final_price=Decimal(str(final_price)),
        razorpay_payment_id=payment_id,
        razorpay_order_id=order_id,
        # PR-19 — hardcoded 'Paid'. The PHP never checks the payment's real
        # status with Razorpay; the signature check above is what now makes
        # this assertion trustworthy.
        payment_status="Paid",
    )
    await db.commit()

    logger.info(
        "enrollment_created",
        extra={"email": email, "plan": plan, "final_price": final_price},
    )

    # PR-19 — the success body is exactly {"success": true}, nothing else.
    return {"success": True}
