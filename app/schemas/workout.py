from pydantic import BaseModel, Field


class WorkoutSetLogInput(BaseModel):
    """
    One workout set submitted as part of a complete workout log.
    """

    exercise_id: int = Field(gt=0)
    set_no: int = Field(gt=0)
    completed: bool


class WorkoutLogCreate(BaseModel):
    """
    Payload used when logging a complete workout day.

    The client identity is NOT supplied by the frontend.

    It is resolved by the API route through CurrentClient.

    The backend calculates:

        total_sets
        completed_sets
        completion_percent
        calories_burned

    from the active workout plan and submitted set states.
    """

    month_no: int = Field(gt=0)

    week_no: int = Field(gt=0)

    day_id: int = Field(gt=0)

    # -------------------------------------------------------------
    # Retained for compatibility with the current frontend.
    #
    # These values are NOT trusted by the backend.
    # -------------------------------------------------------------

    total_sets: int = Field(ge=0)

    completed_sets: int = Field(ge=0)

    calories_burned: int = Field(ge=0)

    # -------------------------------------------------------------
    # Actual set-level workout state.
    # -------------------------------------------------------------

    sets: list[WorkoutSetLogInput] = Field(
        default_factory=list
    )


class WorkoutSummary(BaseModel):
    """
    Backend-calculated workout summary.
    """

    total_sets: int

    completed_sets: int

    completion_percent: float

    calories_burned: int


class WorkoutLogResponse(BaseModel):
    """
    Response returned after logging a workout.
    """

    success: bool

    message: str

    log_id: int

    sets_logged: int

    summary: WorkoutSummary


class WorkoutSetLogCreate(BaseModel):
    """
    Payload for logging/updating one individual workout set.

    client_id is deliberately excluded.

    The authenticated client is supplied by CurrentClient.
    """

    month_no: int = Field(gt=0)

    week_no: int = Field(gt=0)

    day_id: int = Field(gt=0)

    exercise_id: int = Field(gt=0)

    set_no: int = Field(gt=0)

    completed: bool


class WorkoutSetLogResponse(BaseModel):
    """
    Persisted workout set response.
    """

    id: int

    client_id: int

    month_no: int

    week_no: int

    day_id: int

    exercise_id: int

    set_no: int

    completed: bool