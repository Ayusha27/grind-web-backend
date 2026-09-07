import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_intval, php_trim
from app.core.config import settings
from app.core.exceptions import ValidationFailure
from app.db.models import WorkoutDay, WorkoutExercise, WorkoutPlan
from app.repositories import workout_repo
from app.schemas.workout import (
    WorkoutLogCreate,
    WorkoutSetLogCreate,
    WorkoutSetLogInput,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Workout calorie configuration
# ---------------------------------------------------------------------

WORKOUT_CAL_MIN = 250
WORKOUT_CAL_MAX = 350


# =====================================================================
# RESPONSE HELPERS
# =====================================================================


def _plan_dict(plan: WorkoutPlan) -> dict[str, Any]:
    """PHP `SELECT *` returned every column."""

    return {
        "id": plan.id,
        "client_id": plan.client_id,
        "plan_name": plan.plan_name,
        "is_active": int(bool(plan.is_active)),
        "created_at": (
            plan.created_at.strftime("%Y-%m-%d %H:%M:%S")
            if plan.created_at
            else None
        ),
        "workout_json": plan.workout_json,
        "version_no": plan.version_no,
    }


def _exercise_dict(
    ex: WorkoutExercise,
) -> dict[str, Any]:
    return {
        "id": ex.id,
        "day_id": ex.day_id,
        "exercise_name": ex.exercise_name,
        "sets_count": ex.sets_count,
        "reps": ex.reps,
        "youtube_url": ex.youtube_url,
        "notes": ex.notes,
        "sort_order": ex.sort_order,
    }


def _day_dict(
    day: WorkoutDay,
    exercises: list[WorkoutExercise],
) -> dict[str, Any]:
    return {
        "id": day.id,
        "plan_id": day.plan_id,
        "day_number": day.day_number,
        "day_name": day.day_name,
        "exercises": [
            _exercise_dict(ex)
            for ex in exercises
        ],
    }


# =====================================================================
# CALCULATION HELPERS
# =====================================================================


def _php_round(value: float) -> int:
    """
    Match PHP round() for non-negative workout values.

    PHP rounds .5 away from zero.
    """

    return int(value + 0.5)


def calculate_completion_percent(
    completed_sets: int,
    total_sets: int,
) -> float:
    """
    Calculate workout-day completion percentage.

        completed_sets / total_sets * 100
    """

    if total_sets <= 0:
        return 0.0

    completed_sets = max(
        0,
        min(
            completed_sets,
            total_sets,
        ),
    )

    percentage = (
        completed_sets
        / total_sets
        * 100
    )

    return round(
        percentage,
        2,
    )


def calculate_workout_calories(
    completed_sets: int,
    total_sets: int,
    *,
    cal_min: int = WORKOUT_CAL_MIN,
    cal_max: int = WORKOUT_CAL_MAX,
) -> int:
    """
    Match the migrated PHP workout calorie formula.

        r = completed_sets / total_sets

        calories =
            round(
                (calMin + (calMax - calMin) * r) * r
            )
    """

    if total_sets <= 0 or completed_sets <= 0:
        return 0

    completed_sets = min(
        completed_sets,
        total_sets,
    )

    ratio = (
        completed_sets
        / total_sets
    )

    calories = (
        cal_min
        + (
            cal_max
            - cal_min
        )
        * ratio
    ) * ratio

    return _php_round(
        calories
    )


# =====================================================================
# WORKOUT PLAN
# =====================================================================


async def get_workout(
    db: AsyncSession,
    raw_client_id: Any,
) -> dict[str, Any]:
    """
    Legacy api/workout.php compatibility endpoint.
    """

    if (
        raw_client_id is None
        or raw_client_id == ""
    ):
        client_id = (
            settings.LEGACY_DEFAULT_CLIENT_ID
        )
    else:
        client_id = php_intval(
            raw_client_id
        )

    plan = await workout_repo.get_active_plan(
        db,
        client_id,
    )

    if plan is None:
        return {
            "success": True,
            "data": None,
            "message": "No workout plan assigned.",
        }

    days = await workout_repo.get_days_with_exercises(
        db,
        plan.id,
    )

    return {
        "success": True,
        "data": {
            "plan": _plan_dict(plan),
            "days": [
                _day_dict(
                    day,
                    exercises,
                )
                for day, exercises in days
            ],
        },
    }


# =====================================================================
# LEGACY WORKOUT ENDPOINTS
# =====================================================================


async def complete_workout(
    db: AsyncSession,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Legacy api/complete-workout.php compatibility endpoint.
    """

    exercise_id = php_intval(
        payload.get("exercise_id")
    )

    day_id = php_intval(
        payload.get("day_id")
    )

    user_email = php_trim(
        payload.get("user_email")
    )

    if (
        exercise_id <= 0
        or day_id <= 0
        or user_email == ""
    ):
        raise ValidationFailure(
            "Missing required fields"
        )

    await workout_repo.insert_log(
        db,
        user_email=user_email,
        month_no=1,
        week_no=1,
        day_id=day_id,
        exercise_id=exercise_id,
        set_no=1,
        completed=True,
    )

    await db.commit()

    return {
        "success": True,
        "message": "Workout marked complete",
    }


async def save_log(
    db: AsyncSession,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Legacy base files/save-progress.php compatibility endpoint.
    """

    user_email = php_trim(
        payload.get("email")
    )

    if user_email == "":
        raise ValidationFailure(
            "Missing required fields"
        )

    await workout_repo.insert_log(
        db,
        user_email=user_email,
        month_no=php_intval(
            payload.get("month")
        ) or 1,
        week_no=php_intval(
            payload.get("week")
        ) or 1,
        day_id=php_intval(
            payload.get("day")
        ),
        exercise_id=php_intval(
            payload.get("exercise")
        ),
        set_no=php_intval(
            payload.get("set")
        ) or 1,
        completed=bool(
            php_intval(
                payload.get("completed")
            )
        ),
    )

    await db.commit()

    return {
        "success": True
    }


# =====================================================================
# WORKOUT PLAN RESOLUTION
# =====================================================================


async def _get_workout_day_plan(
    db: AsyncSession,
    *,
    client_id: int,
    day_number: int,
) -> tuple[
    WorkoutDay | None,
    list[WorkoutExercise],
]:
    """
    Resolve the client's active workout plan and requested day.

    The migrated API uses day_number (1, 2, 3...) as the workout day
    identifier from the frontend.
    """

    plan = await workout_repo.get_active_plan(
        db,
        client_id,
    )

    if plan is None:
        raise ValidationFailure(
            "No workout plan assigned."
        )

    day_rows = (
        await workout_repo.get_days_with_exercises(
            db,
            plan.id,
        )
    )

    for day, exercises in day_rows:
        if day.day_number == day_number:
            return day, exercises

    raise ValidationFailure(
        "Workout day not found."
    )


def _calculate_planned_sets(
    exercises: list[WorkoutExercise],
) -> int:
    """
    Calculate planned sets from workout_exercises.sets_count.
    """

    return sum(
        max(
            0,
            int(
                exercise.sets_count
                or 0
            ),
        )
        for exercise in exercises
    )


# =====================================================================
# SET STATE NORMALIZATION
# =====================================================================


def _normalise_set_states(
    payload: WorkoutLogCreate,
    exercises: list[WorkoutExercise],
) -> tuple[
    list[WorkoutSetLogInput],
    int,
]:
    """
    Validate raw frontend set states against the actual workout plan.

    Returns Pydantic set objects rather than dictionaries so the repository
    can safely access:

        .exercise_id
        .set_no
        .completed
    """

    planned_by_exercise: dict[int, int] = {}

    for exercise in exercises:
        if exercise.id is None:
            continue

        planned_by_exercise[
            int(exercise.id)
        ] = max(
            0,
            int(
                exercise.sets_count
                or 0
            ),
        )

    normalized: list[WorkoutSetLogInput] = []

    seen: set[tuple[int, int]] = set()

    for workout_set in payload.sets:
        exercise_id = int(
            workout_set.exercise_id
        )

        set_no = int(
            workout_set.set_no
        )

        key = (
            exercise_id,
            set_no,
        )

        if key in seen:
            raise ValidationFailure(
                "Duplicate workout set submitted."
            )

        seen.add(key)

        if exercise_id not in planned_by_exercise:
            raise ValidationFailure(
                "Workout exercise does not belong to the selected day."
            )

        planned_set_count = (
            planned_by_exercise[
                exercise_id
            ]
        )

        if (
            set_no <= 0
            or set_no > planned_set_count
        ):
            raise ValidationFailure(
                "Workout set number is outside the planned set range."
            )

        normalized.append(
            WorkoutSetLogInput(
                exercise_id=exercise_id,
                set_no=set_no,
                completed=bool(
                    workout_set.completed
                ),
            )
        )

    completed_sets = sum(
        1
        for workout_set in normalized
        if workout_set.completed
    )

    return (
        normalized,
        completed_sets,
    )


# =====================================================================
# NEW MIGRATED WORKOUT SUMMARY
# =====================================================================


async def save_workout_summary(
    db: AsyncSession,
    payload: WorkoutLogCreate,
    *,
    client_id: int,
) -> dict[str, Any]:
    """
    Save one complete workout-day state.

    IMPORTANT:

    client_id comes from CurrentClient in the API route.

    The frontend does NOT control the client identity.

    Backend calculates:

        total_sets
        completed_sets
        completion_percent
        calories_burned

    using:

        active workout plan
        +
        submitted set states
    """

    # -------------------------------------------------------------
    # Resolve active workout plan/day.
    # -------------------------------------------------------------

    day, exercises = (
        await _get_workout_day_plan(
            db,
            client_id=client_id,
            day_number=payload.day_id,
        )
    )

    if day is None:
        raise ValidationFailure(
            "Workout day not found."
        )

    # -------------------------------------------------------------
    # Planned sets are backend-owned.
    # -------------------------------------------------------------

    total_sets = _calculate_planned_sets(
        exercises
    )

    # -------------------------------------------------------------
    # Validate submitted raw set states.
    # -------------------------------------------------------------

    normalized_sets, completed_sets = (
        _normalise_set_states(
            payload,
            exercises,
        )
    )

    if completed_sets > total_sets:
        raise ValidationFailure(
            "Completed sets cannot exceed total sets"
        )

    # -------------------------------------------------------------
    # Backend calculations.
    # -------------------------------------------------------------

    completion_percent = (
        calculate_completion_percent(
            completed_sets,
            total_sets,
        )
    )

    calories_burned = (
        calculate_workout_calories(
            completed_sets,
            total_sets,
            cal_min=WORKOUT_CAL_MIN,
            cal_max=WORKOUT_CAL_MAX,
        )
    )

    # -------------------------------------------------------------
    # Save raw set states.
    #
    # workout_set_logs = source of truth for individual sets.
    # -------------------------------------------------------------

    set_logs = await workout_repo.upsert_workout_sets(
        db,
        client_id=client_id,
        month_no=payload.month_no,
        week_no=payload.week_no,
        day_id=payload.day_id,
        sets=normalized_sets,
    )

    # -------------------------------------------------------------
    # Save derived day summary.
    # -------------------------------------------------------------

    log = await workout_repo.upsert_workout_summary(
        db,
        client_id=client_id,
        month_no=payload.month_no,
        week_no=payload.week_no,
        day_id=payload.day_id,
        total_sets=total_sets,
        completed_sets=completed_sets,
        completion_percent=completion_percent,
        calories_burned=calories_burned,
    )

    await db.commit()

    return {
        "success": True,
        "message": "Workout logged successfully",
        "log_id": log.id,
        "sets_logged": len(set_logs),
        "summary": {
            "total_sets": total_sets,
            "completed_sets": completed_sets,
            "completion_percent": completion_percent,
            "calories_burned": calories_burned,
        },
    }


# =====================================================================
# INDIVIDUAL SET ENDPOINT
# =====================================================================


async def save_workout_set(
    db: AsyncSession,
    *,
    client_id: int,
    payload: WorkoutSetLogCreate,
) -> dict[str, Any]:
    """
    Save or update one individual workout set.

    The client identity comes from CurrentClient.

    This endpoint only changes raw set state. It does not create a
    day summary by itself.
    """

    day, exercises = (
        await _get_workout_day_plan(
            db,
            client_id=client_id,
            day_number=payload.day_id,
        )
    )

    if day is None:
        raise ValidationFailure(
            "Workout day not found."
        )

    planned_by_exercise: dict[
        int,
        int,
    ] = {}

    for exercise in exercises:
        if exercise.id is None:
            continue

        planned_by_exercise[
            int(exercise.id)
        ] = max(
            0,
            int(
                exercise.sets_count
                or 0
            ),
        )

    if payload.exercise_id not in planned_by_exercise:
        raise ValidationFailure(
            "Workout exercise does not belong to the selected day."
        )

    planned_set_count = (
        planned_by_exercise[
            payload.exercise_id
        ]
    )

    if (
        payload.set_no <= 0
        or payload.set_no > planned_set_count
    ):
        raise ValidationFailure(
            "Workout set number is outside the planned set range."
        )

    log = await workout_repo.upsert_workout_set(
        db,
        client_id=client_id,
        month_no=payload.month_no,
        week_no=payload.week_no,
        day_id=payload.day_id,
        exercise_id=payload.exercise_id,
        set_no=payload.set_no,
        completed=payload.completed,
    )

    await db.commit()

    return {
        "success": True,
        "id": log.id,
        "client_id": log.client_id,
        "month_no": log.month_no,
        "week_no": log.week_no,
        "day_id": log.day_id,
        "exercise_id": log.exercise_id,
        "set_no": log.set_no,
        "completed": log.completed,
    }


# =====================================================================
# READ SAVED SET STATES
# =====================================================================


async def get_workout_sets(
    db: AsyncSession,
    *,
    client_id: int,
    month_no: int,
    week_no: int,
    day_id: int,
) -> list[dict[str, Any]]:
    """
    Return saved raw set completion states for one workout day.
    """

    logs = await workout_repo.get_workout_sets(
        db,
        client_id=client_id,
        month_no=month_no,
        week_no=week_no,
        day_id=day_id,
    )

    return [
        {
            "id": log.id,
            "client_id": log.client_id,
            "month_no": log.month_no,
            "week_no": log.week_no,
            "day_id": log.day_id,
            "exercise_id": log.exercise_id,
            "set_no": log.set_no,
            "completed": log.completed,
        }
        for log in logs
    ]