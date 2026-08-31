# app/services/plan_service.py
import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_json_is_falsy
from app.core.exceptions import NotFoundError, ValidationFailure
from app.repositories import client_repo, workout_repo

logger = logging.getLogger(__name__)


def _parse_plan_json(raw: str) -> Any:
    """PR-26 — reproduce `json_decode` + `if (!$data)` exactly.

    PHP treats a syntactically VALID document as invalid when it decodes to
    a falsy value: null, false, 0, "", "0", [] and {} all yield "Invalid JSON".
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise ValidationFailure("Invalid JSON") from None

    if php_json_is_falsy(data):
        raise ValidationFailure("Invalid JSON")
    return data


async def create_plan(
    db: AsyncSession, *, client_id: int, plan_name: str, workout_json: str
) -> dict[str, Any]:
    """PR-23, PR-25 — create-plan.php.

    Deliberately DIFFERENT from import (5.6b):
      * does NOT deactivate previous plans -> the client ends up with two
        active plans, and PR-02's `ORDER BY id DESC` silently picks the newer
      * does NOT set version_no -> column default 1
      * the PHP had NO transaction; we add one, because a partial plan
        (days inserted, exercises not) is not a behaviour anyone relied on,
        it is a crash artefact. Successful runs are byte-identical.
    """
    client = await client_repo.get_by_id(db, client_id)
    if client is None:
        raise NotFoundError("Client not found")

    data = _parse_plan_json(workout_json)

    plan = await workout_repo.insert_plan(
        db,
        client_id=client_id,
        plan_name=plan_name,
        workout_json=workout_json,   # RAW string, not re-serialised
    )

    day_count = exercise_count = 0
    # PR-25 — create-plan.php guards `isset($json['days'])`; a plan with no
    # days is accepted and creates just the parent row.
    for index, day in enumerate(data.get("days", []) or []):
        day_row = await workout_repo.insert_day(
            db, plan_id=plan.id, day_number=index + 1, day_name=day.get("day_name")
        )
        day_count += 1

        rows = [
            {
                "exercise_name": ex.get("name"),
                "sets_count": ex.get("sets"),
                "reps": ex.get("reps"),
                "youtube_url": ex.get("youtube"),
                # create-plan.php does NOT name the notes column, so it takes
                # the column default (NULL). import-workout.php writes ''.
                "sort_order": order + 1,
            }
            for order, ex in enumerate(day.get("exercises", []) or [])
        ]
        await workout_repo.insert_exercises(db, day_id=day_row.id, rows=rows)
        exercise_count += len(rows)

    await db.commit()
    logger.info(
        "plan_created",
        extra={"plan_id": plan.id, "client_id": client_id,
               "days": day_count, "exercises": exercise_count},
    )
    return {
        "success": True,
        "message": "Workout Plan Created Successfully",
        "plan_id": plan.id,
        "days": day_count,
        "exercises": exercise_count,
    }


async def import_plan(
    db: AsyncSession, *, client_id: int, workout_json: str
) -> dict[str, Any]:
    """PR-24, PR-25, PR-26 — import-workout.php, the versioned path.

    Entire body runs in ONE transaction, exactly as the PHP
    (beginTransaction / commit / rollBack). If any day or exercise fails,
    nothing is written and the previous plan stays active.
    """
    data = _parse_plan_json(workout_json)

    # PR-24 — the three steps, in this order. Reordering them would leave a
    # window where the client has no active plan.
    next_version = (await workout_repo.get_max_version(db, client_id)) + 1
    await workout_repo.deactivate_plans(db, client_id)

    plan = await workout_repo.insert_plan(
        db,
        client_id=client_id,
        plan_name=data.get("plan_name"),
        workout_json=workout_json,   # RAW string
        is_active=True,
        version_no=next_version,
    )

    day_count = exercise_count = 0
    # PR-25 — import-workout.php indexes $data['days'] WITHOUT isset(), so a
    # payload lacking "days" was a PHP warning + empty loop. `or []` matches.
    for index, day in enumerate(data.get("days", []) or []):
        day_row = await workout_repo.insert_day(
            db, plan_id=plan.id, day_number=index + 1, day_name=day.get("day_name")
        )
        day_count += 1

        rows = [
            {
                "exercise_name": ex.get("name"),
                "sets_count": ex.get("sets"),
                "reps": ex.get("reps"),
                "youtube_url": ex.get("youtube"),
                # PR-25 — import writes the LITERAL empty string, not NULL.
                # my-plan surfaces this as the exercise "note" field, so the
                # difference is visible to the client. Keep it.
                "notes": "",
                "sort_order": order + 1,
            }
            for order, ex in enumerate(day.get("exercises", []) or [])
        ]
        await workout_repo.insert_exercises(db, day_id=day_row.id, rows=rows)
        exercise_count += len(rows)

    await db.commit()
    logger.info(
        "plan_imported",
        extra={"plan_id": plan.id, "client_id": client_id, "version": next_version,
               "days": day_count, "exercises": exercise_count},
    )
    return {
        "success": True,
        "message": "Workout Imported Successfully",
        "plan_id": plan.id,
        "version_no": next_version,
        "days": day_count,
        "exercises": exercise_count,
    }
