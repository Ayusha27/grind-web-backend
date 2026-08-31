# app/services/affiliate_service.py
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_intval, php_trim
from app.repositories import affiliate_repo


async def validate_code(db: AsyncSession, raw_code: Any) -> dict[str, Any]:
    """PR-10, PR-11 — validate_affiliate.php.

    The PHP is `trim($_POST['code'])` with NO isset() guard: a missing field
    raised a notice and trim(null) produced "". We reproduce the "" outcome.

    PR-10: no expiry check here, on purpose. GR_INDIA_30 expired 2026-08-20
    and still validates through this endpoint while being refused at order
    time. That inconsistency is the legacy behaviour.

    PR-11: both branches are HTTP 200. A miss is not a 404.
    """
    code = php_trim(raw_code)
    row = await affiliate_repo.get_active_code(db, code)

    if row is not None:
        return {"success": True, "discount": row.discount_percent, "code": row.code}

    return {"success": False, "message": "Coupon not found"}


async def get_dashboard(db: AsyncSession) -> dict[str, Any]:
    """PR-12 — affiliate-dashboard.php, returned as JSON instead of HTML.

    The PHP summed the totals in a PHP foreach rather than in SQL. Summing
    the same rows in Python reproduces it exactly, including the fact that
    `total_sales` totals COUNT(e.id) per affiliate (so an enrollment with a
    coupon matching two codes would be counted twice — it cannot, because
    affiliate_codes.code is unique, but the summation order is preserved).
    """
    rows = await affiliate_repo.get_dashboard_rows(db)

    total_sales = 0
    total_revenue = Decimal("0")
    for row in rows:
        total_sales += php_intval(row["total_sales"])
        total_revenue += Decimal(str(row["revenue"] or 0))

    return {
        "success": True,
        "summary": {
            "total_affiliates": len(rows),
            "total_sales": total_sales,
            "total_revenue": float(total_revenue),
        },
        "affiliates": [
            {
                "id": r["id"],
                "affiliate_name": r["affiliate_name"],
                "affiliate_email": r["affiliate_email"],
                "code": r["code"],
                "discount_percent": r["discount_percent"],
                "commission_percent": float(r["commission_percent"] or 0),
                "status": r["status"],
                "total_sales": php_intval(r["total_sales"]),
                "revenue": float(r["revenue"] or 0),
                "average_order": float(r["average_order"] or 0),
                "commission_due": float(r["commission_due"] or 0),
            }
            for r in rows
        ],
    }
