import logging
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger(__name__)


def _statement_timeout_command() -> str:
    """Server-side ceiling, as an init_command run on every new connection.

    Same guarantee the Postgres `-c statement_timeout` option gave us: a slow
    query can never hold a pool slot for longer than this, so one bad statement
    cannot cascade (Phase 2.1). MySQL and MariaDB spell it differently and take
    different units, hence DB_SERVER_FLAVOR.
    """
    ms = settings.DB_STATEMENT_TIMEOUT_MS
    if settings.DB_SERVER_FLAVOR == "mariadb":
        # MariaDB: max_statement_time, a double in SECONDS.
        return f"SET SESSION max_statement_time={ms / 1000}"
    # MySQL 5.7.8+ / 8.x: max_execution_time, an integer in MILLISECONDS.
    # Caveat worth knowing: MySQL applies it to read-only SELECTs only, so it
    # is a weaker guarantee than Postgres' statement_timeout was.
    return f"SET SESSION max_execution_time={ms}"


def _connect_args() -> dict[str, object]:
    """Arguments forwarded to PyMySQL's Connection by the aiomysql dialect.

    This is the pymysql.connect(...) call: host, port, user, password and
    database come from the URL that SQLAlchemy parses out of
    settings.DATABASE_URL, and the rest are passed through from here.
    """
    args: dict[str, object] = {
        # pymysql.connect(connect_timeout=10) — seconds, socket connect only.
        "connect_timeout": settings.DB_CONNECT_TIMEOUT,
    }
    if settings.DB_STATEMENT_TIMEOUT_MS > 0:
        args["init_command"] = _statement_timeout_command()
    return args


# The one and only engine. Driver is aiomysql, which is PyMySQL's asyncio
# wrapper (it imports pymysql for the wire protocol), so every downstream
# AsyncSession, repository and service keeps working untouched.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    # MySQL closes idle connections after wait_timeout (often 300s or less on
    # shared hosting, vs Postgres leaving them open). Recycling below that
    # ceiling is what prevents "MySQL server has gone away" on a quiet pool.
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
    connect_args=_connect_args(),
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # objects stay usable after commit, for serialisation
    autoflush=False,         # we control exactly when SQL is emitted
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped session.

    Deliberately does NOT auto-commit: read endpoints (95% of traffic per
    Phase 0.2) would pay a pointless COMMIT round trip. Services that write
    call `await db.commit()` explicitly, which also makes the transaction
    boundary visible in the code that owns the business rule.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def ensure_applicants_table() -> bool:
    """Create `applicants` if it is missing; leave it untouched if it exists.

    Returns True if this call created the table.

    Scoped to one table on purpose. A bare create_all() would try to
    materialise every model in Base.metadata against a live legacy database
    that already owns those tables, which is not a thing to do at boot.

    RACE: gunicorn starts one worker per core and they boot together, so two
    workers can both see "absent" and both issue CREATE TABLE — checkfirst
    does has_table THEN create, which is not atomic. The loser gets MySQL 1050
    "table already exists", which is the outcome we wanted anyway, so it is
    swallowed. Any other DDL failure is logged and re-raised: a missing table
    means every intake submission fails, and that should stop the boot.

    LIMITATION: this only ever creates. A table that already exists with an
    older column set is used as-is and inserts fail later on the missing
    column; create-if-missing cannot detect drift. The Alembic revision is the
    answer for a managed environment — set APPLICANTS_AUTO_CREATE=false there.
    """
    from app.db.models import Applicant  # local: avoids a circular import at module load

    def _create(connection: Any) -> bool:
        inspector = sa_inspect(connection)
        if inspector.has_table(Applicant.__tablename__):
            return False
        Applicant.__table__.create(connection)
        return True

    async with engine.begin() as connection:
        try:
            return await connection.run_sync(_create)
        except (ProgrammingError, OperationalError) as exc:
            # 1050 = ER_TABLE_EXISTS_ERROR. Another worker won the race.
            if getattr(exc.orig, "args", (None,))[0] == 1050:
                logger.info("applicants_table_created_by_peer")
                return False
            logger.exception("applicants_table_create_failed")
            raise


async def dispose_engine() -> None:
    await engine.dispose()
    logger.info("db_pool_disposed")