# app/repositories/diet_repo.py
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DietPlan


async def get_active_plan(db: AsyncSession, client_id: int) -> DietPlan | None:
    """PHP: WHERE client_id=? AND is_active=1 ORDER BY id DESC LIMIT 1"""
    stmt = (
        select(DietPlan)
        .where(DietPlan.client_id == client_id, DietPlan.is_active.is_(True))
        .order_by(DietPlan.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def deactivate_all(db: AsyncSession, client_id: int) -> None:
    """PR-27 first half."""
    await db.execute(
        update(DietPlan).where(DietPlan.client_id == client_id).values(is_active=False)
    )


async def insert_plan(
    db: AsyncSession, *, client_id: int, plan_name: str | None, diet_json: str
) -> DietPlan:
    """PR-27 second half. diet_json is stored as the RAW request string,
    not a re-serialised dict — re-serialising would reorder keys and change
    whitespace, so the stored bytes would differ from the legacy system.
    """
    plan = DietPlan(
        client_id=client_id, plan_name=plan_name, diet_json=diet_json, is_active=True
    )
    db.add(plan)
    await db.flush()
    return plan
