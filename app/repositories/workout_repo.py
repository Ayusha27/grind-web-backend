# app/repositories/workout_repo.py
from collections import defaultdict

from sqlalchemy import distinct, func, nulls_first, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WorkoutDay, WorkoutExercise, WorkoutLog, WorkoutPlan


async def get_active_plan(db: AsyncSession, client_id: int) -> WorkoutPlan | None:
    """PR-02.

    PHP: SELECT * FROM workout_plans
         WHERE client_id = ? AND is_active = 1
         ORDER BY id DESC LIMIT 1
    """
    stmt = (
        select(WorkoutPlan)
        .where(WorkoutPlan.client_id == client_id, WorkoutPlan.is_active.is_(True))
        .order_by(WorkoutPlan.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_days_with_exercises(
    db: AsyncSession, plan_id: int
) -> list[tuple[WorkoutDay, list[WorkoutExercise]]]:
    """PR-04, with the N+1 collapsed.

    PHP issued 1 query for days plus one MORE query per day (Phase 0.2 step 4:
    7 round trips per request, 8,400 queries/s at peak). This is 2 queries
    regardless of day count, and produces byte-identical output because the
    grouping and ordering are preserved exactly.
    """
    day_stmt = (
        select(WorkoutDay)
        .where(WorkoutDay.plan_id == plan_id)
        .order_by(nulls_first(WorkoutDay.day_number.asc()), WorkoutDay.id.asc())
    )
    days = list((await db.execute(day_stmt)).scalars().all())
    if not days:
        return []

    ex_stmt = (
        select(WorkoutExercise)
        .where(WorkoutExercise.day_id.in_([d.id for d in days]))
        .order_by(
            WorkoutExercise.day_id.asc(),
            nulls_first(WorkoutExercise.sort_order.asc()),
            WorkoutExercise.id.asc(),
        )
    )
    # day_id is nullable in the legacy schema, so None is a possible key.
    grouped: dict[int | None, list[WorkoutExercise]] = defaultdict(list)
    for exercise in (await db.execute(ex_stmt)).scalars().all():
        grouped[exercise.day_id].append(exercise)

    return [(day, grouped.get(day.id, [])) for day in days]


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
    """PR-07, PR-08. No existence check, no dedupe — exactly as the PHP."""
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


async def count_all_exercises(db: AsyncSession) -> int:
    """PR-28 denominator.

    PHP: SELECT COUNT(*) FROM workout_exercises   -- GLOBAL, not per client.
    This is a bug (every client's percentage shrinks as other clients' plans
    are imported) and Decision 4 says keep it. It is a sequential scan, so
    Phase 7 caches it for 300 s — keeping the bug then costs nothing.
    """
    return (await db.execute(select(func.count()).select_from(WorkoutExercise))).scalar_one()


async def count_completed_exercises(db: AsyncSession, user_email: str) -> int:
    """PR-29.

    PHP: SELECT COUNT(DISTINCT exercise_id) FROM workout_logs
         WHERE user_email = ? AND completed = 1
    Served by ix_workout_logs_user_completed_ex (Phase 3.2).
    """
    stmt = select(func.count(distinct(WorkoutLog.exercise_id))).where(
        WorkoutLog.user_email == user_email,
        WorkoutLog.completed.is_(True),
    )
    return (await db.execute(stmt)).scalar_one()


# ── admin write paths ──────────────────────────────────────────────

async def get_max_version(db: AsyncSession, client_id: int) -> int:
    """PR-24: COALESCE(MAX(version_no), 0)."""
    stmt = select(func.coalesce(func.max(WorkoutPlan.version_no), 0)).where(
        WorkoutPlan.client_id == client_id
    )
    # COALESCE guarantees a value, but version_no is a nullable column so the
    # inferred type stays int | None. `or 0` states the guarantee explicitly.
    return (await db.execute(stmt)).scalar_one() or 0


async def deactivate_plans(db: AsyncSession, client_id: int) -> None:
    """PR-24: UPDATE workout_plans SET is_active = 0 WHERE client_id = ?"""
    from sqlalchemy import update

    await db.execute(
        update(WorkoutPlan)
        .where(WorkoutPlan.client_id == client_id)
        .values(is_active=False)
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
    """Shared by PR-23 (create-plan) and PR-24 (import).

    When is_active / version_no are None the column defaults apply — which is
    precisely what create-plan.php relied on (it named only three columns).
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

    plan = WorkoutPlan(**values)
    db.add(plan)
    await db.flush()          # assigns plan.id, the PHP lastInsertId()
    return plan


async def insert_day(
    db: AsyncSession, *, plan_id: int, day_number: int, day_name: str | None
) -> WorkoutDay:
    day = WorkoutDay(plan_id=plan_id, day_number=day_number, day_name=day_name)
    db.add(day)
    await db.flush()
    return day


async def insert_exercises(
    db: AsyncSession, *, day_id: int, rows: list[dict[str, object]]
) -> None:
    """Bulk insert. PHP looped one INSERT per exercise; a 5-day plan with 6
    exercises each was 30 round trips. This is one statement, same rows.
    """
    if not rows:
        return
    db.add_all([WorkoutExercise(day_id=day_id, **row) for row in rows])  # type: ignore[arg-type]
    await db.flush()
