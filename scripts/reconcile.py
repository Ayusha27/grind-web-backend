# scripts/reconcile.py
"""Row counts + a checksum per table, to compare against the MySQL source."""
import asyncio
import sys
from pathlib import Path

# Run as "python scripts/reconcile.py": Python puts scripts/ on sys.path,
# not the project root, so "app" would not be importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from sqlalchemy import text

from app.db.session import engine

CHECKS = {
    "clients": "SELECT COUNT(*), COALESCE(SUM(id),0), COALESCE(MAX(id),0) FROM clients",
    "workout_plans": "SELECT COUNT(*), COALESCE(SUM(id),0), COALESCE(MAX(id),0) FROM workout_plans",
    "workout_days": "SELECT COUNT(*), COALESCE(SUM(id),0), COALESCE(MAX(id),0) FROM workout_days",
    "workout_exercises": "SELECT COUNT(*), COALESCE(SUM(id),0), COALESCE(MAX(id),0) FROM workout_exercises",
    "workout_logs": "SELECT COUNT(*), COALESCE(SUM(id),0), COALESCE(MAX(id),0) FROM workout_logs",
    "client_progress": "SELECT COUNT(*), COALESCE(SUM(id),0), COALESCE(MAX(id),0) FROM client_progress",
    "diet_plans": "SELECT COUNT(*), COALESCE(SUM(id),0), COALESCE(MAX(id),0) FROM diet_plans",
    "enrollments": "SELECT COUNT(*), COALESCE(SUM(id),0), COALESCE(MAX(id),0) FROM enrollments",
    "affiliate_codes": "SELECT COUNT(*), COALESCE(SUM(id),0), COALESCE(MAX(id),0) FROM affiliate_codes",
}

EXPECTED_COUNTS = {  # from the dumps; AUTO_INCREMENT - 1 where contiguous
    "clients": 15,
    "workout_plans": 42,
    "workout_days": 155,
    "workout_exercises": 927,
    "workout_logs": 1,
    "client_progress": 2,
    "diet_plans": 25,
    "enrollments": 1,
    "affiliate_codes": 15,
}


async def main() -> None:
    failures = 0
    async with engine.connect() as conn:
        for table, sql in CHECKS.items():
            count, id_sum, id_max = (await conn.execute(text(sql))).one()
            expected = EXPECTED_COUNTS.get(table)
            ok = expected is None or count == expected
            failures += 0 if ok else 1
            flag = "OK " if ok else "FAIL"
            print(f"{flag} {table:22} rows={count:<6} sum(id)={id_sum:<8} max(id)={id_max}")
    await engine.dispose()
    print("\nreconciliation:", "PASSED" if failures == 0 else f"{failures} MISMATCH(ES)")


def _run(coro) -> None:
    # psycopg's async mode cannot run on Windows' default ProactorEventLoop.
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(coro)
    else:
        asyncio.run(coro)


_run(main())
