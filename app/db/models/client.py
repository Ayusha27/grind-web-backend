from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    goal: Mapped[str | None] = mapped_column(String(255))
    # enum('active','inactive') in MySQL. Kept as a string: a native enum
    # would raise on any value the legacy data happens to contain.
    status: Mapped[str | None] = mapped_column(String(20), server_default="active")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    access_token: Mapped[str | None] = mapped_column(String(50), unique=True)


class ClientProgress(Base):
    __tablename__ = "client_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    waist: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    chest: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    arms: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    thighs: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # PR-32 reads newest and oldest per client on every portal load.
        Index("ix_client_progress_client_created", "client_id", "created_at"),
    )