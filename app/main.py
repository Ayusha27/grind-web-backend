# app/main.py
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.legacy import router as legacy_router
from app.api.v1 import api_router
from app.cache.redis import close_redis, get_redis
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.core.responses import ORJSONResponse
from app.db.session import SessionLocal, dispose_engine
from app.integrations.razorpay_client import razorpay_client
from app.middleware import RequestContextMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging(settings.LOG_LEVEL, json_output=settings.IS_PROD)
    logger.info("startup", extra={"env": settings.ENV, "app": settings.APP_NAME})

    # Warm the pool so the first user request does not pay connection setup.
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
    logger.info("db_ready")

    try:
        await get_redis().ping()
        logger.info("redis_ready")
    except Exception:
        # Cache is optional by design (Phase 6.2) — boot anyway, degraded.
        logger.warning("redis_unavailable_at_startup")

    yield

    # Ordered shutdown: stop outbound HTTP, then cache, then the DB pool, so
    # in-flight work can still finish writing.
    await razorpay_client.aclose()
    await close_redis()
    await dispose_engine()
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        lifespan=lifespan,
        default_response_class=ORJSONResponse,   # 2-4x faster serialisation
        # No interactive docs in production: they advertise every endpoint
        # and their schemas to anyone who finds the host.
        docs_url=None if settings.IS_PROD else "/docs",
        redoc_url=None,
        openapi_url=None if settings.IS_PROD else "/openapi.json",
    )

    # Middleware runs bottom-up on the way in. Request context must be
    # OUTERMOST so every other layer, including error handlers, has the id.
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,   # never "*" with credentials
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=3600,                          # cache preflights for an hour
    )
    if settings.TRUSTED_HOSTS != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    app.include_router(legacy_router)   # .php aliases, unprefixed

    @app.get("/health", tags=["ops"], include_in_schema=False)
    async def health() -> dict:
        """LIVENESS. Must touch NOTHING external.

        If this checked the database, a brief DB blip would make the
        orchestrator kill every healthy pod — turning a 10-second database
        hiccup into a full outage.
        """
        return {"status": "ok", "env": settings.ENV}

    @app.get("/health/ready", tags=["ops"], include_in_schema=False)
    async def ready() -> dict:
        """READINESS. Checks dependencies, so a pod that cannot serve is
        removed from the load balancer without being restarted.
        """
        checks: dict[str, str] = {}
        try:
            async with SessionLocal() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            logger.exception("readiness_db_failed")
            checks["database"] = "error"

        try:
            await get_redis().ping()
            checks["redis"] = "ok"
        except Exception:
            # Degraded, NOT unready — the app serves correctly without cache.
            checks["redis"] = "degraded"

        status = "ok" if checks["database"] == "ok" else "error"
        return {"status": status, "checks": checks}

    return app


app = create_app()
