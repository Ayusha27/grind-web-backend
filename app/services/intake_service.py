# app/services/intake_service.py
from typing import Any

from email_validator import EmailNotValidError, validate_email

from app.core.compat import php_clean, php_trim
from app.core.exceptions import ValidationFailure

# Swagger UI's "Try it out" pre-fills every untouched string field with the
# literal word "string". That arrives as a perfectly ordinary non-empty value,
# so the `or 'N/A'` fallbacks below never fire and the email reads "Gender:
# string". Treating it as unfilled is what a reader of these emails expects.
#
# The trade-off, stated plainly: a genuine answer of "string" is now discarded.
# For a fitness intake form — occupation, diet, injuries — nobody types that,
# and an email full of "string" is the far more likely outcome by orders of
# magnitude.
_PLACEHOLDER = "string"


def _field(value: Any) -> str:
    """php_clean, with the Swagger placeholder collapsed to empty."""
    text = php_clean(value)
    return "" if text.strip().lower() == _PLACEHOLDER else text


def _joined(value: Any) -> str:
    """PHP: implode(', ', cleanArr((array)$_POST['goals']))"""
    if value is None:
        return ""
    items = value if isinstance(value, list) else [value]
    # Placeholders are dropped per item, so ["cardio", "string"] keeps "cardio"
    # instead of rendering "cardio, string".
    return ", ".join(cleaned for item in items if (cleaned := _field(item)))


# ── persistence ──────────────────────────────────────────────────────────────
# The email and the database row are built from the same payload but NOT from
# the same values. php_clean() HTML-escapes for the message body, so an
# occupation of "R&D" renders correctly in mail as "R&amp;D" — and would be
# stored that way too if the row reused those strings. Escaping is a property
# of the destination, not of the data, so the row is built from php_trim()
# instead and carries what the submitter actually typed.


def _raw(value: Any) -> str | None:
    """Trimmed submitter input, or None. No HTML escaping.

    Returns None rather than "" so an unanswered optional question is NULL in
    the database — "not asked/not answered" and "answered with an empty string"
    should not be the same value in a column someone will later query.
    """
    text = php_trim(value)
    return None if text.lower() == _PLACEHOLDER or not text else text


def _joined_raw(value: Any) -> str | None:
    """_joined, unescaped, for storage."""
    if value is None:
        return None
    items = value if isinstance(value, list) else [value]
    joined = ", ".join(cleaned for item in items if (cleaned := _raw(item)))
    return joined or None


def build_applicant_row(payload: dict[str, Any]) -> dict[str, Any]:
    """Map an intake payload onto Applicant columns.

    Call AFTER build_intake_email: this does no validation of its own and
    trusts that the four required fields are present, which is exactly what
    build_intake_email has just guaranteed by raising if they were not.

    Keys here must match the model's column names — `height` becomes
    `height_cm`, and the payload's yes/no `consultation` becomes a bool.
    """
    return {
        "name": _raw(payload.get("name")),
        "email": _raw(payload.get("email")),
        "age": _raw(payload.get("age")),
        "weight": _raw(payload.get("weight")),
        # Units are NOT NULL with a server default; keep the PHP fallbacks so a
        # stored weight always has a scale attached to it.
        "weight_unit": _raw(payload.get("weight_unit")) or "kg",
        "height_unit": _raw(payload.get("height_unit")) or "cm",
        "height_cm": _raw(payload.get("height")),
        "height_ft": _raw(payload.get("height_ft")),
        "height_in": _raw(payload.get("height_in")),
        "fitness_level": _raw(payload.get("fitness_level")),
        "days_per_week": _raw(payload.get("days_per_week")),
        "session_duration": _raw(payload.get("session_duration")),
        "gender": _raw(payload.get("gender")),
        "occupation": _raw(payload.get("occupation")),
        "goals": _joined_raw(payload.get("goals")),
        "goal_focus": _raw(payload.get("goal_focus")),
        "workout_pref": _raw(payload.get("workout_pref")),
        "injuries": _joined_raw(payload.get("injuries")),
        "injuries_detail": _raw(payload.get("injuries_detail")),
        "diet": _raw(payload.get("diet")),
        "sleep": _raw(payload.get("sleep")),
        "stress": _raw(payload.get("stress")),
        # The email renders a sentence; the column stores the decision.
        "consultation": (_raw(payload.get("consultation")) or "no").lower() == "yes",
    }


def build_intake_email(payload: dict[str, Any]) -> tuple[str, str, str]:
    """PR-33 — start-your-journey.php.

    Returns (subject, body, reply_to). The body layout is reproduced line for
    line, including the 'N/A' / 'None' / 'None selected' fallbacks, because
    whoever reads these emails reads them by eye and a reformatted template
    is a real regression for them.
    """
    name = _field(payload.get("name"))
    raw_email = php_trim(payload.get("email"))
    raw_email = "" if raw_email.lower() == _PLACEHOLDER else raw_email
    email = _field(raw_email)
    age = _field(payload.get("age"))
    gender = _field(payload.get("gender"))
    occupation = _field(payload.get("occupation"))
    weight = _field(payload.get("weight"))
    # Units fall back to the PHP defaults, so an unfilled unit still renders a
    # sensible "55 kg" rather than a bare number.
    weight_unit = _field(payload.get("weight_unit")) or "kg"
    height_unit = _field(payload.get("height_unit")) or "cm"
    height_cm = _field(payload.get("height"))
    height_ft = _field(payload.get("height_ft"))
    height_in = _field(payload.get("height_in"))
    fitness_level = _field(payload.get("fitness_level"))
    days_per_week = _field(payload.get("days_per_week"))
    session_dur = _field(payload.get("session_duration"))
    goals = _joined(payload.get("goals"))
    goal_focus = _field(payload.get("goal_focus"))
    workout_pref = _field(payload.get("workout_pref"))
    injuries = _joined(payload.get("injuries"))
    injuries_detail = _field(payload.get("injuries_detail"))
    diet = _field(payload.get("diet"))
    sleep = _field(payload.get("sleep"))
    stress = _field(payload.get("stress"))
    consultation = _field(payload.get("consultation")) or "no"

    # Height is optional, so an unfilled one has to read "N/A" rather than the
    # bare unit — "Height : cm" looks like a truncated value, not a blank.
    if height_unit == "ft":
        height_display = f"{height_ft}ft {height_in}in" if (height_ft or height_in) else "N/A"
    else:
        height_display = f"{height_cm} cm" if height_cm else "N/A"

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
