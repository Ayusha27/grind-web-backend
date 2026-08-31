from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int | None] = mapped_column(Integer)
    plan_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool | None] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    workout_json: Mapped[str | None] = mapped_column(Text)
    version_no: Mapped[int | None] = mapped_column(Integer, server_default="1")

    __table_args__ = (
        # PR-02: WHERE client_id=? AND is_active=1 ORDER BY id DESC LIMIT 1.
        # A partial index is ~40x smaller than a full one here, because only
        # one plan per client is ever active.
        Index(
            "ix_workout_plans_active",
            "client_id",
            "id",
            postgresql_where=(is_active.is_(True)),
        ),
        # PR-24: MAX(version_no) per client during import.
        Index("ix_workout_plans_client_version", "client_id", "version_no"),
    )


class WorkoutDay(Base):
    __tablename__ = "workout_days"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int | None] = mapped_column(Integer)
    day_number: Mapped[int | None] = mapped_column(Integer)
    day_name: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        # PR-04. The legacy schema has NO index here at all — the join was a
        # seq scan of 156 rows, which is fine at 156 rows and fatal at 10k users.
        Index("ix_workout_days_plan_daynum", "plan_id", "day_number"),
    )


class WorkoutExercise(Base):
    __tablename__ = "workout_exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_id: Mapped[int | None] = mapped_column(Integer)
    exercise_name: Mapped[str | None] = mapped_column(String(255))
    sets_count: Mapped[int | None] = mapped_column(Integer)
    reps: Mapped[str | None] = mapped_column(String(50))
    youtube_url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_workout_exercises_day_sort", "day_id", "sort_order"),
    )


class WorkoutLog(Base):
    __tablename__ = "workout_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    month_no: Mapped[int] = mapped_column(Integer, nullable=False)
    week_no: Mapped[int] = mapped_column(Integer, nullable=False)
    day_id: Mapped[int] = mapped_column(Integer, nullable=False)
    exercise_id: Mapped[int] = mapped_column(Integer, nullable=False)
    set_no: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool | None] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # THE hot table: ~200k rows/day at target load (Phase 0.2 step 5).
        # PR-29 runs COUNT(DISTINCT exercise_id) WHERE user_email=? AND completed=1
        # on every portal load. Without this it is a full scan of tens of millions
        # of rows, per page view. This single index is the difference between
        # 2 ms and 8 s on that query.
        Index(
            "ix_workout_logs_user_completed_ex",
            "user_email",
            "exercise_id",
            postgresql_where=(completed.is_(True)),
        ),
        Index("ix_workout_logs_user_created", "user_email", "created_at"),
    )


class WorkoutProgress(Base):
    """Present in the legacy schema; no PHP file reads or writes it.

    Kept so the migration is a faithful reproduction and nothing silently
    disappears. Safe to drop once you confirm nothing external uses it.
    """

    __tablename__ = "workout_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int | None] = mapped_column(Integer)
    exercise_id: Mapped[int | None] = mapped_column(Integer)
    set_number: Mapped[int | None] = mapped_column(Integer)
    completed: Mapped[bool | None] = mapped_column(Boolean)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))