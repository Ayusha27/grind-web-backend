from pydantic import BaseModel, Field

class WorkoutSetLogInput(BaseModel):
    exercise_id: int = Field(gt=0)
    set_no: int = Field(gt=0)
    completed: bool

class WorkoutLogCreate(BaseModel):
    client_id: int = Field(gt=0)
    month_no: int = Field(gt=0)
    week_no: int = Field(gt=0)
    day_id: int = Field(gt=0)
    total_sets: int = Field(ge=0)
    completed_sets: int = Field(ge=0)
    calories_burned: int = Field(ge=0)

    sets: list[WorkoutSetLogInput] = Field(
        default_factory=list
    )


class WorkoutLogResponse(BaseModel):
    success: bool
    message: str
    log_id: int

class WorkoutSetLogCreate(BaseModel):
    month_no: int = Field(gt=0)
    week_no: int = Field(gt=0)
    day_id: int = Field(gt=0)
    exercise_id: int = Field(gt=0)
    set_no: int = Field(gt=0)
    completed: bool

class WorkoutSetLogResponse(BaseModel):
    id: int
    client_id: int
    month_no: int
    week_no: int
    day_id: int
    exercise_id: int
    set_no: int
    completed: bool        