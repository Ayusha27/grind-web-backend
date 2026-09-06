# app/services/portal_service.py
import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_round_int
from app.core.config import settings
from app.core.exceptions import ValidationFailure
from app.repositories import client_repo, diet_repo, workout_repo

logger = logging.getLogger(__name__)

# PR-31 — the exact palette from my-plan.php, in order. The index is
# (day_number - 1) % 6, so day 7 wraps back to the first colour.
DAY_COLORS: tuple[str, ...] = (
    "#ff5c35", "#2563eb", "#16a34a", "#9333ea", "#ea580c", "#0f766e",
)


async def _resolve_client(db: AsyncSession, token: str):
    """Decision 5 — my-plan.php hardcoded a single email and
    workout-progress.php hardcoded client_id = 1. Neither can survive in a
    multi-client API, so both now derive the identity from the caller's
    access token. Recorded in PARITY.md.
    """
    client = await client_repo.get_by_access_token(db, (token or "").strip())
    if client is None:
        # PHP: die("Invalid Access Link")
        raise ValidationFailure("Invalid Access Link")
    return client


# async def _progress_percent(db: AsyncSession, user_email: str) -> dict[str, int]:
#     """PR-28, PR-29.

#     The denominator is COUNT(*) over the ENTIRE workout_exercises table, not
#     the client's own plan. Every client's percentage therefore shrinks each
#     time any other client's plan is imported. It is a bug; Decision 4 keeps
#     it; Phase 7 caches the count so keeping it is free.
#     """
#     from app.cache.redis import cached_exercise_count

#     if settings.LEGACY_GLOBAL_PROGRESS_DENOMINATOR:
#         total = await cached_exercise_count(db)
#     else:
#         total = await cached_exercise_count(db)  # swap for a per-plan count here

#     completed = await workout_repo.count_completed_exercises(db, user_email)

#     # PHP round() is half-away-from-zero; Python's builtin is not (Phase 2.4).
#     percent = php_round_int(completed / total * 100) if total > 0 else 0

#     return {"total": total, "completed": completed, "percent": percent}


def _session_denominators(plan_days: int) -> tuple[int, int, int]:
    """(workouts per week, weeks per month, sessions per month).

    The tracker counts SESSIONS — one completed workout day is one session —
    so a 4-week month of a 5-day plan is 20 sessions. The per-week figure comes
    from the client's own active plan; the configured default only fills in for
    a client who has no plan days yet.
    """
    per_week = plan_days or settings.WORKOUT_DEFAULT_WORKOUTS_PER_WEEK
    weeks = settings.WORKOUT_WEEKS_PER_MONTH
    return per_week, weeks, per_week * weeks


def _percent(part: int, whole: int) -> int:
    """Clamped percentage. PHP round() is half-away-from-zero (Phase 2.4)."""
    if whole <= 0:
        return 0
    return php_round_int(min(part, whole) / whole * 100)


async def _workout_progress(db: AsyncSession, client_id: int) -> dict[str, Any]:
    """Session-based progress for the tracker.

    Every summary row in workout_logs is one logged workout DAY. The four
    tracker slots are:

      1. sessions completed / sessions in the month  (1/20 = 5%)
      2. calories burned, and calories / completed sessions
      3. same pair, exposed under `overall` for the whole plan to date
      4. weeks touched / weeks in the month, and the best week's score, where
         a week in which all `per_week` workouts were done scores 100%

    Set counts are still returned, under `sets`, because they are what the
    logging endpoint writes — they are just no longer what the tracker shows.
    """
    logs = await workout_repo.get_workout_summary_details(db, client_id)
    plan_days = await workout_repo.count_active_plan_days(db, client_id)
    per_week, weeks_per_month, per_month = _session_denominators(plan_days)

    weekly_detail: dict[str, dict[str, dict[str, Any]]] = {}
    total_sets = 0
    completed_sets = 0

    for log in logs:
        month_key = str(log.month_no)
        week_key = str(log.week_no)
        day_key = str(log.day_id)

        row_total = int(log.total_sets or 0)
        row_done = int(log.completed_sets or 0)
        total_sets += row_total
        completed_sets += row_done

        if log.completion_percent is not None:
            row_percent = float(log.completion_percent)
        elif row_total > 0:
            row_percent = row_done / row_total * 100
        else:
            row_percent = 0.0

        day = (
            weekly_detail
            .setdefault(month_key, {})
            .setdefault(week_key, {})
            .setdefault(
                day_key,
                {"completed": False, "completion_percent": 0.0, "calories_burned": 0},
            )
        )
        # A day can hold more than one row if the client re-logged it. The work
        # done adds up, but the day is still ONE session, and the best attempt
        # decides whether that session counts as finished.
        day["calories_burned"] += int(log.calories_burned or 0)
        day["completion_percent"] = max(day["completion_percent"], row_percent)
        day["completed"] = day["completed"] or (row_done >= row_total and row_total > 0)

    months: dict[str, dict[str, Any]] = {}

    for month_key, month_data in weekly_detail.items():
        sessions_completed = 0
        sessions_logged = 0
        calories = 0
        weeks_completed = 0
        weeks_logged = 0
        best_week_score = 0

        for week_data in month_data.values():
            week_completed = sum(1 for d in week_data.values() if d["completed"])
            week_logged = sum(
                1 for d in week_data.values() if d["completion_percent"] > 0
            )
            calories += sum(d["calories_burned"] for d in week_data.values())

            sessions_completed += week_completed
            sessions_logged += week_logged
            weeks_completed += 1 if week_completed > 0 else 0
            weeks_logged += 1 if week_logged > 0 else 0
            best_week_score = max(best_week_score, _percent(week_completed, per_week))

        months[month_key] = {
            "month_no": int(month_key),
            # slot 1
            "sessions_completed": sessions_completed,
            "sessions_logged": sessions_logged,
            "sessions_total": per_month,
            "percent": _percent(sessions_completed, per_month),
            # slots 2 and 3
            "calories_burned": calories,
            "avg_calories_per_session": (
                php_round_int(calories / sessions_completed)
                if sessions_completed
                else 0
            ),
            # slot 4
            "weeks_completed": weeks_completed,
            "weeks_logged": weeks_logged,
            "weeks_total": weeks_per_month,
            "best_week_score": best_week_score,
        }

    # The month the tracker opens on: the newest one with any logs.
    active_month_no = max((int(k) for k in months), default=1)
    active = months.get(
        str(active_month_no),
        {
            "month_no": active_month_no,
            "sessions_completed": 0,
            "sessions_logged": 0,
            "sessions_total": per_month,
            "percent": 0,
            "calories_burned": 0,
            "avg_calories_per_session": 0,
            "weeks_completed": 0,
            "weeks_logged": 0,
            "weeks_total": weeks_per_month,
            "best_week_score": 0,
        },
    )

    overall_sessions = sum(m["sessions_completed"] for m in months.values())
    overall_calories = sum(m["calories_burned"] for m in months.values())
    # Months elapsed, not months logged: a client who skipped month 2 entirely
    # is still three months into the plan by month 3.
    months_span = max((int(k) for k in months), default=0)
    overall_total = per_month * months_span

    overall = {
        "sessions_completed": overall_sessions,
        "sessions_total": overall_total,
        "percent": _percent(overall_sessions, overall_total),
        "calories_burned": overall_calories,
        "avg_calories_per_session": (
            php_round_int(overall_calories / overall_sessions)
            if overall_sessions
            else 0
        ),
        "months_tracked": months_span,
    }

    return {
        "plan": {
            "workouts_per_week": per_week,
            "weeks_per_month": weeks_per_month,
            "sessions_per_month": per_month,
        },
        "month": active,
        "months": months,
        "overall": overall,
        "sets": {
            "total": total_sets,
            "completed": completed_sets,
            "percent": _percent(completed_sets, total_sets),
        },
        "weekly_detail": weekly_detail,
    }


async def _progress_percent(db: AsyncSession, client_id: int) -> dict[str, int]:
    """The my-plan.php progress block, in the tracker's session terms.

    Same key names the page already reads — `total`, `completed`, `percent` —
    but they now count sessions out of the month's 20, not sets out of every
    set ever logged.
    """
    progress = await _workout_progress(db, client_id)
    month = progress["month"]

    return {
        "total": month["sessions_total"],
        "completed": month["sessions_completed"],
        "percent": month["percent"],
        "calories_burned": month["calories_burned"],
    }

async def get_my_plan(db: AsyncSession, token: str) -> dict[str, Any]:
    """my-plan.php — the full portal payload."""
    client = await _resolve_client(db, token)
    progress = await _progress_percent(db, client.id)

    plan = await workout_repo.get_active_plan(db, client.id)
    days_payload: list[dict[str, Any]] = []

    if plan is not None:
        for day, exercises in await workout_repo.get_days_with_exercises(db, plan.id):
            day_number = day.day_number or 0
            days_payload.append(
                {
                    # PR-30 — key names and constants are what the frontend
                    # JS reads. `id` is the DAY NUMBER, not the row id, and
                    # `label` and `short` are both day_name.
                    "id": int(day_number),
                    "label": day.day_name,
                    "short": day.day_name,
                    "color": DAY_COLORS[(day_number - 1) % len(DAY_COLORS)],  # PR-31
                    "colorSoft": "rgba(255,92,53,.1)",
                    "calMin": 250,
                    "calMax": 350,
                    "calNote": "Workout Day",
                    "exercises": [
                        {
                            "name": ex.exercise_name,
                            "sets": int(ex.sets_count or 0),
                            "reps": ex.reps,
                            "note": ex.notes,
                            "yt": ex.youtube_url,
                        }
                        for ex in exercises
                    ],
                }
            )

    diet_plan = await diet_repo.get_active_plan(db, client.id)
    diet_data: Any = []
    if diet_plan is not None and diet_plan.diet_json:
        try:
            diet_data = json.loads(diet_plan.diet_json)
        except json.JSONDecodeError:
            # PHP json_decode returns null on bad JSON and the page rendered
            # an empty diet rather than erroring.
            logger.warning("diet_json_invalid", extra={"diet_plan_id": diet_plan.id})
            diet_data = []

    return {
        "success": True,
        "data": {
            "client": {"id": client.id, "name": client.name, "goal": client.goal},
            "plan_name": plan.plan_name if plan else None,
            "days": days_payload,
            "diet": diet_data,
            "progress": progress,
        },
    }


def _f(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


async def get_progress(db: AsyncSession, token: str) -> dict[str, Any]:
    """workout-progress.php — PR-32."""
    client = await _resolve_client(db, token)
    progress = await _workout_progress(db, client.id)
    month = progress["month"]

    history = await client_repo.get_progress_history(db, client.id)

    # PR-32 — `start` is the OLDEST row, `current` the NEWEST. Both stats are
    # 0 unless both exist (a single row means start is current, so the deltas
    # are 0 anyway, which the PHP also produced).
    start = history[0] if history else None
    current = history[-1] if history else None

    weight_lost = 0.0
    waist_reduced = 0.0
    if start is not None and current is not None:
        if start.weight is not None and current.weight is not None:
            weight_lost = float(start.weight - current.weight)
        if start.waist is not None and current.waist is not None:
            waist_reduced = float(start.waist - current.waist)

    return {
        "success": True,
        "data": {
            # Slot 1. The key stays `exercises` because that is what the
            # tracker reads, but the numbers are SESSIONS out of the month's
            # 20 now — one finished workout day is 1/20, i.e. 5%.
            "exercises": {
                "total": month["sessions_total"],
                "completed": month["sessions_completed"],
                "percent": month["percent"],
            },
            # Slots 2 and 3.
            "calories_burned": month["calories_burned"],
            "avg_calories_per_session": month["avg_calories_per_session"],
            # Slot 4 — weeks touched this month, and the best week's score.
            "active_weeks": month["weeks_completed"],
            "weeks_total": month["weeks_total"],
            "best_week_score": month["best_week_score"],
            "weekly_detail": progress["weekly_detail"],
            # Everything above is the active month. These carry the rest.
            "plan": progress["plan"],
            "month": month,
            "months": progress["months"],
            "overall": progress["overall"],
            "sets": progress["sets"],
            "current": {
                "weight": _f(current.weight), "waist": _f(current.waist),
                "chest": _f(current.chest), "arms": _f(current.arms),
                "thighs": _f(current.thighs),
            }
            if current
            else None,
            "transformation": {
                "weight_lost": weight_lost,
                "waist_reduced": waist_reduced,
            },
            "chart": {
                # PHP: date('d M', strtotime($row['created_at'])) -> "15 Jun"
                "dates": [r.created_at.strftime("%d %b") for r in history],
                "weights": [_f(r.weight) for r in history],
                "waists": [_f(r.waist) for r in history],
            },
        },
    }
