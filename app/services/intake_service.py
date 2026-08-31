# app/services/intake_service.py
from typing import Any

from email_validator import EmailNotValidError, validate_email

from app.core.compat import php_clean, php_trim
from app.core.exceptions import ValidationFailure


def _joined(value: Any) -> str:
    """PHP: implode(', ', cleanArr((array)$_POST['goals']))"""
    if value is None:
        return ""
    items = value if isinstance(value, list) else [value]
    return ", ".join(php_clean(item) for item in items)


def build_intake_email(payload: dict[str, Any]) -> tuple[str, str, str]:
    """PR-33 — start-your-journey.php.

    Returns (subject, body, reply_to). The body layout is reproduced line for
    line, including the 'N/A' / 'None' / 'None selected' fallbacks, because
    whoever reads these emails reads them by eye and a reformatted template
    is a real regression for them.
    """
    name = php_clean(payload.get("name"))
    raw_email = php_trim(payload.get("email"))
    email = php_clean(raw_email)
    age = php_clean(payload.get("age"))
    gender = php_clean(payload.get("gender"))
    occupation = php_clean(payload.get("occupation"))
    weight = php_clean(payload.get("weight"))
    weight_unit = php_clean(payload.get("weight_unit") or "kg")
    height_unit = php_clean(payload.get("height_unit") or "cm")
    height_cm = php_clean(payload.get("height"))
    height_ft = php_clean(payload.get("height_ft"))
    height_in = php_clean(payload.get("height_in"))
    fitness_level = php_clean(payload.get("fitness_level"))
    days_per_week = php_clean(payload.get("days_per_week"))
    session_dur = php_clean(payload.get("session_duration"))
    goals = _joined(payload.get("goals"))
    goal_focus = php_clean(payload.get("goal_focus"))
    workout_pref = php_clean(payload.get("workout_pref"))
    injuries = _joined(payload.get("injuries"))
    injuries_detail = php_clean(payload.get("injuries_detail"))
    diet = php_clean(payload.get("diet"))
    sleep = php_clean(payload.get("sleep"))
    stress = php_clean(payload.get("stress"))
    consultation = php_clean(payload.get("consultation") or "no")

    height_display = (
        f"{height_ft}ft {height_in}in" if height_unit == "ft" else f"{height_cm} cm"
    )

    # PR-33 — the four required fields, validated in the PHP's own order.
    errors: list[str] = []
    if not name:
        errors.append("Name is required.")
    if not raw_email:
        errors.append("A valid email address is required.")
    else:
        try:
            # PHP used FILTER_VALIDATE_EMAIL. email-validator is stricter but
            # rejects the same practical set; check_deliverability is off so
            # no DNS lookup blocks the request.
            validate_email(raw_email, check_deliverability=False)
        except EmailNotValidError:
            errors.append("A valid email address is required.")
    if not age:
        errors.append("Age is required.")
    if not weight:
        errors.append("Weight is required.")

    if errors:
        raise ValidationFailure(" ".join(errors))

    consultation_text = (
        "Yes – lifestyle consultation requested" if consultation == "yes" else "No"
    )

    body = (
        "New GRIND Client Intake Submission\n"
        "===================================\n\n"
        "PERSONAL\n"
        "--------\n"
        f"Name       : {name}\n"
        f"Email      : {email}\n"
        f"Age        : {age}\n"
        f"Gender     : {gender or 'N/A'}\n"
        f"Occupation : {occupation or 'N/A'}\n\n"
        "BODY METRICS\n"
        "------------\n"
        f"Weight          : {weight} {weight_unit}\n"
        f"Height          : {height_display}\n"
        f"Fitness Level   : {fitness_level or 'N/A'}\n"
        f"Training Days   : {days_per_week or 'N/A'}\n"
        f"Session Length  : {session_dur or 'N/A'}\n\n"
        "TRAINING GOALS\n"
        "--------------\n"
        f"Goals Selected  : {goals or 'None selected'}\n"
        f"Specific Focus  : {goal_focus or 'N/A'}\n"
        f"Workout Pref    : {workout_pref or 'N/A'}\n"
        f"Consultation    : {consultation_text}\n\n"
        "HEALTH\n"
        "------\n"
        f"Injuries        : {injuries or 'None'}\n"
        f"Injury Details  : {injuries_detail or 'N/A'}\n\n"
        "DIET & LIFESTYLE\n"
        "----------------\n"
        f"Diet            : {diet or 'N/A'}\n"
        f"Sleep           : {sleep or 'N/A'}\n"
        f"Stress Level    : {stress or 'N/A'}\n\n"
        "===================================\n"
        "Submitted via GRIND Intake Form\n"
    )

    return f"New GRIND Intake Submission — {name}", body, raw_email
