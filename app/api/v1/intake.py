# app/api/v1/intake.py
from fastapi import APIRouter, BackgroundTasks, Depends

from app.api import openapi_ext
from app.api.deps import BodyParams
from app.cache.rate_limit import limit_intake
from app.integrations.mailer import send_intake_email
from app.services import intake_service

router = APIRouter(tags=["intake"])


@router.post(
    "/intake",
    dependencies=[Depends(limit_intake)],
    openapi_extra=openapi_ext.body(
        {
            "name": "Required.",
            "email": "Required. Reserved TLDs such as .local are rejected.",
            "age": "Required.",
            "weight": "Required.",
            "weight_unit": "Defaults to kg.",
            "height": "Height in cm.",
            "height_ft": "Height feet, when height_unit is ft.",
            "height_in": "Height inches, when height_unit is ft.",
            "height_unit": "Defaults to cm.",
            "gender": "Optional.",
            "occupation": "Optional.",
            "fitness_level": "Optional.",
            "days_per_week": "Optional.",
            "session_duration": "Optional.",
            "goals": "Repeatable. Send goals[] when form-encoded.",
            "goal_focus": "Optional.",
            "workout_pref": "Optional.",
            "injuries": "Repeatable. Send injuries[] when form-encoded.",
            "injuries_detail": "Optional.",
            "diet": "Optional.",
        },
        required=["name", "email", "age", "weight"],
    ),
)
async def submit_intake(payload: BodyParams, background: BackgroundTasks) -> dict:
    """PR-33.

    Validation is synchronous (the user must see field errors); the SMTP send
    is a background task so the response returns in ~5 ms instead of ~800 ms
    and does not hold a concurrency slot on a network wait.
    """
    subject, body, reply_to = intake_service.build_intake_email(payload)
    background.add_task(send_intake_email, subject, body, reply_to)
    return {"success": True, "message": "Submission received"}
