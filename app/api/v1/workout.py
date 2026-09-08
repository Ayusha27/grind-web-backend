from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentClient, DbSession
from app.schemas.workout import (
    WorkoutLogCreate,
    WorkoutLogResponse,
    WorkoutSetLogCreate,
)
from app.services import workout_service

router = APIRouter()


# =====================================================================
# LEGACY WORKOUT ENDPOINTS
# =====================================================================


@router.get("/workout")
async def get_workout(
    db: DbSession,
    client_id: Any = Query(default=None),
) -> dict[str, Any]:
    """
    Return the active workout plan.

    This preserves the existing GET /workout contract.
    """

    return await workout_service.get_workout(
        db,
        client_id,
    )


@router.post(
    "/workout/complete",
)
async def complete_workout(
    payload: dict[str, Any],
    db: DbSession,
) -> dict[str, Any]:
    """
    Legacy workout completion endpoint.

    Kept for backwards compatibility with the original PHP-compatible API.
    """

    return await workout_service.complete_workout(
        db,
        payload,
    )


@router.post(
    "/workout/logs",
)
async def save_workout_log(
    payload: dict[str, Any],
    db: DbSession,
) -> dict[str, Any]:
    """
    Legacy set-level workout log endpoint.

    Kept unchanged so existing clients do not break.
    """

    return await workout_service.save_log(
        db,
        payload,
    )


# =====================================================================
# MIGRATED WORKOUT SUMMARY
# =====================================================================


@router.post(
    "/workout/log",
    response_model=WorkoutLogResponse,
)
async def log_workout(
    payload: WorkoutLogCreate,
    client: CurrentClient,
    db: DbSession,
) -> WorkoutLogResponse:
    """
    Save one complete workout-day state.

    The authenticated client is resolved from:

        ?token=<access_token>

    or:

        Authorization: Bearer <access_token>

    The backend is responsible for calculating:

        total_sets
        completed_sets
        completion_percent
        calories_burned

    from:

        active workout plan
        +
        submitted set states

    The summary row is upserted by:

        client_id
        + month_no
        + week_no
        + day_id
    """

    return await workout_service.save_workout_summary(
        db,
        payload,
        client_id=client.id,
    )


# =====================================================================
# MIGRATED INDIVIDUAL SET STATE
# =====================================================================


@router.post(
    "/workout/set",
)
async def log_workout_set(
    payload: WorkoutSetLogCreate,
    client: CurrentClient,
    db: DbSession,
) -> dict[str, Any]:
    """
    Save or update one individual workout set.

    The client ID is obtained from CurrentClient rather than from the
    request payload.

    This writes to workout_set_logs.

    It does not create a day summary by itself.
    """

    return await workout_service.save_workout_set(
        db,
        client_id=client.id,
        payload=payload,
    )


# =====================================================================
# READ INDIVIDUAL SET STATES
# =====================================================================


@router.get("/workout/sets")
async def get_workout_sets(
    db: DbSession,
    client: CurrentClient,
    month_no: int = Query(gt=0),
    week_no: int = Query(gt=0),
    day_id: int = Query(gt=0),
) -> dict[str, Any]:
    """
    Return saved individual set states for one workout day.

    The client ID is resolved from the authenticated access token.

    This allows the frontend to restore checkbox/set completion state
    after a page refresh.
    """

    sets = await workout_service.get_workout_sets(
        db,
        client_id=client.id,
        month_no=month_no,
        week_no=week_no,
        day_id=day_id,
    )

    return {
        "success": True,
        "data": sets,
    }