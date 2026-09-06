# app/services/workout_service.py
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_intval, php_trim
from app.core.config import settings
from app.core.exceptions import ValidationFailure
from app.db.models import WorkoutDay, WorkoutExercise, WorkoutPlan
from app.repositories import workout_repo
from app.schemas.workout import WorkoutLogCreate
from app.schemas.workout import (
    WorkoutSetLogCreate,
)

logger = logging.getLogger(__name__)


def _plan_dict(plan: WorkoutPlan) -> dict[str, Any]:
    """PHP `SELECT *` returned every column. Key order matches the table."""
    return {
        "id": plan.id,
        "client_id": plan.client_id,
        "plan_name": plan.plan_name,
        # MySQL tinyint(1) serialised as 0/1 through json_encode, and
        # workoutService.ts types is_active as `number`. Emitting `true`
        # would be a contract change for the frontend.
        "is_active": int(bool(plan.is_active)),
        "created_at": plan.created_at.strftime("%Y-%m-%d %H:%M:%S") if plan.created_at else None,
        "workout_json": plan.workout_json,
        "version_no": plan.version_no,
    }


def _exercise_dict(ex: WorkoutExercise) -> dict[str, Any]:
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


def _day_dict(day: WorkoutDay, exercises: list[WorkoutExercise]) -> dict[str, Any]:
    return {
        "id": day.id,
        "plan_id": day.plan_id,
        "day_number": day.day_number,
        "day_name": day.day_name,
        "exercises": [_exercise_dict(e) for e in exercises],
    }


async def get_workout(db: AsyncSession, raw_client_id: Any) -> dict[str, Any]:
    """PR-01 .. PR-05 — api/workout.php.

    PR-01: absent  -> LEGACY_DEFAULT_CLIENT_ID (1)
           "abc"   -> 0 via PHP (int) cast, NOT a 422
    PR-03: no plan -> HTTP 200 with data:null and the exact legacy message.
    """
    if raw_client_id is None or raw_client_id == "":
        client_id = settings.LEGACY_DEFAULT_CLIENT_ID
    else:
        client_id = php_intval(raw_client_id)

    plan = await workout_repo.get_active_plan(db, client_id)

    if plan is None:
        # Key order is exactly json_encode(['success','data','message']).
        return {"success": True, "data": None, "message": "No workout plan assigned."}

    days = await workout_repo.get_days_with_exercises(db, plan.id)

    # The success branch has NO "message" key in the PHP. Do not add one.
    return {
        "success": True,
        "data": {
            "plan": _plan_dict(plan),
            "days": [_day_dict(day, exercises) for day, exercises in days],
        },
    }


async def complete_workout(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """PR-06 .. PR-09 — api/complete-workout.php."""
    exercise_id = php_intval(payload.get("exercise_id"))
    day_id = php_intval(payload.get("day_id"))
    user_email = php_trim(payload.get("user_email"))

    # PR-06 — message text is byte-for-byte what the PHP returned.
    if exercise_id <= 0 or day_id <= 0 or user_email == "":
        raise ValidationFailure("Missing required fields")

    # PR-07 — month_no, week_no, set_no and completed are hardcoded in the
    # PHP INSERT. They are NOT read from the request even if supplied.
    # PR-08 — no FK check, no dedupe: calling twice inserts two rows.
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

    return {"success": True, "message": "Workout marked complete"}


async def save_log(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """Base files/save-progress.php.

    The original is DEAD CODE: it does `require 'config.php'`, and no such
    file exists anywhere in the repository, so every call is a PHP fatal.
    There is therefore no behaviour to preserve — only intent. The intent
    (a full-fidelity log write with month/week/set from the caller) is
    implemented here with the validation the original lacked.
    """
    user_email = php_trim(payload.get("email"))
    if user_email == "":
        raise ValidationFailure("Missing required fields")

    await workout_repo.insert_log(
        db,
        user_email=user_email,
        month_no=php_intval(payload.get("month")) or 1,
        week_no=php_intval(payload.get("week")) or 1,
        day_id=php_intval(payload.get("day")),
        exercise_id=php_intval(payload.get("exercise")),
        set_no=php_intval(payload.get("set")) or 1,
        completed=bool(php_intval(payload.get("completed"))),
    )
    await db.commit()
    return {"success": True}

async def save_workout_summary(
    db: AsyncSession,
    payload: WorkoutLogCreate,
) -> dict[str, Any]:
    """Save one workout summary and all individual set states."""

    if payload.completed_sets > payload.total_sets:
        raise ValidationFailure(
            "Completed sets cannot exceed total sets"
        )

    if payload.total_sets == 0:
        calculated_percent = 0.0
    else:
        calculated_percent = (
            payload.completed_sets /
            payload.total_sets
        ) * 100

    calculated_percent = round(
        calculated_percent,
        2,
    )

    # ---------------------------------------------------------
    # Save individual set states
    # ---------------------------------------------------------

    set_logs = await workout_repo.upsert_workout_sets(
        db,
        client_id=payload.client_id,
        month_no=payload.month_no,
        week_no=payload.week_no,
        day_id=payload.day_id,
        sets=payload.sets,
    )

    # ---------------------------------------------------------
    # Save workout summary
    # ---------------------------------------------------------

    log = await workout_repo.insert_workout_summary(
        db,
        client_id=payload.client_id,
        month_no=payload.month_no,
        week_no=payload.week_no,
        day_id=payload.day_id,
        total_sets=payload.total_sets,
        completed_sets=payload.completed_sets,
        completion_percent=calculated_percent,
        calories_burned=payload.calories_burned,
    )

    # ---------------------------------------------------------
    # One transaction
    # ---------------------------------------------------------

    await db.commit()

    return {
        "success": True,
        "message": "Workout logged successfully",
        "log_id": log.id,
        "sets_logged": len(set_logs),
    }


async def save_workout_set(
    db: AsyncSession,
    *,
    client_id: int,
    payload: WorkoutSetLogCreate,
) -> dict[str, Any]:
    """Save or update one workout set completion state."""

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

async def get_workout_sets(
    db: AsyncSession,
    *,
    client_id: int,
    month_no: int,
    week_no: int,
    day_id: int,
) -> list[dict[str, Any]]:
    """Return saved set completion states for one workout day."""

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