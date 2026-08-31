# app/repositories/enrollment_repo.py
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Enrollment


async def insert(
    db: AsyncSession,
    *,
    name: str,
    email: str,
    phone: str,
    plan_name: str,
    original_price: Decimal,
    discount_percent: int,
    coupon_code: str,
    final_price: Decimal,
    razorpay_payment_id: str,
    razorpay_order_id: str,
    payment_status: str,
) -> Enrollment:
    """PR-19. payment_status is passed in but is always 'Paid' from the service,
    exactly as the PHP hardcoded it.
    """
    row = Enrollment(
        name=name, email=email, phone=phone, plan_name=plan_name,
        original_price=original_price, discount_percent=discount_percent,
        coupon_code=coupon_code, final_price=final_price,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_order_id=razorpay_order_id, payment_status=payment_status,
    )
    db.add(row)
    await db.flush()
    return row


async def get_by_payment_id(db: AsyncSession, payment_id: str) -> Enrollment | None:
    """Not in the legacy code. Used only to LOG duplicate captures, never to
    block them — blocking would change PR-19 behaviour.
    """
    return (
        await db.execute(
            select(Enrollment).where(Enrollment.razorpay_payment_id == payment_id).limit(1)
        )
    ).scalars().first()
