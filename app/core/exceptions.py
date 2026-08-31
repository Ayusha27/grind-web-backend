import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_ctx
from app.core.responses import ORJSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base for every deliberate, user-visible failure.

    `message` is what the client sees — it is chosen to match the legacy
    PHP string exactly wherever a legacy string existed.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class ValidationFailure(AppError):
    status_code = status.HTTP_400_BAD_REQUEST


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND


class AuthError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class ExternalServiceError(AppError):
    """Razorpay / SMTP failed. Detail is logged, never returned."""

    status_code = status.HTTP_502_BAD_GATEWAY


class DatabaseError(AppError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


def _envelope(message: str, status_code: int) -> ORJSONResponse:
    """The legacy shape, unchanged, so the React client needs no edits."""
    return ORJSONResponse(
        status_code=status_code,
        content={"success": False, "message": message},
        headers={"X-Request-ID": request_id_ctx.get()},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> ORJSONResponse:
        # Expected failures: log at INFO, they are not incidents.
        logger.info("app_error", extra={"error": exc.message, "status": exc.status_code})
        return _envelope(exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> ORJSONResponse:
        logger.info("validation_error", extra={"errors": exc.errors()[:5]})
        return _envelope("Invalid request payload", status.HTTP_422_UNPROCESSABLE_CONTENT)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> ORJSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return _envelope(detail, exc.status_code)

    @app.exception_handler(SQLAlchemyError)
    async def _db(_: Request, exc: SQLAlchemyError) -> ORJSONResponse:
        # Real incident: full traceback to the log, generic text to the caller.
        logger.exception("database_error", extra={"error_type": type(exc).__name__})
        return _envelope("A database error occurred.", status.HTTP_500_INTERNAL_SERVER_ERROR)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> ORJSONResponse:
        logger.exception("unhandled_error", extra={"error_type": type(exc).__name__})
        return _envelope("Internal server error", status.HTTP_500_INTERNAL_SERVER_ERROR)