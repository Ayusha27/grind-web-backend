# app/integrations/mailer.py
import asyncio
import logging
import smtplib
from email.message import EmailMessage

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
