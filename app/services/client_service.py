# app/services/client_service.py
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import letters_only, php_str_pad, php_trim
from app.core.exceptions import NotFoundError, ValidationFailure
from app.db.models import Client
from app.repositories import client_repo

logger = logging.getLogger(__name__)


def build_access_token(name: str | None, client_id: int) -> str:
    """PR-22 — the exact PHP token algorithm.

        strtoupper(substr(str_pad(preg_replace('/[^A-Za-z]/','',$name), 3, 'X'), 0, 3))

    str_pad's default direction is RIGHT, which is the trap:
        "Al"      -> "AlX"   -> "ALX"      (not "XAL")
        "Bo Xu"   -> "BoXu"  -> "BOX"
        ""        -> "XXX"   -> "XXX"
        "Arup"    -> "Arup"  -> "ARU"
        "123"     -> ""      -> "XXX"      (digits stripped first)
    Then: "GR_" + prefix + "_" + str_pad(id, 6, "0", STR_PAD_LEFT)
    """
    prefix = php_str_pad(letters_only(name or ""), 3, "X")[:3].upper()
    suffix = php_str_pad(str(client_id), 6, "0", left=True)
    return f"GR_{prefix}_{suffix}"


def _client_dict(client: Client) -> dict[str, Any]:
    return {
        "id": client.id,
        "name": client.name,
        "email": client.email,
        "phone": client.phone,
        "goal": client.goal,
        "status": client.status,
        "created_at": client.created_at.strftime("%Y-%m-%d %H:%M:%S")
        if client.created_at
        else None,
        "access_token": client.access_token,
    }


async def upsert_client(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """PR-21, PR-22 — the POST branch of clients.php.

    The PHP trims ONLY the email; name/phone/goal go in raw. Preserved.
    """
    email = php_trim(payload.get("email"))
    name = payload.get("name")
    phone = payload.get("phone")
    goal = payload.get("goal")

    if email == "":
        raise ValidationFailure("Email is required")

    existing = await client_repo.get_by_email(db, email)

    if existing is not None:
        # PR-21 — update name/phone/goal only. access_token is NOT
        # regenerated; doing so would invalidate every saved portal link.
        await client_repo.update_details(
            db, email=email, name=name, phone=phone, goal=goal
        )
        await db.commit()
        refreshed = await client_repo.get_by_email(db, email)
        assert refreshed is not None
        return {"success": True, "created": False, "client": _client_dict(refreshed)}

    client = await client_repo.insert(
        db, name=name, email=email, phone=phone, goal=goal
    )
    token = build_access_token(name if isinstance(name, str) else "", client.id)
    await client_repo.set_access_token(db, client_id=client.id, token=token)
    await db.commit()

    logger.info("client_created", extra={"client_id": client.id, "email": email})

    refreshed = await client_repo.get_by_id(db, client.id)
    assert refreshed is not None
    return {"success": True, "created": True, "client": _client_dict(refreshed)}


async def list_clients(db: AsyncSession) -> dict[str, Any]:
    clients = await client_repo.list_all(db)
    return {"success": True, "data": [_client_dict(c) for c in clients]}


async def get_client(db: AsyncSession, client_id: int) -> dict[str, Any]:
    client = await client_repo.get_by_id(db, client_id)
    if client is None:
        # client-details.php crashed with an undefined-index warning here and
        # rendered an empty page. A 404 is the only sane equivalent.
        raise NotFoundError("Client not found")
    return {"success": True, "data": _client_dict(client)}


def _to_decimal(value: Any) -> Decimal | None:
    """add-progress.php passed raw form values straight into decimal(5,2)
    columns. MySQL coerced "" to 0.00 with a warning; Postgres raises.
    Empty becomes NULL, which is what an unfilled measurement means.
    """
    text = php_trim(value)
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValidationFailure(f"Invalid numeric value: {text}") from exc


async def add_progress(
    db: AsyncSession, *, client_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """admin/add-progress.php."""
    client = await client_repo.get_by_id(db, client_id)
    if client is None:
        raise NotFoundError("Client not found")

    await client_repo.insert_progress(
        db,
        client_id=client_id,
        weight=_to_decimal(payload.get("weight")),
        waist=_to_decimal(payload.get("waist")),
        chest=_to_decimal(payload.get("chest")),
        arms=_to_decimal(payload.get("arms")),
        thighs=_to_decimal(payload.get("thighs")),
        notes=payload.get("notes"),
    )
    await db.commit()
    return {"success": True, "message": "Progress Saved"}
