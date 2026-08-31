# app/repositories/client_repo.py
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Client, ClientProgress


async def get_by_id(db: AsyncSession, client_id: int) -> Client | None:
    return (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalars().first()


async def get_by_email(db: AsyncSession, email: str) -> Client | None:
    """PR-21 — the upsert key is email, not id."""
    return (
        await db.execute(select(Client).where(Client.email == email))
    ).scalars().first()


async def get_by_access_token(db: AsyncSession, token: str) -> Client | None:
    """PR-27 and Decision 5 — the client portal identifies by token."""
    return (
        await db.execute(
            select(Client).where(Client.access_token == token).limit(1)
        )
    ).scalars().first()


async def list_all(db: AsyncSession) -> list[Client]:
    """PHP: SELECT * FROM clients ORDER BY id DESC"""
    return list(
        (await db.execute(select(Client).order_by(Client.id.desc()))).scalars().all()
    )


async def insert(
    db: AsyncSession, *, name: str | None, email: str, phone: str | None, goal: str | None
) -> Client:
    client = Client(name=name, email=email, phone=phone, goal=goal)
    db.add(client)
    await db.flush()   # PHP lastInsertId(), needed to build the token
    return client


async def update_details(
    db: AsyncSession, *, email: str, name: str | None, phone: str | None, goal: str | None
) -> None:
    """PR-21 existing-client branch: name/phone/goal only.

    access_token is deliberately NOT touched — regenerating it would break
    every client's saved portal link.
    """
    await db.execute(
        update(Client)
        .where(Client.email == email)
        .values(name=name, phone=phone, goal=goal)
    )


async def set_access_token(db: AsyncSession, *, client_id: int, token: str) -> None:
    await db.execute(
        update(Client).where(Client.id == client_id).values(access_token=token)
    )


# ── client_progress ────────────────────────────────────────────────

async def insert_progress(
    db: AsyncSession,
    *,
    client_id: int,
    weight: Decimal | None,
    waist: Decimal | None,
    chest: Decimal | None,
    arms: Decimal | None,
    thighs: Decimal | None,
    notes: str | None,
) -> ClientProgress:
    row = ClientProgress(
        client_id=client_id, weight=weight, waist=waist, chest=chest,
        arms=arms, thighs=thighs, notes=notes,
    )
    db.add(row)
    await db.flush()
    return row


async def get_progress_history(db: AsyncSession, client_id: int) -> list[ClientProgress]:
    """PR-32.

    PHP ran THREE queries: newest (DESC LIMIT 1), oldest (ASC LIMIT 1), and the
    full history (ASC). The history already contains the other two, so one
    query gives identical results in a third of the round trips.
    `id` breaks ties on identical timestamps, which MySQL left undefined.
    """
    stmt = (
        select(ClientProgress)
        .where(ClientProgress.client_id == client_id)
        .order_by(ClientProgress.created_at.asc(), ClientProgress.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())
