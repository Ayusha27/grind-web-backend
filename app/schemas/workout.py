from pydantic import BaseModel, Field


class WorkoutLogCreate(BaseModel):
    client_id: int = Field(gt=0)
    month_no: int = Field(gt=0)
    week_no: int = Field(gt=0)
    day_id: int = Field(gt=0)
    total_sets: int = Field(ge=0)
    completed_sets: int = Field(ge=0)
    calories_burned: int = Field(ge=0)


class WorkoutLogResponse(BaseModel):
    success: bool
    message: str
    log_id: int