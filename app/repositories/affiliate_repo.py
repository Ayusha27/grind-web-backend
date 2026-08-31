# app/repositories/affiliate_repo.py
from typing import Any

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AffiliateCode, Enrollment


async def get_active_code(db: AsyncSession, code: str) -> AffiliateCode | None:
    """PR-10 — validate_affiliate.php.

    PHP: SELECT * FROM affiliate_codes WHERE code = ? AND status = 'active'
    Note the ABSENCE of an expiry check. An expired code validates here and
    is then refused at order time (PR-14). The two paths genuinely disagree
    in the legacy system and Decision 4 keeps that.
    """
    stmt = select(AffiliateCode).where(
        AffiliateCode.code == code,
        AffiliateCode.status == "active",
    )
    return (await db.execute(stmt)).scalars().first()


async def get_active_unexpired_code(db: AsyncSession, code: str) -> AffiliateCode | None:
    """PR-14 — create_order.php.

    PHP: ... AND status='active' AND expiry_date >= CURDATE() LIMIT 1

    MySQL's `NULL >= CURDATE()` is NULL, i.e. the row is excluded — so a code
    with no expiry date does NOT discount. Postgres behaves identically, but
    the comparison is written explicitly so the intent survives review.
    """
    stmt = (
        select(AffiliateCode)
        .where(
            AffiliateCode.code == code,
            AffiliateCode.status == "active",
            AffiliateCode.expiry_date.is_not(None),
            AffiliateCode.expiry_date >= func.current_date(),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def get_dashboard_rows(db: AsyncSession) -> list[dict[str, Any]]:
    """PR-12 — affiliate-dashboard.php, one aggregate query.

    PHP:
      SELECT ac.*, COUNT(e.id) total_sales,
             COALESCE(SUM(e.final_price),0) revenue,
             COALESCE(AVG(e.final_price),0) average_order,
             COALESCE(SUM(e.final_price)*ac.commission_percent/100,0) commission_due
      FROM affiliate_codes ac
      LEFT JOIN enrollments e
        ON ac.code = e.coupon_code AND e.payment_status = 'Paid'
      GROUP BY ...  ORDER BY revenue DESC

    The join predicate (not a WHERE) is load-bearing: it keeps affiliates with
    zero paid sales in the result with revenue 0. Moving it to WHERE would
    drop them — a silent behaviour change.
    """
    revenue = func.coalesce(func.sum(Enrollment.final_price), 0)

    stmt = (
        select(
            AffiliateCode.id,
            AffiliateCode.affiliate_name,
            AffiliateCode.affiliate_email,
            AffiliateCode.code,
            AffiliateCode.discount_percent,
            AffiliateCode.commission_percent,
            AffiliateCode.status,
            func.count(Enrollment.id).label("total_sales"),
            revenue.label("revenue"),
            func.coalesce(func.avg(Enrollment.final_price), 0).label("average_order"),
            func.coalesce(
                func.sum(Enrollment.final_price)
                * cast(AffiliateCode.commission_percent, Numeric(10, 4))
                / 100,
                0,
            ).label("commission_due"),
        )
        .select_from(AffiliateCode)
        .outerjoin(
            Enrollment,
            (AffiliateCode.code == Enrollment.coupon_code)
            & (Enrollment.payment_status == "Paid"),
        )
        .group_by(
            AffiliateCode.id,
            AffiliateCode.affiliate_name,
            AffiliateCode.affiliate_email,
            AffiliateCode.code,
            AffiliateCode.discount_percent,
            AffiliateCode.commission_percent,
            AffiliateCode.status,
        )
        .order_by(revenue.desc())
    )
    return [dict(row) for row in (await db.execute(stmt)).mappings().all()]
