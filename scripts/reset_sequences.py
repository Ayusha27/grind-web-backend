# scripts/reset_sequences.py
import asyncio
import sys
from pathlib import Path

# Run as "python scripts/reset_sequences.py": Python puts scripts/ on sys.path,
# not the project root, so "app" would not be importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from sqlalchemy import text

from app.db.session import engine

TABLES = [
    "clients", "client_progress", "workout_plans", "workout_days",
    "workout_exercises", "workout_logs", "workout_progress", "diet_plans",
    "enrollments", "affiliate_codes", "affiliate_conversions",
]


async def main() -> None:
    async with engine.begin() as conn:
        for table in TABLES:
            # `table` is interpolated because a table name is an identifier and
            # cannot be bound as a parameter. It comes from the TABLES constant
            # above, never from input, so there is no injection surface.
            sql = (
                "SELECT setval(pg_get_serial_sequence(:t, 'id'), "  # noqa: S608
                "COALESCE((SELECT MAX(id) FROM " + table + "), 1), true)"
            )
            await conn.execute(text(sql), {"t": table})
            print(f"sequence reset: {table}")
    await engine.dispose()


def _run(coro) -> None:
    # psycopg's async mode cannot run on Windows' default ProactorEventLoop.
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(coro)
    else:
        asyncio.run(coro)


_run(main())
