# app/api/v1/admin.py
from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, DbSession
from app.cache.rate_limit import limit_login
from app.cache.redis import invalidate_client_caches
from app.core.config import settings
from app.core.exceptions import AuthError
from app.core.security import create_access_token, verify_admin_credentials
from app.repositories import client_repo
from app.schemas.admin import (
    AdminLoginRequest,
    AdminLoginResponse,
    ClientUpsertRequest,
    DietSaveRequest,
    PlanCreateRequest,
    PlanImportRequest,
    ProgressCreateRequest,
)
from app.services import client_service, diet_service, plan_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/login", response_model=AdminLoginResponse,
             dependencies=[Depends(limit_login)])
async def login(payload: AdminLoginRequest) -> AdminLoginResponse:
    """Replaces the plaintext comparison in login.php.

    The failure message is deliberately identical for a bad username and a
    bad password, so it cannot be used to enumerate valid usernames.
    """
    if not verify_admin_credentials(payload.username, payload.password):
        raise AuthError("Invalid Login")   # legacy string, preserved

    return AdminLoginResponse(
        access_token=create_access_token(payload.username),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/clients")
async def list_clients(db: DbSession, _: AdminUser) -> dict:
    return await client_service.list_clients(db)


@router.post("/clients")
async def upsert_client(
    payload: ClientUpsertRequest, db: DbSession, _: AdminUser
) -> dict:
    """PR-21, PR-22."""
    return await client_service.upsert_client(db, payload.model_dump())


@router.get("/clients/{client_id}")
async def client_details(client_id: int, db: DbSession, _: AdminUser) -> dict:
    return await client_service.get_client(db, client_id)


@router.post("/plans")
async def create_plan(payload: PlanCreateRequest, db: DbSession, _: AdminUser) -> dict:
    """PR-23, PR-25 — the non-versioned legacy path."""
    result = await plan_service.create_plan(
        db,
        client_id=payload.client_id,
        plan_name=payload.plan_name,
        workout_json=payload.workout_json,
    )
    await invalidate_client_caches(payload.client_id)
    return result


@router.post("/plans/import")
async def import_plan(payload: PlanImportRequest, db: DbSession, _: AdminUser) -> dict:
    """PR-24..PR-26 — the versioned, transactional path."""
    result = await plan_service.import_plan(
        db, client_id=payload.client_id, workout_json=payload.workout_json
    )
    # Cache invalidation must include the client's portal key, so fetch the
    # token. Importing also changes PR-28's global denominator for EVERYONE,
    # which invalidate_client_caches handles by dropping the count key.
    client = await client_repo.get_by_id(db, payload.client_id)
    await invalidate_client_caches(
        payload.client_id, client.access_token if client else None
    )
    return result


@router.post("/diet")
async def save_diet(payload: DietSaveRequest, db: DbSession, _: AdminUser) -> dict:
    """PR-27."""
    result = await diet_service.save_diet_plan(
        db, access_token=payload.access_token, diet_json=payload.diet_json
    )
    client = await client_repo.get_by_access_token(db, payload.access_token.strip())
    if client:
        await invalidate_client_caches(client.id, client.access_token)
    return result


@router.post("/progress")
async def add_progress(
    payload: ProgressCreateRequest, db: DbSession, _: AdminUser
) -> dict:
    return await client_service.add_progress(
        db,
        client_id=payload.client_id,
        payload=payload.model_dump(exclude={"client_id"}),
    )
