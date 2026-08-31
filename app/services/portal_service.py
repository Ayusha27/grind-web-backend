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


async def _progress_percent(db: AsyncSession, user_email: str) -> dict[str, int]:
    """PR-28, PR-29.

    The denominator is COUNT(*) over the ENTIRE workout_exercises table, not
    the client's own plan. Every client's percentage therefore shrinks each
    time any other client's plan is imported. It is a bug; Decision 4 keeps
    it; Phase 7 caches the count so keeping it is free.
    """
    from app.cache.redis import cached_exercise_count

    if settings.LEGACY_GLOBAL_PROGRESS_DENOMINATOR:
        total = await cached_exercise_count(db)
    else:
        total = await cached_exercise_count(db)  # swap for a per-plan count here

    completed = await workout_repo.count_completed_exercises(db, user_email)

    # PHP round() is half-away-from-zero; Python's builtin is not (Phase 2.4).
    percent = php_round_int(completed / total * 100) if total > 0 else 0

    return {"total": total, "completed": completed, "percent": percent}


async def get_my_plan(db: AsyncSession, token: str) -> dict[str, Any]:
    """my-plan.php — the full portal payload."""
    client = await _resolve_client(db, token)
    progress = await _progress_percent(db, client.email or "")

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
    progress = await _progress_percent(db, client.email or "")

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
            "exercises": progress,
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
