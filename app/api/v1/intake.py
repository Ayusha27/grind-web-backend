# app/api/v1/intake.py
from fastapi import APIRouter, BackgroundTasks, Depends

from app.api import openapi_ext
from app.api.deps import BodyParams, DbSession
from app.cache.rate_limit import limit_intake
from app.integrations.mailer import send_intake_email
from app.repositories import applicant_repo
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
async def submit_intake(
    payload: BodyParams, db: DbSession, background: BackgroundTasks
) -> dict:
    """PR-33. Stores the submission, then notifies by email.

    ORDER MATTERS. Validation runs first, so the user sees field errors on a
    400 and nothing is written. The row is then saved and committed INSIDE the
    request; only after that is the mail queued.

    Save-then-queue, not the reverse: a failed insert must abort before any
    notification exists, otherwise an email can announce a submission that was
    never stored. The two also differ in how failure is handled — a lost row is
    permanent data loss and earns a 500 the submitter can retry, while a lost
    email is recoverable from the row plus logs/mail.log and must never break a
    submission that already succeeded. That is why the insert is awaited and
    the SMTP send is not: smtplib blocks for seconds, the INSERT does not.
    """
    subject, body, reply_to = intake_service.build_intake_email(payload)

    applicant = await applicant_repo.create(db, intake_service.build_applicant_row(payload))

    background.add_task(
        send_intake_email, subject, body, reply_to, applicant_id=applicant.id
    )
    return {"success": True, "message": "Submission received"}
