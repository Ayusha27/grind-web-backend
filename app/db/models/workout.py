# app/db/models/workout.py

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    client_id: Mapped[int | None] = mapped_column(
        Integer,
    )

    plan_name: Mapped[str | None] = mapped_column(
        String(255),
    )

    is_active: Mapped[bool | None] = mapped_column(
        Boolean,
        server_default="true",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    workout_json: Mapped[str | None] = mapped_column(
        Text,
    )

    version_no: Mapped[int | None] = mapped_column(
        Integer,
        server_default="1",
    )

    __table_args__ = (
        # Active plan lookup:
        #
        # SELECT *
        # FROM workout_plans
        # WHERE client_id = ?
        #   AND is_active = true
        # ORDER BY id DESC
        # LIMIT 1
        Index(
            "ix_workout_plans_active",
            "client_id",
            "id",
            postgresql_where=(is_active.is_(True)),
        ),

        # Used when determining the latest plan version.
        Index(
            "ix_workout_plans_client_version",
            "client_id",
            "version_no",
        ),
    )


class WorkoutDay(Base):
    __tablename__ = "workout_days"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    plan_id: Mapped[int | None] = mapped_column(
        Integer,
    )

    day_number: Mapped[int | None] = mapped_column(
        Integer,
    )

    day_name: Mapped[str | None] = mapped_column(
        String(255),
    )

    __table_args__ = (
        Index(
            "ix_workout_days_plan_daynum",
            "plan_id",
            "day_number",
        ),
    )


class WorkoutExercise(Base):
    __tablename__ = "workout_exercises"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    day_id: Mapped[int | None] = mapped_column(
        Integer,
    )

    exercise_name: Mapped[str | None] = mapped_column(
        String(255),
    )

    sets_count: Mapped[int | None] = mapped_column(
        Integer,
    )

    reps: Mapped[str | None] = mapped_column(
        String(50),
    )

    youtube_url: Mapped[str | None] = mapped_column(
        Text,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
    )

    sort_order: Mapped[int | None] = mapped_column(
        Integer,
    )

    __table_args__ = (
        Index(
            "ix_workout_exercises_day_sort",
            "day_id",
            "sort_order",
        ),
    )


class WorkoutLog(Base):
    """
    Workout log / workout-day summary table.

    This table intentionally retains the legacy PHP set-level columns:

        user_email
        exercise_id
        set_no
        completed

    because the existing legacy endpoints still use them.

    The migrated implementation additionally stores one summary row per:

        client_id + month_no + week_no + day_id

    using:

        total_sets
        completed_sets
        completion_percent
        calories_burned

    Progress calculations should use WorkoutSetLog as the raw source of
    truth and WorkoutLog as the persisted day-summary/cache.
    """

    __tablename__ = "workout_logs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # ------------------------------------------------------------------
    # Legacy PHP field
    # ------------------------------------------------------------------

    user_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Migrated client identity
    # ------------------------------------------------------------------

    client_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Workout period
    # ------------------------------------------------------------------

    month_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    week_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    day_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Legacy set-level fields
    #
    # These remain nullable because summary rows do not represent one
    # particular exercise/set.
    # ------------------------------------------------------------------

    exercise_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    set_no: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    completed: Mapped[bool | None] = mapped_column(
        Boolean,
        server_default="false",
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Migrated day-summary fields
    # ------------------------------------------------------------------

    total_sets: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    completed_sets: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    completion_percent: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    calories_burned: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Timestamp
    # ------------------------------------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # ------------------------------------------------------------------
        # IMPORTANT:
        #
        # A migrated summary row represents exactly one:
        #
        # client + month + week + day
        #
        # The partial condition keeps legacy set-level rows valid because
        # those rows have total_sets = NULL.
        # ------------------------------------------------------------------
        Index(
            "uq_workout_logs_summary_client_period_day",
            "client_id",
            "month_no",
            "week_no",
            "day_id",
            unique=True,
            postgresql_where=(
                (client_id.is_not(None))
                & (total_sets.is_not(None))
                & (completed_sets.is_not(None))
            ),
        ),

        # Useful when retrieving all summary rows for a client.
        Index(
            "ix_workout_logs_summary_client_period",
            "client_id",
            "month_no",
            "week_no",
            "day_id",
            postgresql_where=(
                (client_id.is_not(None))
                & (total_sets.is_not(None))
            ),
        ),

        # Preserve the legacy completed-exercise query path:
        #
        # WHERE user_email = ?
        #   AND completed = true
        #
        # The old PHP implementation used this query.
        Index(
            "ix_workout_logs_user_completed_ex",
            "user_email",
            "completed",
            "exercise_id",
        ),
    )


class WorkoutProgress(Base):
    """
    Present in the legacy schema.

    The PHP implementation did not read/write this table directly, but it
    remains in the migrated schema so existing database structures are not
    silently removed.
    """

    __tablename__ = "workout_progress"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    client_id: Mapped[int | None] = mapped_column(
        Integer,
    )

    exercise_id: Mapped[int | None] = mapped_column(
        Integer,
    )

    set_number: Mapped[int | None] = mapped_column(
        Integer,
    )

    completed: Mapped[bool | None] = mapped_column(
        Boolean,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )


class WorkoutSetLog(Base):
    """
    Raw workout set state.

    This is the authoritative record of what the user actually completed.

    One row represents:

        client
        + month
        + week
        + day
        + exercise
        + set

    The row is updated rather than duplicated when the same set is logged
    again.
    """

    __tablename__ = "workout_set_logs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    client_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    month_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    week_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    day_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    exercise_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    set_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # ------------------------------------------------------------------
        # A set can exist only once for a particular workout position.
        #
        # This makes upsert behavior safe at the database level too.
        # ------------------------------------------------------------------
        Index(
            "uq_workout_set_logs_client_period_set",
            "client_id",
            "month_no",
            "week_no",
            "day_id",
            "exercise_id",
            "set_no",
            unique=True,
        ),

        # Fast lookup for one workout day.
        Index(
            "ix_workout_set_logs_client_period_day",
            "client_id",
            "month_no",
            "week_no",
            "day_id",
        ),

        # Fast lookup for a client's complete workout history.
        Index(
            "ix_workout_set_logs_client_month",
            "client_id",
            "month_no",
        ),
    )