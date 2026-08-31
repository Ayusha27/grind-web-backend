# app/api/deps.py
import logging
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthError, ValidationFailure
from app.core.security import decode_access_token
from app.db.models import Client
from app.db.session import get_db
from app.repositories import client_repo

logger = logging.getLogger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


async def body_params(request: Request) -> dict[str, Any]:
    """Accept form-encoded (legacy) OR JSON (React), transparently."""
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("application/json"):
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    if content_type.startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    ):
        form = await request.form()
        result: dict[str, Any] = {}
        for key in form.keys():
            values = form.getlist(key)
            # `goals[]` and `injuries[]` arrive as repeated keys (PR-33).
            result[key.removesuffix("[]")] = values if len(values) > 1 else values[0]
        return result

    # No content-type (curl -d without a header) — try JSON, then give up.
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


BodyParams = Annotated[dict[str, Any], Depends(body_params)]


async def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Guards every admin route.

    Decision 3: import-workout.php, add_diet.php and affiliate-dashboard.php
    had NO auth in the legacy system. Set LEGACY_OPEN_ADMIN=true to restore
    that, only if an unauthenticated caller (cron, bookmark) depends on it.
    """
    if settings.LEGACY_OPEN_ADMIN:
        return "legacy-open"

    if credentials is None or not credentials.credentials:
        raise AuthError("Not authenticated")

    payload = decode_access_token(credentials.credentials)
    if payload.get("typ") != "admin":
        raise AuthError("Invalid credentials")
    return str(payload.get("sub", ""))


AdminUser = Annotated[str, Depends(require_admin)]


async def require_client(request: Request, db: DbSession) -> Client:
    """Portal auth. The access token may arrive as ?token= (legacy links that
    clients have bookmarked) or as a Bearer header (the SPA).
    """
    token = request.query_params.get("token", "")
    if not token:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:]

    client = await client_repo.get_by_access_token(db, token.strip())
    if client is None:
        # PHP: die("Invalid Access Link")
        raise ValidationFailure("Invalid Access Link")
    return client


CurrentClient = Annotated[Client, Depends(require_client)]
