# app/services/diet_service.py
import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_json_is_falsy, php_trim
from app.core.exceptions import ValidationFailure
from app.repositories import client_repo, diet_repo

logger = logging.getLogger(__name__)


async def save_diet_plan(
    db: AsyncSession, *, access_token: Any, diet_json: str
) -> dict[str, Any]:
    """PR-27 — add_diet.php.

    The client is resolved by ACCESS TOKEN, not id — that is how the admin
    UI worked and it is what the operator types.

    The PHP had NO transaction: it deactivated the old plans, and if the
    INSERT then failed the client was left with NO active diet at all. We
    wrap both in one transaction. A successful run is byte-identical; only
    the failure mode improves.
    """
    token = php_trim(access_token)
    client = await client_repo.get_by_access_token(db, token)
    if client is None:
        # PHP: die("Invalid Access Token") — plain text, HTTP 200.
        raise ValidationFailure("Invalid Access Token")

    try:
        data = json.loads(diet_json)
    except (json.JSONDecodeError, TypeError):
        raise ValidationFailure("Invalid JSON") from None
    if php_json_is_falsy(data):
        raise ValidationFailure("Invalid JSON")

    await diet_repo.deactivate_all(db, client.id)
    plan = await diet_repo.insert_plan(
        db,
        client_id=client.id,
        plan_name=data.get("plan_name") if isinstance(data, dict) else None,
        diet_json=diet_json,   # RAW string — re-serialising would reorder keys
    )
    await db.commit()

    logger.info("diet_saved", extra={"client_id": client.id, "diet_plan_id": plan.id})
    return {
        "success": True,
        "message": "Diet Plan Saved Successfully",
        "diet_plan_id": plan.id,
    }
