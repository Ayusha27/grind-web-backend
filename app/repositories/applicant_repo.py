# app/repositories/applicant_repo.py
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import request_id_ctx
from app.db.models import Applicant


def _expires_at_expression() -> Any:
    """`expires_at` as SQL, evaluated on the database clock.

    Deliberately NOT datetime.now(UTC) in Python. created_at comes from the
    server's now(), and MySQL DATETIME carries no timezone — so a Python-side
    UTC value would be measured against a different clock than the column it
    sits beside, and the two would disagree by the server's UTC offset. On an
    IST instance that makes expires_at read as created_at + 30 days - 5h30m,
    which is confusing to anyone reading the row and skews the purge.

    Computing it here means both timestamps come from one clock and the
    arithmetic is exact.

    DATE_ADD is MySQL/MariaDB syntax, which is consistent with the rest of this
    layer (see the init_command in db/session.py); `func.now() + timedelta`
    would compile to `now() + %s` and MySQL would read that as numeric
    addition, not an interval.
    """
    days = int(settings.APPLICANT_RETENTION_DAYS)
    return func.date_add(func.now(), text(f"INTERVAL {days} DAY"))


async def create(db: AsyncSession, fields: dict[str, Any]) -> Applicant:
    """Insert one submission and commit.

    Commits here rather than leaving it to the caller: get_db() deliberately
    does not auto-commit, and this row must be durable before the endpoint
    queues the notification email — otherwise a mail can go out about a
    submission that was never stored.

    The retention window is whatever APPLICANT_RETENTION_DAYS said at the
    moment of submission; changing the setting later moves the deadline for new
    rows only, which is what makes the policy auditable.
    """
    # Ambient request metadata, stamped here so every caller gets it for free.
    # "-" is the contextvar's default outside a request (a script, a test).
    request_id = request_id_ctx.get()
    fields.setdefault("request_id", None if request_id == "-" else request_id)
    # The mail goes out after the response; this is what it starts as.
    fields.setdefault("mail_status", "pending")

    applicant = Applicant(**fields, expires_at=_expires_at_expression())
    db.add(applicant)
    await db.commit()
    # The expression above is server-side, so the in-memory object still holds
    # a SQL construct until it is read back.
    await db.refresh(applicant)
    return applicant


async def set_mail_status(db: AsyncSession, applicant_id: int, status: str) -> None:
    """Record the outcome of the notification email.

    Called from the background mail task, which runs after the response — the
    request's session is closed by then, so the caller must open its own.
    """
    await db.execute(
        update(Applicant).where(Applicant.id == applicant_id).values(mail_status=status)
    )
    await db.commit()


async def mark_converted(db: AsyncSession, applicant_id: int, client_id: int) -> None:
    """Link an applicant to the client they became, and stop the clock.

    Clearing expires_at is the point: a lead who signs up on day 45 must not
    have had their intake purged on day 30. NULL is never collected.
    """
    await db.execute(
        update(Applicant)
        .where(Applicant.id == applicant_id)
        .values(client_id=client_id, expires_at=None)
    )
    await db.commit()


async def purge_expired(db: AsyncSession, *, batch_size: int = 1000) -> int:
    """Delete rows past their retention date. Returns how many went.

    Batched in a loop rather than one unbounded DELETE: a backlog (the purge
    not having run for a month, say) would otherwise hold a long lock on a
    shared MySQL instance. `expires_at IS NULL` is excluded by the comparison
    itself — NULL <= NOW() is never true — but it is written out because that
    exemption is the whole retention policy, not an implementation detail.
    """
    total = 0
    while True:
        ids = (
            await db.execute(
                select(Applicant.id)
                .where(
                    Applicant.expires_at.is_not(None),
                    # func.now(), not Python's clock: expires_at was written by
                    # the server's clock, so it must be compared against the
                    # same one. See _expires_at_expression().
                    Applicant.expires_at <= func.now(),
                )
                .limit(batch_size)
            )
        ).scalars().all()
        if not ids:
            return total

        await db.execute(delete(Applicant).where(Applicant.id.in_(ids)))
        await db.commit()
        total += len(ids)


async def list_recent(db: AsyncSession, *, limit: int = 100) -> list[Applicant]:
    """Newest submissions first — the shape an admin listing needs."""
    return list(
        (
            await db.execute(
                select(Applicant).order_by(Applicant.created_at.desc()).limit(limit)
            )
        ).scalars().all()
    )
