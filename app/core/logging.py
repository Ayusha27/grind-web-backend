# app/core/logging.py
import json
import logging
import logging.handlers
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the ambient request id attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        # anything passed as logger.info("x", extra={"client_id": 5}) lands here
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable dev output that still shows the structured fields.

    A plain logging.Formatter renders only %(message)s, which silently drops
    everything passed via extra={...} — so an access line degrades to the
    useless "app.access :: request" with no method, path, status or timing.
    This appends those fields as key=value.
    """

    _BASE = "%(levelname)-8s %(name)s :: %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        base = logging.Formatter(self._BASE).format(record)
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_")
        }
        rid = request_id_ctx.get()
        if rid != "-":
            extras.setdefault("request_id", rid)
        if extras:
            base += "  " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else ConsoleFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn duplicates access lines; ours (Phase 8) carries the request id.
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("uvicorn.error").handlers.clear()
    logging.getLogger("uvicorn.error").propagate = True

    # SQLAlchemy logs every statement at INFO when echo=True — keep it at WARNING.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


# ── mail log file ────────────────────────────────────────────────────────────
# Email is fire-and-forget: send_workout_email() and send_intake_email() run as
# background tasks AFTER the response is written, so a failure can never show up
# in an HTTP status. Without a durable record, "did the mail go out?" is
# unanswerable. This writes every attempt — sent, skipped, or failed — to its
# own file, in a block format that includes the message body.


class MailLogFormatter(logging.Formatter):
    """One readable block per mail event, body included.

    Deliberately NOT JSON: this file exists to be read by a human asking
    "is the mailer working, and what did it send?", and a 2 KB workout plan
    squeezed onto one JSON line answers that badly.

    The body arrives as record._mail_body. The leading underscore is load-
    bearing: both JsonFormatter and ConsoleFormatter skip underscore-prefixed
    fields, so the same log call stays a one-liner on stdout while the full
    message text lands only here.
    """

    _RULE = "=" * 78
    # Rendered on their own lines, in this order, when present. Everything else
    # in extra={...} is appended as key=value.
    _FIELDS = ("outcome", "client_id", "cached", "transport",
               "from", "to", "subject", "smtp_code", "refused", "reason")

    def format(self, record: logging.LogRecord) -> str:
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_")
        }
        ts = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        outcome = extras.pop("outcome", record.levelname)

        lines = [
            self._RULE,
            f"{ts}  {outcome:<8} request_id={request_id_ctx.get()}",
            f"  event      : {record.getMessage()}",
        ]
        for key in self._FIELDS:
            if key in extras:
                lines.append(f"  {key:<11}: {extras.pop(key)}")
        for key, value in extras.items():
            lines.append(f"  {key:<11}: {value}")

        body = getattr(record, "_mail_body", None)
        if body:
            lines.append("  body       :")
            # Prefixed so a body line can never be mistaken for a log field,
            # and so the block stays greppable (grep -v '    | ').
            lines += [f"    | {line}" for line in str(body).splitlines()]

        if record.exc_info:
            lines.append("  traceback  :")
            trace = self.formatException(record.exc_info)
            lines += [f"    | {line}" for line in trace.splitlines()]

        return '\n'.join(lines)


def setup_mail_log(
    path: str,
    *,
    max_bytes: int = 5_000_000,
    backup_count: int = 3,
) -> Path | None:
    """Attach the mail log file to the mailer logger. Returns the resolved path.

    Rotating, because WORKOUT_EMAIL_ON_CACHE_HIT=true logs a full plan body on
    every request to the busiest route in the app — an unbounded file would eat
    the disk in a day.

    propagate is left ON, so these events still reach stdout as usual; this
    handler is additive, never a redirect. A file that cannot be opened (bad
    path, read-only volume) is logged and skipped rather than raised: mail
    logging must never be the reason the app fails to boot.
    """
    target = Path(path).expanduser()
    if not target.is_absolute():
        # Relative to the project root (app/core/logging.py -> ../../), not to
        # the CWD, so systemd and `uv run` from any directory agree on one file.
        target = Path(__file__).resolve().parents[2] / target

    mail_logger = logging.getLogger("app.integrations.mailer")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
    except OSError:
        logging.getLogger(__name__).warning("mail_log_unavailable", extra={"path": str(target)})
        return None

    handler.setFormatter(MailLogFormatter())
    # Tagged so a re-run (reload, test) replaces its own handler instead of
    # stacking a second one that double-writes every line.
    handler.set_name("mail_file")
    for existing in list(mail_logger.handlers):
        if existing.get_name() == "mail_file":
            mail_logger.removeHandler(existing)
            existing.close()
    mail_logger.addHandler(handler)
    # The mailer's own events are INFO; the root level could be WARNING in prod
    # and would otherwise silence exactly the file the operator came to read.
    mail_logger.setLevel(logging.INFO)
    return target
