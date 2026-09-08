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


# =====================================================================
# CONSTANTS
# =====================================================================

# PR-31 — exact palette from my-plan.php.
DAY_COLORS: tuple[str, ...] = (
    "#ff5c35",
    "#2563eb",
    "#16a34a",
    "#9333ea",
    "#ea580c",
    "#0f766e",
)

# Current migrated workout calorie range.
WORKOUT_CAL_MIN = 250
WORKOUT_CAL_MAX = 350


# =====================================================================
# CLIENT
# =====================================================================


async def _resolve_client(
    db: AsyncSession,
    token: str,
):
    """
    Resolve the client from the access token.

    The original PHP implementation used a hardcoded client/email.
    The migrated implementation uses the caller's access token instead.
    """

    client = await client_repo.get_by_access_token(
        db,
        (token or "").strip(),
    )

    if client is None:
        raise ValidationFailure(
            "Invalid Access Link"
        )

    return client


# =====================================================================
# SESSION / PROGRAM DENOMINATORS
# =====================================================================


def _session_denominators(
    plan_days: int,
) -> tuple[int, int, int]:
    """
    Return:

        workouts per week
        weeks per month
        sessions per month

    The GRIND program uses:

        5 workouts/week
        4 weeks/month
        20 sessions/month

    If the client's active plan has a different number of workout days,
    the actual plan count is used.
    """

    per_week = (
        plan_days
        or settings.WORKOUT_DEFAULT_WORKOUTS_PER_WEEK
    )

    weeks = settings.WORKOUT_WEEKS_PER_MONTH

    return (
        per_week,
        weeks,
        per_week * weeks,
    )


def _percent(
    part: int,
    whole: int,
) -> int:
    """
    PHP-compatible percentage.

    Values are clamped so the numerator cannot exceed the denominator.
    """

    if whole <= 0:
        return 0

    return php_round_int(
        min(
            max(part, 0),
            whole,
        )
        / whole
        * 100
    )


# =====================================================================
# WORKOUT DAY CALCULATIONS
# =====================================================================


def _calculate_day_calories(
    completed_sets: int,
    total_sets: int,
) -> int:
    """
    PHP workout calorie formula.

    r = completed_sets / total_sets

    calories =
        round(
            (calMin + (calMax - calMin) * r) * r
        )

    With the current 250-350 range:

        0 / 30  -> 0 kcal
        15 / 30 -> 150 kcal
        30 / 30 -> 350 kcal
    """

    if (
        total_sets <= 0
        or completed_sets <= 0
    ):
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
        WORKOUT_CAL_MIN
        + (
            WORKOUT_CAL_MAX
            - WORKOUT_CAL_MIN
        )
        * ratio
    ) * ratio

    return php_round_int(
        calories
    )


def _day_from_summary(
    log,
) -> dict[str, Any]:
    """
    Convert one WorkoutLog summary row into the Progress day structure.

    A workout day is:

        logged      -> completion_percent > 0
        completed   -> 100% / completed_sets == total_sets
    """

    total_sets = int(
        log.total_sets or 0
    )

    completed_sets = int(
        log.completed_sets or 0
    )

    if total_sets > 0:
        completion_percent = round(
            (
                completed_sets
                / total_sets
                * 100
            ),
            2,
        )
    else:
        completion_percent = 0.0

    # Do not trust a stale stored calorie value for Progress.
    # Recalculate using the PHP formula.
    calories_burned = _calculate_day_calories(
        completed_sets,
        total_sets,
    )

    return {
        "completed": (
            total_sets > 0
            and completed_sets >= total_sets
        ),
        # The existence of a workout summary means the session was
        # attended/logged. A session may therefore be logged even when
        # completed_sets == 0 or the workout is only partially complete.
        "logged": True,
        "completion_percent": completion_percent,
        "calories_burned": calories_burned,
        "total_sets": total_sets,
        "completed_sets": completed_sets,
    }


# =====================================================================
# SUMMARY DEDUPLICATION
# =====================================================================


def _latest_summary_per_day(
    logs: list[Any],
) -> dict[
    tuple[int, int, int],
    Any,
]:
    """
    Collapse old duplicate WorkoutLog summary rows.

    Logical identity:

        month_no + week_no + day_id

    The repository orders rows by ID ascending, so the final row encountered
    for a key is the newest summary.

    This is also safe for databases containing duplicate rows created by the
    earlier implementation.
    """

    latest: dict[
        tuple[int, int, int],
        Any,
    ] = {}

    for log in logs:
        key = (
            int(log.month_no),
            int(log.week_no),
            int(log.day_id),
        )

        latest[key] = log

    return latest


# =====================================================================
# WORKOUT PROGRESS
# =====================================================================


async def _workout_progress(
    db: AsyncSession,
    client_id: int,
) -> dict[str, Any]:
    """
    Build the complete workout Progress data.

    Hierarchy:

        Day
          ↓
        Week
          ↓
        Month
          ↓
        3-month overview
          ↓
        Overall program

    Important PHP semantics:

        completion > 0%
            = workout/session was logged

        completion == 100%
            = workout/session completed

        weekly score
            = logged sessions / planned sessions × 100

        A session is logged/attended when a workout summary exists.
        It does not need to be 100% complete.

        active week
            = week with any logged workout

        best week
            = highest weekly attendance score
    """

    logs = await workout_repo.get_workout_summary_details(
        db,
        client_id,
    )

    plan_days = await workout_repo.count_active_plan_days(
        db,
        client_id,
    )

    (
        per_week,
        weeks_per_month,
        per_month,
    ) = _session_denominators(
        plan_days
    )

    # -------------------------------------------------------------
    # Planned sets for one complete month.
    #
    # The Progress Tracker's monthly Sets Done denominator must come
    # from the active workout plan, not from the sessions the client
    # has already logged.
    #
    # Example:
    #
    #   planned sets in one week = 139 / 4
    #   planned sets in one month = 139
    #
    # Logged sessions may currently contain only:
    #
    #   Day 1 -> 30 sets
    #   Day 2 -> 27 sets
    #
    # but Month 1 must still show:
    #
    #   56 completed / 139 planned
    #
    # rather than:
    #
    #   56 / 57
    # -------------------------------------------------------------

    active_plan = await workout_repo.get_active_plan(
        db,
        client_id,
    )

    planned_sets_per_week = 0

    if active_plan is not None:
        plan_day_rows = (
            await workout_repo.get_days_with_exercises(
                db,
                active_plan.id,
            )
        )

        planned_sets_per_week = sum(
            max(
                0,
                int(
                    exercise.sets_count
                    or 0
                ),
            )
            for _, exercises in plan_day_rows
            for exercise in exercises
        )

    planned_sets_per_month = (
        planned_sets_per_week
        * weeks_per_month
    )

    # -------------------------------------------------------------
    # Remove duplicate summary rows.
    # -------------------------------------------------------------

    latest_logs = _latest_summary_per_day(
        logs
    )

    # -------------------------------------------------------------
    # Month → Week → Day
    # -------------------------------------------------------------

    weekly_detail: dict[
        str,
        dict[
            str,
            dict[
                str,
                dict[str, Any],
            ],
        ],
    ] = {}

    total_sets = 0
    completed_sets = 0

    for log in latest_logs.values():

        month_key = str(
            log.month_no
        )

        week_key = str(
            log.week_no
        )

        day_key = str(
            log.day_id
        )

        day = _day_from_summary(
            log
        )

        # Overall set totals are based on the latest day summary,
        # not duplicate historical submissions.
        total_sets += day["total_sets"]
        completed_sets += day["completed_sets"]

        weekly_detail.setdefault(
            month_key,
            {},
        ).setdefault(
            week_key,
            {},
        )[day_key] = {
            "completed": day["completed"],
            "logged": day["logged"],
            "completion_percent": day[
                "completion_percent"
            ],
            "calories_burned": day[
                "calories_burned"
            ],
            "total_sets": day[
                "total_sets"
            ],
            "completed_sets": day[
                "completed_sets"
            ],
        }

    # -------------------------------------------------------------
    # Always expose M1/M2/M3.
    #
    # This allows the frontend to render the complete 3-month overview
    # even when no workout has been logged for M2/M3 yet.
    # -------------------------------------------------------------

    months: dict[
        str,
        dict[str, Any],
    ] = {}

    for month_no in range(1, 4):

        month_key = str(
            month_no
        )

        month_data = weekly_detail.get(
            month_key,
            {},
        )

        sessions_completed = 0
        sessions_logged = 0
        calories = 0
        month_total_sets = 0
        month_completed_sets = 0

        active_weeks = 0
        completed_weeks = 0

        best_week_score = 0

        # ---------------------------------------------------------
        # Weeks 1 → 4
        # ---------------------------------------------------------

        for week_no in range(
            1,
            weeks_per_month + 1,
        ):

            week_key = str(
                week_no
            )

            week_data = month_data.get(
                week_key,
                {},
            )

            week_completed = sum(
                1
                for day in week_data.values()
                if day["completed"]
            )

            week_logged = sum(
                1
                for day in week_data.values()
                if day["logged"]
            )

            week_calories = sum(
                int(
                    day["calories_burned"]
                )
                for day in week_data.values()
            )

            week_total_sets = sum(
                int(day["total_sets"])
                for day in week_data.values()
            )

            week_completed_sets = sum(
                int(day["completed_sets"])
                for day in week_data.values()
            )

            sessions_completed += (
                week_completed
            )

            sessions_logged += (
                week_logged
            )

            calories += (
                week_calories
            )

            month_total_sets += (
                week_total_sets
            )

            month_completed_sets += (
                week_completed_sets
            )

            # -----------------------------------------------------
            # IMPORTANT:
            #
            # Active/logged week means ANY activity.
            # It does not require 100% completion.
            # -----------------------------------------------------

            if week_logged > 0:
                active_weeks += 1

            if week_completed > 0:
                completed_weeks += 1

            # Weekly score measures attendance/logged sessions, not
            # 100% workout completion.
            week_score = _percent(
                week_logged,
                per_week,
            )

            best_week_score = max(
                best_week_score,
                week_score,
            )

        # ---------------------------------------------------------
        # Monthly calculations
        # ---------------------------------------------------------

        # Month score measures attended/logged sessions.
        # A partially completed workout still counts as one attended
        # session.
        month_score = _percent(
            sessions_logged,
            per_month,
        )

        average_calories = (
            php_round_int(
                calories
                / sessions_logged
            )
            if sessions_logged > 0
            else 0
        )

        months[month_key] = {
            "month_no": month_no,

            # -----------------------------------------------------
            # Sessions
            # -----------------------------------------------------

            "sessions_completed": (
                sessions_completed
            ),

            "sessions_logged": (
                sessions_logged
            ),

            "sessions_total": (
                per_month
            ),

            "percent": (
                month_score
            ),

            # -----------------------------------------------------
            # Sets logged within this month.
            # The denominator used by the UI remains the planned
            # sets from the active plan, while this value tracks
            # actual completed sets.
            # -----------------------------------------------------

            # IMPORTANT:
            # The denominator is the COMPLETE planned month,
            # regardless of how many sessions have been logged.
            "sets_total": (
                planned_sets_per_month
            ),

            # Numerator is the actual completed sets from logged
            # sessions in this month.
            "sets_completed": (
                month_completed_sets
            ),

            # -----------------------------------------------------
            # Calories
            # -----------------------------------------------------

            "calories_burned": (
                calories
            ),

            "avg_calories_per_session": (
                average_calories
            ),

            # -----------------------------------------------------
            # Weeks
            # -----------------------------------------------------

            "active_weeks": (
                active_weeks
            ),

            "weeks_logged": (
                active_weeks
            ),

            "weeks_completed": (
                completed_weeks
            ),

            "weeks_total": (
                weeks_per_month
            ),

            "best_week_score": (
                best_week_score
            ),

            # -----------------------------------------------------
            # Three-month overview values.
            # -----------------------------------------------------

            # A workout/session counts as attended when it is logged.
            "workouts": (
                sessions_logged
            ),

            "calories": (
                calories
            ),

            "score": (
                month_score
            ),
        }

    # =================================================================
    # ACTIVE MONTH
    # =================================================================

    # The tracker should open on the newest month containing workout
    # activity. If there is no activity, default to M1.

    months_with_activity = [
        month_no
        for month_no in range(1, 4)
        if (
            months[str(month_no)][
                "sessions_logged"
            ] > 0
        )
    ]

    active_month_no = (
        max(
            months_with_activity
        )
        if months_with_activity
        else 1
    )

    active = months[
        str(active_month_no)
    ]

    # =================================================================
    # OVERALL PROGRAM
    # =================================================================

    overall_sessions = sum(
        month[
            "sessions_completed"
        ]
        for month in months.values()
    )

    overall_logged_sessions = sum(
        month[
            "sessions_logged"
        ]
        for month in months.values()
    )

    overall_calories = sum(
        month[
            "calories_burned"
        ]
        for month in months.values()
    )

    # The plan has three months, not merely the months in which data exists.
    overall_total = (
        per_month * 3
    )

    # Overall program percentage follows the same attendance rule:
    # logged sessions / planned sessions.
    overall_percent = _percent(
        overall_logged_sessions,
        overall_total,
    )

    overall_average_calories = (
        php_round_int(
            overall_calories
            / overall_logged_sessions
        )
        if overall_logged_sessions > 0
        else 0
    )

    overall_active_weeks = sum(
        month[
            "active_weeks"
        ]
        for month in months.values()
    )

    overall_best_week_score = max(
        (
            month[
                "best_week_score"
            ]
            for month in months.values()
        ),
        default=0,
    )

    overall = {
        "sessions_completed": (
            overall_sessions
        ),

        "sessions_logged": (
            overall_logged_sessions
        ),

        "sessions_total": (
            overall_total
        ),

        "percent": (
            overall_percent
        ),

        "calories_burned": (
            overall_calories
        ),

        "avg_calories_per_session": (
            overall_average_calories
        ),

        "active_weeks": (
            overall_active_weeks
        ),

        "best_week_score": (
            overall_best_week_score
        ),

        "months_tracked": 3,
    }

    # =================================================================
    # RESULT
    # =================================================================

    return {
        "plan": {
            "workouts_per_week": (
                per_week
            ),
            "weeks_per_month": (
                weeks_per_month
            ),
            "sessions_per_month": (
                per_month
            ),
            "months": 3,
            "sessions_total": (
                overall_total
            ),
        },

        "month": active,

        "months": months,

        "overall": overall,

        "sets": {
            "total": total_sets,
            "completed": completed_sets,
            "percent": _percent(
                completed_sets,
                total_sets,
            ),
        },

        "weekly_detail": weekly_detail,
    }


# =====================================================================
# DASHBOARD PROGRESS SUMMARY
# =====================================================================


async def _progress_percent(
    db: AsyncSession,
    client_id: int,
) -> dict[str, int]:
    """
    Return the workout progress block used by /my-plan.

    The migrated tracker uses sessions rather than raw set counts.

    For a five-day plan:

        1 completed workout
        -------------------
        20 planned sessions

        = 5%
    """

    progress = await _workout_progress(
        db,
        client_id,
    )

    month = progress[
        "month"
    ]

    return {
        "total": int(
            month[
                "sessions_total"
            ]
        ),

        "completed": int(
            month[
                "sessions_completed"
            ]
        ),

        "percent": int(
            month[
                "percent"
            ]
        ),

        "calories_burned": int(
            month[
                "calories_burned"
            ]
        ),
    }


# =====================================================================
# MY PLAN
# =====================================================================


async def get_my_plan(
    db: AsyncSession,
    token: str,
) -> dict[str, Any]:
    """
    my-plan.php — full portal payload.
    """

    client = await _resolve_client(
        db,
        token,
    )

    progress = await _progress_percent(
        db,
        client.id,
    )

    plan = await workout_repo.get_active_plan(
        db,
        client.id,
    )

    days_payload: list[
        dict[str, Any]
    ] = []

    if plan is not None:

        for (
            day,
            exercises,
        ) in await workout_repo.get_days_with_exercises(
            db,
            plan.id,
        ):

            day_number = (
                day.day_number or 0
            )

            days_payload.append(
                {
                    # IMPORTANT:
                    # The frontend uses the day number, not WorkoutDay.id.
                    "id": int(
                        day_number
                    ),

                    "label": (
                        day.day_name
                    ),

                    "short": (
                        day.day_name
                    ),

                    "color": DAY_COLORS[
                        (
                            day_number - 1
                        )
                        % len(DAY_COLORS)
                    ],

                    "colorSoft": (
                        "rgba(255,92,53,.1)"
                    ),

                    "calMin": (
                        WORKOUT_CAL_MIN
                    ),

                    "calMax": (
                        WORKOUT_CAL_MAX
                    ),

                    "calNote": (
                        "Workout Day"
                    ),

                    "exercises": [
                        {
                            "id": ex.id,
                            "name": (
                                ex.exercise_name
                            ),
                            "sets": int(
                                ex.sets_count
                                or 0
                            ),
                            "reps": ex.reps,
                            "note": ex.notes,
                            "yt": (
                                ex.youtube_url
                            ),
                        }
                        for ex in exercises
                    ],
                }
            )

    # =================================================================
    # DIET
    # =================================================================

    diet_plan = await diet_repo.get_active_plan(
        db,
        client.id,
    )

    diet_data: Any = []

    if (
        diet_plan is not None
        and diet_plan.diet_json
    ):
        try:
            diet_data = json.loads(
                diet_plan.diet_json
            )

        except json.JSONDecodeError:
            logger.warning(
                "diet_json_invalid",
                extra={
                    "diet_plan_id": (
                        diet_plan.id
                    )
                },
            )

            diet_data = []

    # =================================================================
    # RESPONSE
    # =================================================================

    return {
        "success": True,

        "data": {
            "client": {
                "id": client.id,
                "name": client.name,
                "goal": client.goal,
            },

            "plan_name": (
                plan.plan_name
                if plan
                else None
            ),

            "days": days_payload,

            "diet": diet_data,

            "progress": progress,
        },
    }


# =====================================================================
# CLIENT PROGRESS HELPERS
# =====================================================================


def _f(
    value: Decimal | None,
) -> float | None:
    """
    Convert Decimal database values to JSON-safe floats.
    """

    return (
        float(value)
        if value is not None
        else None
    )


# =====================================================================
# PROGRESS ENDPOINT
# =====================================================================


async def get_progress(
    db: AsyncSession,
    token: str,
) -> dict[str, Any]:
    """
    workout-progress.php equivalent.

    Workout/session calculations are handled by the backend.

    Weight, waist and BMI remain UI-side as requested.
    """

    client = await _resolve_client(
        db,
        token,
    )

    progress = await _workout_progress(
        db,
        client.id,
    )

    month = progress[
        "month"
    ]

    history = await client_repo.get_progress_history(
        db,
        client.id,
    )

    # =================================================================
    # WEIGHT / WAIST HISTORY
    #
    # These are retained exactly as backend raw data.
    # UI remains responsible for BMI and presentation calculations.
    # =================================================================

    start = (
        history[0]
        if history
        else None
    )

    current = (
        history[-1]
        if history
        else None
    )

    weight_lost = 0.0
    waist_reduced = 0.0

    if (
        start is not None
        and current is not None
    ):

        if (
            start.weight is not None
            and current.weight is not None
        ):
            weight_lost = float(
                start.weight
                - current.weight
            )

        if (
            start.waist is not None
            and current.waist is not None
        ):
            waist_reduced = float(
                start.waist
                - current.waist
            )

    # =================================================================
    # RESPONSE
    # =================================================================

    return {
        "success": True,

        "data": {
            # ---------------------------------------------------------
            # Existing FE contract.
            #
            # These values represent monthly workout sessions.
            # ---------------------------------------------------------

            "sessions": {
                "total": int(
                    month[
                        "sessions_total"
                    ]
                ),

                "logged": int(
                    month[
                        "sessions_logged"
                    ]
                ),

                "completed": int(
                    month[
                        "sessions_completed"
                    ]
                ),

                "percent": int(
                    month[
                        "percent"
                    ]
                ),
            },

            # ---------------------------------------------------------
            # Current month
            # ---------------------------------------------------------

            "calories_burned": int(
                month[
                    "calories_burned"
                ]
            ),

            "avg_calories_per_session": int(
                month[
                    "avg_calories_per_session"
                ]
            ),

            "active_weeks": int(
                month[
                    "active_weeks"
                ]
            ),

            "weeks_total": int(
                month[
                    "weeks_total"
                ]
            ),

            "best_week_score": int(
                month[
                    "best_week_score"
                ]
            ),

            # ---------------------------------------------------------
            # Detailed month → week → day data.
            # ---------------------------------------------------------

            "weekly_detail": (
                progress[
                    "weekly_detail"
                ]
            ),

            # ---------------------------------------------------------
            # Program configuration.
            # ---------------------------------------------------------

            "plan": progress[
                "plan"
            ],

            # ---------------------------------------------------------
            # Currently selected/latest active month.
            # ---------------------------------------------------------

            "month": month,

            # ---------------------------------------------------------
            # M1 / M2 / M3.
            # ---------------------------------------------------------

            "months": progress[
                "months"
            ],

            # ---------------------------------------------------------
            # Whole program.
            # ---------------------------------------------------------

            "overall": progress[
                "overall"
            ],

            # ---------------------------------------------------------
            # Raw set totals.
            # ---------------------------------------------------------

            "sets": progress[
                "sets"
            ],

            # ---------------------------------------------------------
            # Current body measurements.
            #
            # BMI stays on the frontend.
            # ---------------------------------------------------------

            "current": {
                "weight": _f(
                    current.weight
                ),
                "waist": _f(
                    current.waist
                ),
                "chest": _f(
                    current.chest
                ),
                "arms": _f(
                    current.arms
                ),
                "thighs": _f(
                    current.thighs
                ),
            }
            if current
            else None,

            # ---------------------------------------------------------
            # Transformation.
            # ---------------------------------------------------------

            "transformation": {
                "weight_lost": (
                    weight_lost
                ),
                "waist_reduced": (
                    waist_reduced
                ),
            },

            # ---------------------------------------------------------
            # Historical chart.
            # ---------------------------------------------------------

            "chart": {
                "dates": [
                    r.created_at.strftime(
                        "%d %b"
                    )
                    for r in history
                ],

                "weights": [
                    _f(r.weight)
                    for r in history
                ],

                "waists": [
                    _f(r.waist)
                    for r in history
                ],
            },
        },
    }