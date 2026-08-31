from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AffiliateCode(Base):
    __tablename__ = "affiliate_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str | None] = mapped_column(String(50), unique=True)
    affiliate_name: Mapped[str | None] = mapped_column(String(100))
    affiliate_email: Mapped[str | None] = mapped_column(String(150))
    discount_percent: Mapped[int | None] = mapped_column(Integer, server_default="10")
    commission_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), server_default="0.00"
    )
    total_sales: Mapped[int | None] = mapped_column(Integer, server_default="0")
    total_revenue: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), server_default="0.00"
    )
    status: Mapped[str | None] = mapped_column(String(20), server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expiry_date: Mapped[date | None] = mapped_column(Date)


class AffiliateConversion(Base):
    """Schema exists in the legacy dump; no PHP file ever writes it.

    Reproduced for fidelity. PR-19 explicitly keeps it unwritten.
    """

    __tablename__ = "affiliate_conversions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    affiliate_code: Mapped[str | None] = mapped_column(String(50))
    plan_name: Mapped[str | None] = mapped_column(String(100))
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    customer_name: Mapped[str | None] = mapped_column(String(255))
    customer_email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )