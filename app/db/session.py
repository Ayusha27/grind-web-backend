import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger(__name__)


def _connect_args() -> dict[str, object]:
    args: dict[str, object] = {
        # Server-side ceiling. A slow query can never hold a pool slot for
        # longer than this, so one bad statement cannot cascade (Phase 2.1).
        "options": f"-c statement_timeout={settings.DB_STATEMENT_TIMEOUT_MS}",
    }
    if settings.DB_USE_PGBOUNCER:
        # PgBouncer transaction mode hands you a different backend per
        # transaction, so server-side prepared statements break with
        # "prepared statement _pg3_0 already exists". Disabling them is
        # mandatory, not optional, once PgBouncer is in front.
        args["prepare_threshold"] = None
    return args


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
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


async def dispose_engine() -> None:
    await engine.dispose()
    logger.info("db_pool_disposed")