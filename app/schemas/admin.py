# app/schemas/admin.py
from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class AdminLoginResponse(BaseModel):
    success: bool = True
    access_token: str
    token_type: str = "bearer"  # noqa: S105 — an OAuth scheme name, not a secret
    expires_in: int


class ClientUpsertRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    email: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    goal: str | None = Field(default=None, max_length=255)


class PlanImportRequest(BaseModel):
    client_id: int = Field(gt=0)
    workout_json: str = Field(min_length=1)


class PlanCreateRequest(BaseModel):
    client_id: int = Field(gt=0)
    plan_name: str = Field(min_length=1, max_length=255)
    workout_json: str = Field(min_length=1)


class DietSaveRequest(BaseModel):
    access_token: str = Field(min_length=1, max_length=50)
    diet_json: str = Field(min_length=1)


class ProgressCreateRequest(BaseModel):
    client_id: int = Field(gt=0)
    weight: str | None = None
    waist: str | None = None
    chest: str | None = None
    arms: str | None = None
    thighs: str | None = None
    notes: str | None = None
