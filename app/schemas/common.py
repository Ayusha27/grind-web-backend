# app/schemas/common.py

from pydantic import BaseModel, ConfigDict


class SuccessResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True


class ErrorResponse(BaseModel):
    success: bool = False
    message: str


class MessageResponse(BaseModel):
    success: bool = True
    message: str
