# app/repositories/workout_repo.py

from collections import defaultdict

from sqlalchemy import distinct, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_round_int
from app.core.config import settings
from app.db.models import (
    WorkoutDay,
    WorkoutExercise,
    WorkoutLog,
    WorkoutPlan,
)
from app.db.models.workout import WorkoutSetLog


# =====================================================================
# WORKOUT PLAN
# =====================================================================


async def get_active_plan(
    db: AsyncSession,
    client_id: int,
) -> WorkoutPlan | None:
    """
    PHP:

        SELECT *
        FROM workout_plans
        WHERE client_id = ?
          AND is_active = 1
        ORDER BY id DESC
        LIMIT 1
    """

    stmt = (
        select(WorkoutPlan)
        .where(
            WorkoutPlan.client_id == client_id,
            WorkoutPlan.is_active.is_(True),
        )
        .order_by(WorkoutPlan.id.desc())
        .limit(1)
    )

    return (await db.execute(stmt)).scalar_one_or_none()


async def get_days_with_exercises(
    db: AsyncSession,
    plan_id: int,
) -> list[tuple[WorkoutDay, list[WorkoutExercise]]]:
    """
    Load all workout days and their exercises.

    This keeps the existing optimized two-query implementation rather than
    performing one query per day.
    """

    day_stmt = (
        select(WorkoutDay)
        .where(WorkoutDay.plan_id == plan_id)
        .order_by(
            WorkoutDay.day_number.asc(),
            WorkoutDay.id.asc(),
        )
    )

    days = list(
        (await db.execute(day_stmt))
        .scalars()
        .all()
    )

    if not days:
        return []

    day_ids = [day.id for day in days]

    exercise_stmt = (
        select(WorkoutExercise)
        .where(
            WorkoutExercise.day_id.in_(day_ids)
        )
        .order_by(
            WorkoutExercise.day_id.asc(),
            WorkoutExercise.sort_order.asc(),
            WorkoutExercise.id.asc(),
        )
    )

    exercises = list(
        (await db.execute(exercise_stmt))
        .scalars()
        .all()
    )

    grouped: dict[int | None, list[WorkoutExercise]] = defaultdict(list)

    for exercise in exercises:
        grouped[exercise.day_id].append(exercise)

    return [
        (
            day,
            grouped.get(day.id, []),
        )
        for day in days
    ]


# =====================================================================
# LEGACY WORKOUT LOGGING
# =====================================================================


async def insert_log(
    db: AsyncSession,
    *,
    user_email: str,
    month_no: int,
    week_no: int,
    day_id: int,
    exercise_id: int,
    set_no: int,
    completed: bool,
) -> WorkoutLog:
    """
    Legacy set-level workout_logs insert.

    Existing /workout/complete and /workout/logs still depend on this
    behavior, so it is intentionally preserved.
    """

    log = WorkoutLog(
        user_email=user_email,
        month_no=month_no,
        week_no=week_no,
        day_id=day_id,
        exercise_id=exercise_id,
        set_no=set_no,
        completed=completed,
    )

    db.add(log)

    await db.flush()

    return log


# =====================================================================
# WORKOUT SUMMARY
# =====================================================================


async def get_workout_summary(
    db: AsyncSession,
    *,
    client_id: int,
    month_no: int,
    week_no: int,
    day_id: int,
) -> WorkoutLog | None:
    """
    Return the migrated summary row for one workout day.

    A summary row is identified by:

        client_id
        month_no
        week_no
        day_id

    Legacy set-level rows are excluded because they have NULL total_sets.
    """

    stmt = (
        select(WorkoutLog)
        .where(
            WorkoutLog.client_id == client_id,
            WorkoutLog.month_no == month_no,
            WorkoutLog.week_no == week_no,
            WorkoutLog.day_id == day_id,
            WorkoutLog.total_sets.is_not(None),
            WorkoutLog.completed_sets.is_not(None),
        )
        .order_by(
            WorkoutLog.id.desc()
        )
        .limit(1)
    )

    return (
        await db.execute(stmt)
    ).scalar_one_or_none()


async def upsert_workout_summary(
    db: AsyncSession,
    *,
    client_id: int,
    month_no: int,
    week_no: int,
    day_id: int,
    total_sets: int,
    completed_sets: int,
    completion_percent: float,
    calories_burned: int,
) -> WorkoutLog:
    """
    Insert or update one workout-day summary.

    This prevents repeated "LOG WORKOUT" submissions from creating multiple
    summary rows for the same:

        client + month + week + day

    The summary is a derived/cache record. Raw set completion remains stored
    separately in workout_set_logs.
    """

    existing = await get_workout_summary(
        db,
        client_id=client_id,
        month_no=month_no,
        week_no=week_no,
        day_id=day_id,
    )

    if existing is not None:
        existing.total_sets = total_sets
        existing.completed_sets = completed_sets
        existing.completion_percent = completion_percent
        existing.calories_burned = calories_burned

        # Summary rows do not represent a specific exercise/set.
        existing.exercise_id = None
        existing.set_no = None
        existing.completed = None

        await db.flush()

        return existing

    log = WorkoutLog(
        client_id=client_id,
        month_no=month_no,
        week_no=week_no,
        day_id=day_id,
        total_sets=total_sets,
        completed_sets=completed_sets,
        completion_percent=completion_percent,
        calories_burned=calories_burned,
        exercise_id=None,
        set_no=None,
        completed=None,
    )

    db.add(log)

    await db.flush()

    return log


# Keep the old repository function name so existing callers do not break.
async def insert_workout_summary(
    db: AsyncSession,
    *,
    client_id: int,
    month_no: int,
    week_no: int,
    day_id: int,
    total_sets: int,
    completed_sets: int,
    completion_percent: float,
    calories_burned: int,
) -> WorkoutLog:
    """
    Backwards-compatible wrapper.

    Existing service code can continue calling insert_workout_summary(),
    while the actual implementation now performs an upsert.
    """

    return await upsert_workout_summary(
        db,
        client_id=client_id,
        month_no=month_no,
        week_no=week_no,
        day_id=day_id,
        total_sets=total_sets,
        completed_sets=completed_sets,
        completion_percent=completion_percent,
        calories_burned=calories_burned,
    )


# =====================================================================
# LEGACY EXERCISE PROGRESS
# =====================================================================


async def count_all_exercises(
    db: AsyncSession,
) -> int:
    """
    PHP PR-28 compatibility.

    Original PHP:

        SELECT COUNT(*)
        FROM workout_exercises

    This is intentionally retained for the legacy exercise-progress path.
    """

    return int(
        (
            await db.execute(
                select(
                    func.count()
                ).select_from(
                    WorkoutExercise
                )
            )
        ).scalar_one()
    )


async def count_completed_exercises(
    db: AsyncSession,
    user_email: str,
) -> int:
    """
    PHP PR-29 compatibility.

    Original PHP:

        SELECT COUNT(DISTINCT exercise_id)
        FROM workout_logs
        WHERE user_email = ?
          AND completed = 1
    """

    stmt = select(
        func.count(
            distinct(
                WorkoutLog.exercise_id
            )
        )
    ).where(
        WorkoutLog.user_email == user_email,
        WorkoutLog.completed.is_(True),
    )

    return int(
        (
            await db.execute(stmt)
        ).scalar_one()
        or 0
    )


# =====================================================================
# PLAN ADMIN / IMPORT
# =====================================================================


async def get_max_version(
    db: AsyncSession,
    client_id: int,
) -> int:
    """
    Return the highest plan version for a client.
    """

    stmt = select(
        func.coalesce(
            func.max(
                WorkoutPlan.version_no
            ),
            0,
        )
    ).where(
        WorkoutPlan.client_id == client_id
    )

    return (
        await db.execute(stmt)
    ).scalar_one() or 0


async def deactivate_plans(
    db: AsyncSession,
    client_id: int,
) -> None:
    """
    Deactivate all plans belonging to a client.
    """

    await db.execute(
        update(WorkoutPlan)
        .where(
            WorkoutPlan.client_id == client_id
        )
        .values(
            is_active=False
        )
    )


async def insert_plan(
    db: AsyncSession,
    *,
    client_id: int,
    plan_name: str | None,
    workout_json: str | None,
    is_active: bool | None = None,
    version_no: int | None = None,
) -> WorkoutPlan:
    """
    Insert a workout plan.
    """

    values: dict[str, object] = {
        "client_id": client_id,
        "plan_name": plan_name,
        "workout_json": workout_json,
    }

    if is_active is not None:
        values["is_active"] = is_active

    if version_no is not None:
        values["version_no"] = version_no

    plan = WorkoutPlan(
        **values
    )

    db.add(plan)

    await db.flush()

    return plan


async def insert_day(
    db: AsyncSession,
    *,
    plan_id: int,
    day_number: int,
    day_name: str | None,
) -> WorkoutDay:
    """
    Insert one workout day.
    """

    day = WorkoutDay(
        plan_id=plan_id,
        day_number=day_number,
        day_name=day_name,
    )

    db.add(day)

    await db.flush()

    return day


async def insert_exercises(
    db: AsyncSession,
    *,
    day_id: int,
    rows: list[dict[str, object]],
) -> None:
    """
    Bulk insert workout exercises.
    """

    if not rows:
        return

    db.add_all(
        [
            WorkoutExercise(
                day_id=day_id,
                **row,
            )
            for row in rows
        ]
    )

    await db.flush()


# =====================================================================
# SUMMARY PROGRESS
# =====================================================================


async def get_workout_summary_progress(
    db: AsyncSession,
    client_id: int,
) -> dict[str, int]:
    """
    Legacy summary aggregation.

    Kept for compatibility with any existing caller.

    NOTE:
    The new Progress implementation should use the detailed day-level
    calculation rather than blindly summing duplicate/legacy rows.
    """

    result = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    WorkoutLog.total_sets
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    WorkoutLog.completed_sets
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    WorkoutLog.calories_burned
                ),
                0,
            ),
        )
        .where(
            WorkoutLog.client_id == client_id,
            WorkoutLog.total_sets.is_not(None),
            WorkoutLog.completed_sets.is_not(None),
        )
    )

    (
        total_sets,
        completed_sets,
        calories_burned,
    ) = result.one()

    total_sets = int(
        total_sets or 0
    )

    completed_sets = int(
        completed_sets or 0
    )

    calories_burned = int(
        calories_burned or 0
    )

    if total_sets > 0:
        percent = php_round_int(
            completed_sets
            / total_sets
            * 100
        )
    else:
        percent = 0

    return {
        "total": total_sets,
        "completed": completed_sets,
        "percent": percent,
        "calories_burned": calories_burned,
    }


# =====================================================================
# ACTIVE PLAN / SESSION DENOMINATORS
# =====================================================================


async def count_active_plan_days(
    db: AsyncSession,
    client_id: int,
) -> int:
    """
    Count workout days in the client's newest active plan.

    This is used as the client's planned workouts-per-week value.
    """

    latest_plan = (
        select(
            WorkoutPlan.id
        )
        .where(
            WorkoutPlan.client_id == client_id,
            WorkoutPlan.is_active.is_(True),
        )
        .order_by(
            WorkoutPlan.id.desc()
        )
        .limit(1)
        .scalar_subquery()
    )

    stmt = select(
        func.count(
            WorkoutDay.id
        )
    ).where(
        WorkoutDay.plan_id == latest_plan
    )

    return int(
        (
            await db.execute(stmt)
        ).scalar()
        or 0
    )


# =====================================================================
# SUMMARY DETAILS
# =====================================================================


async def get_workout_summary_details(
    db: AsyncSession,
    client_id: int,
) -> list[WorkoutLog]:
    """
    Return one summary row per workout day where possible.

    Legacy set-level workout_logs rows are excluded.

    Existing databases may contain duplicate summary rows from earlier
    versions. The query deliberately orders newest first so that callers
    which collapse by day can use the latest persisted summary.
    """

    stmt = (
        select(WorkoutLog)
        .where(
            WorkoutLog.client_id == client_id,
            WorkoutLog.total_sets.is_not(None),
            WorkoutLog.completed_sets.is_not(None),
        )
        .order_by(
            WorkoutLog.month_no.asc(),
            WorkoutLog.week_no.asc(),
            WorkoutLog.day_id.asc(),
            WorkoutLog.id.desc(),
        )
    )

    result = await db.execute(stmt)

    return list(
        result.scalars().all()
    )


# =====================================================================
# RAW SET LOGS
# =====================================================================


async def upsert_workout_set(
    db: AsyncSession,
    *,
    client_id: int,
    month_no: int,
    week_no: int,
    day_id: int,
    exercise_id: int,
    set_no: int,
    completed: bool,
) -> WorkoutSetLog:
    """
    Create or update one raw workout-set state.

    Unique logical key:

        client
        + month
        + week
        + day
        + exercise
        + set
    """

    stmt = select(
        WorkoutSetLog
    ).where(
        WorkoutSetLog.client_id == client_id,
        WorkoutSetLog.month_no == month_no,
        WorkoutSetLog.week_no == week_no,
        WorkoutSetLog.day_id == day_id,
        WorkoutSetLog.exercise_id == exercise_id,
        WorkoutSetLog.set_no == set_no,
    )

    result = await db.execute(stmt)

    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.completed = completed

        await db.flush()

        return existing

    log = WorkoutSetLog(
        client_id=client_id,
        month_no=month_no,
        week_no=week_no,
        day_id=day_id,
        exercise_id=exercise_id,
        set_no=set_no,
        completed=completed,
    )

    db.add(log)

    await db.flush()

    return log


async def get_workout_sets(
    db: AsyncSession,
    *,
    client_id: int,
    month_no: int,
    week_no: int,
    day_id: int,
) -> list[WorkoutSetLog]:
    """
    Return raw set states for one workout day.
    """

    stmt = (
        select(WorkoutSetLog)
        .where(
            WorkoutSetLog.client_id == client_id,
            WorkoutSetLog.month_no == month_no,
            WorkoutSetLog.week_no == week_no,
            WorkoutSetLog.day_id == day_id,
        )
        .order_by(
            WorkoutSetLog.exercise_id.asc(),
            WorkoutSetLog.set_no.asc(),
        )
    )

    result = await db.execute(stmt)

    return list(
        result.scalars().all()
    )


async def get_workout_sets_for_month(
    db: AsyncSession,
    *,
    client_id: int,
    month_no: int,
) -> list[WorkoutSetLog]:
    """
    Return all raw set states for a client/month.
    """

    stmt = (
        select(WorkoutSetLog)
        .where(
            WorkoutSetLog.client_id == client_id,
            WorkoutSetLog.month_no == month_no,
        )
        .order_by(
            WorkoutSetLog.week_no.asc(),
            WorkoutSetLog.day_id.asc(),
            WorkoutSetLog.exercise_id.asc(),
            WorkoutSetLog.set_no.asc(),
        )
    )

    result = await db.execute(stmt)

    return list(
        result.scalars().all()
    )


async def get_workout_sets_for_period(
    db: AsyncSession,
    *,
    client_id: int,
    month_no: int,
    week_no: int,
    day_id: int,
) -> list[WorkoutSetLog]:
    """
    Explicitly named alias for retrieving raw set state for one
    month/week/day.

    Kept separate from get_workout_sets() so future Progress code can use
    a clearly named method without changing the existing API.
    """

    return await get_workout_sets(
        db,
        client_id=client_id,
        month_no=month_no,
        week_no=week_no,
        day_id=day_id,
    )


async def upsert_workout_sets(
    db: AsyncSession,
    *,
    client_id: int,
    month_no: int,
    week_no: int,
    day_id: int,
    sets: list,
) -> list[WorkoutSetLog]:
    """
    Create or update all supplied raw set states for one workout.
    """

    logs: list[WorkoutSetLog] = []

    for workout_set in sets:
        log = await upsert_workout_set(
            db,
            client_id=client_id,
            month_no=month_no,
            week_no=week_no,
            day_id=day_id,
            exercise_id=workout_set.exercise_id,
            set_no=workout_set.set_no,
            completed=workout_set.completed,
        )

        logs.append(log)

    return logs