# app/integrations/mailer.py
import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from app.core.config import settings
from app.db.session import SessionLocal
from app.repositories import applicant_repo

logger = logging.getLogger(__name__)


def _body_field(body: str) -> dict[str, str]:
    """extra={...} fragment carrying the message text to the mail log file.

    The leading underscore is load-bearing: JsonFormatter and ConsoleFormatter
    both skip underscore-prefixed fields, so stdout keeps a tidy one-line event
    while only MailLogFormatter expands the full body into logs/mail.log.
    """
    return {"_mail_body": body} if settings.MAIL_LOG_BODY else {}


def _send_sync(message: EmailMessage) -> dict[str, object]:
    """Transport only. Returns what the server said, for the mail log.

    Building the message is the caller's job, so the log line can name the
    From/To/Subject even when the connection never gets off the ground.
    """
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
        if settings.SMTP_STARTTLS:
            smtp.starttls()
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        # send_message returns the recipients the server REFUSED. An empty dict
        # is the only real proof of acceptance: no exception is raised when
        # some (but not all) recipients are rejected at handoff.
        refused = smtp.send_message(message)

    return {
        "transport": f"{settings.SMTP_HOST}:{settings.SMTP_PORT}"
                     f" {'STARTTLS' if settings.SMTP_STARTTLS else 'plain'}",
        "refused": refused or "none",
    }


async def _record_mail_status(applicant_id: int | None, status: str) -> None:
    """Write the send outcome back onto the applicants row.

    Opens its OWN session on purpose. This runs as a background task, after the
    response has been written, and get_db() closes the request session in its
    `finally` at that point — reusing it would fail on a closed connection.

    Never raises: the mail outcome is already in the log, and a failed status
    write must not turn a successful submission into an error trail.
    """
    if applicant_id is None:
        return
    try:
        async with SessionLocal() as db:
            await applicant_repo.set_mail_status(db, applicant_id, status)
    except Exception:
        logger.exception("mail_status_update_failed", extra={"applicant_id": applicant_id})


async def send_intake_email(
    subject: str, body: str, reply_to: str, *, applicant_id: int | None = None
) -> None:
    """smtplib is blocking, so it runs in a thread — calling it directly on
    the event loop would stall every other request in this worker for the
    duration of the SMTP conversation.

    Every outcome below is written to the mail log file as well as stdout; see
    setup_mail_log() in app/core/logging.py for why that file exists.
    """
    log_extra: dict[str, object] = {
        "from": settings.MAIL_FROM,
        "to": settings.INTAKE_RECIPIENT,
        "subject": subject,
        "reply_to": reply_to,
        "applicant_id": applicant_id,
    }

    if not settings.MAIL_ENABLED:
        # Explicitly disabled (local dev). Log it so a missing email is always
        # traceable to configuration rather than looking like a silent drop.
        logger.info(
            "intake_email_skipped",
            extra={**log_extra, "outcome": "SKIPPED", "reason": "MAIL_ENABLED=false"},
        )
        await _record_mail_status(applicant_id, "skipped")
        return

    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        # Mirrors the guard in send_workout_email(): enabled but with no
        # credentials, every submission would otherwise log a full auth
        # traceback. A named SKIPPED reason says what to fix instead.
        logger.warning(
            "intake_email_skipped_unconfigured",
            extra={**log_extra, "outcome": "SKIPPED",
                   "reason": "SMTP_USER/SMTP_PASSWORD not set"},
        )
        await _record_mail_status(applicant_id, "skipped")
        return

    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = settings.INTAKE_RECIPIENT
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body, charset="utf-8")

    try:
        result = await asyncio.to_thread(_send_sync, message)
        logger.info(
            "intake_email_sent",
            extra={**log_extra, **result, "outcome": "SENT", **_body_field(body)},
        )
        await _record_mail_status(applicant_id, "sent")
    except Exception:
        # Never propagate: the submission is already accepted. The log is the
        # record. (PHP set $mail_error and showed a banner; we cannot, because
        # the response has already been sent.)
        logger.exception(
            "intake_email_failed",
            extra={**log_extra, "outcome": "FAILED", **_body_field(body)},
        )
        await _record_mail_status(applicant_id, "failed")


# Gmail notification for GET /api/v1/workout.
# Everything below is additive and self-contained: it shares no configuration
# and no code path with the intake mailer above.


def format_workout_body(payload: dict[str, Any], *, client_id: int, cached: bool) -> str:
    """Plain-text rendering of the exact dict the endpoint returns.

    Reads defensively (.get everywhere) so a future response-shape change
    degrades to a thinner email instead of raising inside a background task.
    """
    data = payload.get("data")
    lines = [
        "GRIND - Workout Plan",
        "=" * 52,
        f"Client id : {client_id}",
        f"Source    : {'cache' if cached else 'database'}",
        "",
    ]

    if not data:
        # PR-03: a successful response that carries no plan.
        lines.append(payload.get("message") or "No workout plan assigned.")
        return "\n".join(lines)

    plan = data.get("plan") or {}
    days = data.get("days") or []
    total_exercises = sum(len(d.get("exercises") or []) for d in days)

    lines += [
        f"Plan      : {plan.get('plan_name')}",
        f"Plan id   : {plan.get('id')}   version: {plan.get('version_no')}"
        f"   active: {'yes' if plan.get('is_active') else 'no'}",
        f"Created   : {plan.get('created_at')}",
        f"Totals    : {len(days)} day(s), {total_exercises} exercise(s)",
        "",
        "-" * 52,
    ]

    for day in days:
        exercises = day.get("exercises") or []
        lines.append(
            f"Day {day.get('day_number')} - {day.get('day_name')}  ({len(exercises)} exercises)"
        )
        for i, ex in enumerate(exercises, 1):
            sets, reps = ex.get("sets_count"), ex.get("reps")
            scheme = f"{sets} x {reps}" if sets and reps else (reps or sets or "")
            lines.append(f"  {i:>2}. {str(ex.get('exercise_name') or ''):<34} {scheme}")
            if ex.get("notes"):
                lines.append(f"      note: {ex['notes']}")
        lines.append("")

    return "\n".join(lines)


def _send_workout_sync(message: EmailMessage) -> dict[str, object]:
    """Transport only. Returns what the server said, for the mail log.

    Credentials are read here, from .env via Settings — never hardcoded, and
    never logged: nothing in the returned dict carries GMAIL_PASSWORD.

    Gmail submission is STARTTLS on 587 or implicit TLS on 465; the port
    decides which transport is used. The SMTP username is the full Gmail
    address and the password must be a 16-character App Password — Google
    rejects normal account passwords for SMTP AUTH.
    """
    if settings.GMAIL_PORT == 465:
        # Implicit TLS: the socket is wrapped before the greeting, so there is
        # no STARTTLS step to issue.
        with smtplib.SMTP_SSL(
            settings.GMAIL_HOST, settings.GMAIL_PORT, timeout=settings.GMAIL_TIMEOUT
        ) as smtp:
            code, _ = smtp.ehlo()
            smtp.login(settings.GMAIL_EMAIL, settings.GMAIL_PASSWORD)
            refused = smtp.send_message(message)
        return {
            "transport": f"{settings.GMAIL_HOST}:{settings.GMAIL_PORT} SSL",
            "smtp_code": code,
            "refused": refused or "none",
        }

    with smtplib.SMTP(
        settings.GMAIL_HOST, settings.GMAIL_PORT, timeout=settings.GMAIL_TIMEOUT
    ) as smtp:
        smtp.ehlo()
        smtp.starttls()
        code, _ = smtp.ehlo()   # re-identify: the server advertises AUTH only after TLS
        smtp.login(settings.GMAIL_EMAIL, settings.GMAIL_PASSWORD)
        # send_message returns the recipients the server REFUSED. An empty dict
        # is the only real proof of acceptance: no exception is raised when
        # some (but not all) recipients are rejected at handoff.
        refused = smtp.send_message(message)

    return {
        "transport": f"{settings.GMAIL_HOST}:{settings.GMAIL_PORT} STARTTLS",
        "smtp_code": code,
        "refused": refused or "none",
    }


async def send_workout_email(
    payload: dict[str, Any], *, client_id: int, cached: bool
) -> None:
    """Fire-and-forget workout notification.

    ERROR STRATEGY (explicit): this NEVER raises and NEVER alters the HTTP
    response. It is queued as a FastAPI background task, so it runs after the
    response has already been written to the client; an SMTP failure can only
    ever produce a log line. That matches send_intake_email above.

    smtplib is blocking, so the send runs in a worker thread — calling it on
    the event loop would stall every other request in this worker for the
    duration of the SMTP conversation.

    RATE LIMIT: /api/v1/workout is the highest-traffic route in the app and
    Gmail caps a free account at roughly 500 recipients/day (Workspace 2,000),
    with short-term throttling well below that. Set
    WORKOUT_EMAIL_ON_CACHE_HIT=false to send only on a cache miss, which bounds
    volume to about one message per client per CACHE_TTL_WORKOUT.
    """
    log_extra: dict[str, object] = {
        "client_id": client_id,
        "cached": cached,
        "from": settings.GMAIL_FROM or settings.GMAIL_EMAIL,
        "to": settings.GMAIL_TO or settings.GMAIL_EMAIL,
    }

    if not settings.WORKOUT_EMAIL_ENABLED:
        logger.info(
            "workout_email_skipped_disabled",
            extra={**log_extra, "outcome": "SKIPPED",
                   "reason": "WORKOUT_EMAIL_ENABLED=false"},
        )
        return

    if not settings.GMAIL_EMAIL or not settings.GMAIL_PASSWORD:
        # Guard before the socket: without this, an enabled-but-unconfigured
        # deployment logs a full SMTP traceback on every single request.
        logger.warning(
            "workout_email_skipped_unconfigured",
            extra={**log_extra, "outcome": "SKIPPED",
                   "reason": "GMAIL_EMAIL/GMAIL_PASSWORD not set"},
        )
        return

    body = ""
    try:
        plan = (payload.get("data") or {}).get("plan") or {}
        subject = f"[GRIND] Workout plan for client {client_id}"
        if plan.get("plan_name"):
            subject = f"[GRIND] {plan['plan_name']} (client {client_id})"
        log_extra["subject"] = subject

        body = format_workout_body(payload, client_id=client_id, cached=cached)

        message = EmailMessage()
        message["From"] = log_extra["from"]
        message["To"] = log_extra["to"]
        message["Subject"] = subject
        message.set_content(body, charset="utf-8")

        result = await asyncio.to_thread(_send_workout_sync, message)
        logger.info(
            "workout_email_sent",
            extra={**log_extra, **result, "outcome": "SENT", **_body_field(body)},
        )
    except Exception:
        # Never propagate: the response is already sent. The log is the record.
        # The body is logged on failure too — that is the whole point of the
        # file: what was meant to go out, and why it did not.
        logger.exception(
            "workout_email_failed",
            extra={**log_extra, "outcome": "FAILED", **_body_field(body)},
        )
