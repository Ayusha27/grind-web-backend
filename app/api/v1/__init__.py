# app/api/v1/__init__.py
from fastapi import APIRouter

from app.api.v1 import admin, affiliate, intake, payments, portal, workout

api_router = APIRouter()
api_router.include_router(workout.router)
api_router.include_router(affiliate.router)
api_router.include_router(payments.router)
api_router.include_router(portal.router)
api_router.include_router(intake.router)
api_router.include_router(admin.router)
