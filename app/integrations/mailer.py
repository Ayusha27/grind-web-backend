# app/integrations/mailer.py
import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_sync(subject: str, body: str, reply_to: str) -> None:
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = settings.INTAKE_RECIPIENT
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body, charset="utf-8")

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
        if settings.SMTP_STARTTLS:
            smtp.starttls()
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(message)


async def send_intake_email(subject: str, body: str, reply_to: str) -> None:
    """smtplib is blocking, so it runs in a thread — calling it directly on
    the event loop would stall every other request in this worker for the
    duration of the SMTP conversation.
    """
    if not settings.MAIL_ENABLED:
        # Explicitly disabled (local dev). Log it so a missing email is always
        # traceable to configuration rather than looking like a silent drop.
        logger.info("intake_email_skipped", extra={"reply_to": reply_to})
        return

    try:
        await asyncio.to_thread(_send_sync, subject, body, reply_to)
        logger.info("intake_email_sent", extra={"reply_to": reply_to})
    except Exception:
        # Never propagate: the submission is already accepted. The log is the
        # record. (PHP set $mail_error and showed a banner; we cannot, because
        # the response has already been sent.)
        logger.exception("intake_email_failed", extra={"reply_to": reply_to})


# Outlook notification for GET /api/v1/workout.
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


def _send_workout_sync(subject: str, body: str) -> None:
    """Credentials are read here, from .env via Settings — never hardcoded.

    Outlook / Microsoft 365 submission is STARTTLS on 587, and the SMTP
    username is the full mailbox address.
    """
    message = EmailMessage()
    message["From"] = settings.OUTLOOK_FROM or settings.OUTLOOK_EMAIL
    message["To"] = settings.OUTLOOK_TO or settings.OUTLOOK_EMAIL
    message["Subject"] = subject
    message.set_content(body, charset="utf-8")

    with smtplib.SMTP(
        settings.OUTLOOK_HOST, settings.OUTLOOK_PORT, timeout=settings.OUTLOOK_TIMEOUT
    ) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()             # re-identify: the server advertises AUTH only after TLS
        smtp.login(settings.OUTLOOK_EMAIL, settings.OUTLOOK_PASSWORD)
        smtp.send_message(message)


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
    Microsoft 365 caps a mailbox at roughly 30 messages/minute and 10,000
    recipients/day. Set WORKOUT_EMAIL_ON_CACHE_HIT=false to send only on a
    cache miss, which bounds volume to about one message per client per
    CACHE_TTL_WORKOUT.
    """
    log_extra = {"client_id": client_id, "cached": cached}

    if not settings.WORKOUT_EMAIL_ENABLED:
        logger.info("workout_email_skipped_disabled", extra=log_extra)
        return

    if not settings.OUTLOOK_EMAIL or not settings.OUTLOOK_PASSWORD:
        # Guard before the socket: without this, an enabled-but-unconfigured
        # deployment logs a full SMTP traceback on every single request.
        logger.warning("workout_email_skipped_unconfigured", extra=log_extra)
        return

    try:
        plan = (payload.get("data") or {}).get("plan") or {}
        subject = f"[GRIND] Workout plan for client {client_id}"
        if plan.get("plan_name"):
            subject = f"[GRIND] {plan['plan_name']} (client {client_id})"

        body = format_workout_body(payload, client_id=client_id, cached=cached)
        await asyncio.to_thread(_send_workout_sync, subject, body)
        logger.info("workout_email_sent", extra=log_extra)
    except Exception:
        # Never propagate: the response is already sent. The log is the record.
        logger.exception("workout_email_failed", extra=log_extra)
