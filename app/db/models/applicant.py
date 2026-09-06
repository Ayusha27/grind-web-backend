from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Applicant(Base):
    """One row per intake-form submission (POST /api/v1/intake).

    TYPES: the free-text fields are strings, not numbers, on purpose. The
    intake validator only checks that the four required fields are non-empty,
    never that they parse — `age: "twenty four"` is accepted today and would
    raise on an Integer column. Strings keep exactly what the submitter typed,
    matching the PHP-compat approach used across this schema. Add nullable
    `age_num` / `weight_kg` alongside these if the data ever needs querying by
    range; do not retype these.

    ESCAPING: values arrive here php_trim'd, NOT php_clean'd. php_clean
    HTML-escapes for the email body, so an occupation of "R&D" becomes
    "R&amp;D" — correct in a message, corrupt in a database.

    RETENTION: see `expires_at`.
    """

    __tablename__ = "applicants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Required — enforced in intake_service.build_intake_email before we get here.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # NOT unique: the same person may legitimately submit more than once.
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    age: Mapped[str] = mapped_column(String(10), nullable=False)
    weight: Mapped[str] = mapped_column(String(20), nullable=False)
    weight_unit: Mapped[str] = mapped_column(String(10), nullable=False, server_default="kg")

    # Body metrics. Height is stored as submitted: cm, or ft+in, with the unit
    # saying which pair to read. Converting on the way in would lose the
    # original answer and the form does not enforce either scale.
    height_cm: Mapped[str | None] = mapped_column(String(20))
    height_ft: Mapped[str | None] = mapped_column(String(10))
    height_in: Mapped[str | None] = mapped_column(String(10))
    height_unit: Mapped[str] = mapped_column(String(10), nullable=False, server_default="cm")
    fitness_level: Mapped[str | None] = mapped_column(String(50))
    days_per_week: Mapped[str | None] = mapped_column(String(20))
    session_duration: Mapped[str | None] = mapped_column(String(50))

    # Personal.
    gender: Mapped[str | None] = mapped_column(String(50))
    occupation: Mapped[str | None] = mapped_column(String(255))

    # Goals and health. `goals` and `injuries` arrive as lists and are stored
    # comma-joined, the same shape the email renders. Deliberately not JSON:
    # DB_SERVER_FLAVOR may be MariaDB on shared hosting, and this would be the
    # only JSON column in the schema. If these ever need querying individually,
    # a child table is the right answer, not a JSON path expression.
    goals: Mapped[str | None] = mapped_column(Text)
    goal_focus: Mapped[str | None] = mapped_column(String(255))
    workout_pref: Mapped[str | None] = mapped_column(String(100))
    injuries: Mapped[str | None] = mapped_column(Text)
    injuries_detail: Mapped[str | None] = mapped_column(Text)

    # Diet and lifestyle.
    diet: Mapped[str | None] = mapped_column(String(255))
    sleep: Mapped[str | None] = mapped_column(String(50))
    stress: Mapped[str | None] = mapped_column(String(50))
    # Normalised to a flag: the payload carries "yes"/"no" and the email
    # renders the long sentence, so storing the display text would be storing
    # a presentation decision.
    consultation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="0"
    )

    # Operational.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Which endpoint wrote the row. Only /api/v1/intake does today; the legacy
    # /start-your-journey.php alias builds the same email and may be wired up
    # later, and by then "where did this come from?" is unanswerable without it.
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="api")
    # Ties the row to its lines in logs/mail.log and the access log.
    request_id: Mapped[str | None] = mapped_column(String(32))
    # pending -> sent | failed. Written by the background mail task, which runs
    # after the response and therefore needs its own session.
    mail_status: Mapped[str | None] = mapped_column(String(10))
    # Set once an applicant converts. Intentionally a plain int, not a FK:
    # `clients` is legacy data this app does not own, and a constraint here
    # would make an intake insert fail on a stale client row.
    client_id: Mapped[int | None] = mapped_column(Integer)

    # RETENTION: created_at + APPLICANT_RETENTION_DAYS, stamped at insert.
    # Stored rather than computed from created_at so the policy is visible data:
    # a single row can be extended, the window can change without retroactively
    # rewriting the fate of existing rows, and NULL means "keep forever" — which
    # is what a converted lead gets, so a purge can never delete the intake of
    # someone who became a client.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_applicants_email", "email"),
        # An admin list reads newest-first.
        Index("ix_applicants_created_at", "created_at"),
        # The purge scans on this alone; without it the DELETE is a full scan.
        Index("ix_applicants_expires_at", "expires_at"),
    )
