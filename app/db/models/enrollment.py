from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    plan_name: Mapped[str | None] = mapped_column(String(100))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    discount_percent: Mapped[int | None] = mapped_column(Integer, server_default="0")
    coupon_code: Mapped[str | None] = mapped_column(String(50))
    final_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(255))
    razorpay_order_id: Mapped[str | None] = mapped_column(String(255))
    payment_status: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # PR-12 joins affiliate_codes.code -> enrollments.coupon_code
        # and filters payment_status='Paid'.
        Index("ix_enrollments_coupon_status", "coupon_code", "payment_status"),
        # Lets you detect a duplicate capture without changing PR-19 behaviour.
        Index("ix_enrollments_payment_id", "razorpay_payment_id"),
        Index("ix_enrollments_email", "email"),
    )