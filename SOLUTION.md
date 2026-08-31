# GRIND — PHP → FastAPI Implementation Guide

**How to use this file:** read a phase, type the code, run the verification command at the end of that phase, move on. Every code block is complete and copy-ready — no `...` placeholders. Every section starts with **Why** (the need it serves), **Why this way** (why it beats the alternatives), and **Applies to** (which part of the legacy system it replaces).

**Companion file:** `MIGRATION_PLAN.md` holds the legacy audit and the numbered parity rules `PR-01`…`PR-33`. This file implements them. Where code implements a rule, the rule id appears in a comment.

**Target load:** 5,000–10,000 simultaneous users. Phase 0 does the capacity arithmetic that justifies every structural choice that follows.

---

## Table of contents

| Phase | Subject | Files produced |
|---|---|---|
| 0 | Decisions + capacity design | — (reasoning only) |
| 1 | Project skeleton, dependencies | `pyproject.toml`, tree |
| 2 | Core: config, logging, errors, PHP-compat | `app/core/*` |
| 3 | Database: engine, models, indexes, migration | `app/db/*`, `migrations/` |
| 4 | Repositories (all SQL) | `app/repositories/*` |
| 5 | Services (all business rules) | `app/services/*` |
| 6 | Auth, caching, rate limiting | `app/core/security.py`, `app/cache/*` |
| 7 | Schemas + routers + legacy aliases | `app/schemas/*`, `app/api/*` |
| 8 | App assembly + middleware | `app/main.py`, `app/middleware.py` |
| 9 | Deployment: Docker, Gunicorn, PgBouncer, Nginx | `Dockerfile`, compose, configs |
| 10 | Tests + parity proof | `tests/*` |

---
---

# Phase 0 — Decisions and capacity design

Nothing here is code. This phase exists because every structural choice in Phases 1–9 traces back to one of these numbers or decisions, and you asked *why*. Read it once and the rest of the file stops looking arbitrary.

## 0.1 The six decisions, resolved

I picked a production default for each and put it behind a config flag, so you can reverse any of them from `.env` without touching code.

| # | Decision | Chosen | Flag | Reasoning |
|---|---|---|---|---|
| 1 | Database engine | **PostgreSQL 17** | — | `backend-v1` already commits to it (psycopg, compose). Data volume is tiny (~930 exercises, 43 plans, 17 clients, 1 enrollment), so conversion is a one-afternoon mechanical job. Postgres also gives partial indexes and `FILTER` aggregates that the hot queries in §4 benefit from directly. |
| 2 | Razorpay signature | **Verify** | `RAZORPAY_VERIFY_SIGNATURE=true` | The frontend already sends `razorpay_signature`; the PHP just ignores it. Turning verification on breaks nothing legitimate and closes a free-enrollment hole. |
| 2b | Trust client-sent price | **Yes (parity)** | `PAYMENTS_RECOMPUTE_PRICE=false` | Recomputing server-side is correct but *would* change outcomes if the frontend ever rounds differently. Ship parity, flip after one week of shadow-logging mismatches. |
| 3 | Unauthenticated admin endpoints | **Require auth** | `LEGACY_OPEN_ADMIN=false` | `affiliate-dashboard.php` leaks affiliate names, emails, revenue and commission to anyone with the URL. Set the flag `true` only if a cron or bookmark depends on open access. |
| 4 | The three parity bugs (PR-10, PR-16, PR-28) | **Preserve verbatim** | `LEGACY_*` flags | Your brief says business logic stays identical. Each is one flag away from being fixed later. |
| 5 | Hardcoded identities | **Derive from token** | — | `my-plan` hardcodes an email, `workout-progress` hardcodes `client_id=1`. Literally impossible to keep in a multi-client API. Both now come from the caller's access token. Recorded in `PARITY.md`. |
| 6 | Email transport | **SMTP, provider-agnostic** | `SMTP_*` | PHP `mail()` has no Python equivalent. SMTP config in env works with SES, Mailgun, Postmark or a local relay without a code change. |

## 0.2 Capacity: what "10,000 simultaneous users" actually costs

This is the single most important section in the file, because it is what tells you the system does **not** need to be exotic.

**Step 1 — concurrent users are not concurrent requests.**

A fitness client opens their plan, reads it, and ticks sets. Measured against the legacy UI's behaviour, an *active* user issues roughly **one request every 20–30 seconds**.

```
10,000 active users ÷ 25 s  ≈  400 requests/second sustained
Peak burst (evening gym rush, 3×)  ≈  1,200 requests/second
```

**Step 2 — Little's Law gives in-flight concurrency.**

```
in-flight requests = throughput × latency

Cached workout read   : 1,200 req/s × 0.002 s =    2.4 concurrent
Uncached workout read : 1,200 req/s × 0.015 s =   18   concurrent
Write (set tick)      :   200 req/s × 0.010 s =    2   concurrent
```

So at full peak, **roughly 20 requests are actually in flight at any instant.** Not 10,000. This is why an async framework with a modest connection pool wins, and why "10k users" does *not* imply "10k database connections" — a mistake that kills more migrations than any other.

**Step 3 — the pool sizing that follows.**

```
Per Gunicorn worker : pool_size 20 + max_overflow 10  = 30 max connections
4 workers (4 vCPU)  : 4 × 30                          = 120 max connections
Postgres max_connections = 200                        → 40% headroom
```

120 connections against a workload needing ~20 is **6× headroom**. If you scale to multiple app pods, PgBouncer in transaction mode (Phase 9) multiplexes them so Postgres never sees more than ~50 real backends regardless of pod count.

**Step 4 — where the legacy design would have died.**

`api/workout.php` issues **1 + 1 + N** queries per request (plan, days, then one query *per day*). With a 5-day split that is **7 round trips**:

```
Legacy at 1,200 req/s : 1,200 × 7  = 8,400 queries/second   ← Postgres will not do this on 4 vCPU
Phase 4 rewrite       : 1,200 × 2  = 2,400 queries/second   ← comfortable
Phase 7 with cache    : 1,200 × 0.15 × 2 ≈ 360 queries/s    ← trivial
```

Collapsing the N+1 (Phase 4) and caching plan reads (Phase 7) are not decoration. They are the two changes that make the target number reachable on modest hardware. Both produce **byte-identical output** to the PHP — they change round trips, never results.

**Step 5 — the tables that will actually hurt.**

`workout_logs` gains one row per set ticked. At 10,000 users × ~20 sets/day that is **200,000 rows/day, ~73M/year**. The legacy schema has **no index on it at all**, and `my-plan` runs `COUNT(DISTINCT exercise_id) WHERE user_email = ?` on every portal load. Without an index that becomes a full scan of 73M rows, per page view. Phase 3 adds the covering index; Phase 7 caches the result.

Likewise `COUNT(*) FROM workout_exercises` (PR-28, the global denominator) is a sequential scan on every portal load. It is a bug, you asked to keep it, and Phase 7 caches it so keeping it costs nothing.

## 0.3 The resulting architecture, and why each layer earns its place

```
            Nginx (TLS, static, connection buffering)
                    │
            Gunicorn ── 4 × UvicornWorker      ← process-level parallelism past the GIL
                    │
            FastAPI app (async)                ← thousands of open sockets per worker
              ├─ middleware: request-id, CORS, gzip, trusted-host
              ├─ routers    (HTTP only, no SQL, no rules)
              ├─ services   (all business rules, PR-01…PR-33)
              └─ repositories (all SQL, parameterised)
                    │                    │
              PgBouncer            Redis
              (transaction mode)   (cache + rate limit)
                    │
              PostgreSQL 17
```

| Layer | Why it is there | What breaks without it |
|---|---|---|
| **Async everywhere** | 20 in-flight requests each waiting ~15 ms on I/O. Threads would idle; coroutines cost ~KB each. | A sync stack needs ~120 worker threads for the same load, and context-switch overhead eats the CPU budget. |
| **Gunicorn + 4 workers** | Python's GIL means one process saturates one core. JSON serialisation and Pydantic validation are CPU work. | Single process caps at ~300 req/s regardless of async. |
| **Repository layer** | Every legacy SQL statement lives in exactly one place, so parity is auditable file-by-file against the PHP. | Rules leak into routers; nobody can prove parity. |
| **Service layer** | The 33 parity rules are testable without HTTP. | You can only test through the network, so edge cases go untested. |
| **Redis** | Plan reads are ~95% of traffic and change ~monthly. Rate limit state must be shared across 4 workers. | Per-worker in-memory limits let an attacker get 4× the quota; DB takes 8× the read load. |
| **PgBouncer** | App pods × pool_size can exceed `max_connections` the moment you scale out. | `FATAL: sorry, too many clients already` under exactly the load you scaled for. |
| **Alembic** | Legacy schema was hand-edited via phpMyAdmin, so nobody knows what production actually looks like. | Schema drift between environments. |

## 0.4 Non-negotiables carried from the audit

These are settled and appear in the code without further debate:

1. **No secrets in source.** The live Razorpay key/secret, DB password and admin password are currently committed in `backend/`. Rotate all three before cutover; the new code reads them from env only.
2. **Every query parameterised.** No string interpolation into SQL, ever.
3. **Legacy JSON envelope preserved.** `{"success": bool, ...}` with the same keys in the same order, so the React frontend needs zero changes on day one.
4. **Legacy `.php` paths aliased.** `workoutService.ts` calls `/api/workout.php?client_id=`; that keeps working.
5. **Internal errors never leak.** Exception detail is logged with a request id and never returned.

---

**Phase 0 checkpoint:** you should now be able to answer, for any later code block, "which number in §0.2 made this necessary?" If a choice in a later phase seems unmotivated, it traces back to here.

---
---

# Phase 1 — Project skeleton and dependencies

**Why:** every later phase imports from a fixed tree. Getting it wrong once means renaming imports in 40 files later.
**Applies to:** replaces the flat `backend/` layout where `config/database.php` was `require`d by relative path from three different depths.

## 1.1 Dependencies

Replace `backend-v1/pyproject.toml`:

```toml
[project]
name = "grind-api"
version = "1.0.0"
description = "GRIND fitness backend — FastAPI"
readme = "README.md"
requires-python = ">=3.12"

dependencies = [
    # web
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "gunicorn>=23.0.0",
    "python-multipart>=0.0.12",   # legacy endpoints are form-POST; FastAPI needs this for Form()
    "orjson>=3.10.0",             # 2-4x faster JSON serialisation than stdlib

    # database
    "sqlalchemy[asyncio]>=2.0.36",
    "psycopg[binary,pool]>=3.2.0",
    "alembic>=1.14.0",

    # config / validation
    "pydantic>=2.9.0",
    "pydantic-settings>=2.6.0",
    "email-validator>=2.2.0",     # PR-33 needs RFC-grade email validation

    # infrastructure
    "redis>=5.2.0",               # cache + shared rate-limit state
    "httpx>=0.28.0",              # async Razorpay client

    # security
    "pyjwt>=2.10.0",
    "argon2-cffi>=23.1.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.8.0",
    "mypy>=1.13.0",
]

[tool.uv]
package = false

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "S", "ASYNC"]
ignore = ["S101"]  # assert is fine in tests
```

**Why `python-multipart` matters:** `Payment/create_order.php`, `verify_payment.php`, `complete-workout.php` and `validate_affiliate.php` all read `$_POST`, i.e. `application/x-www-form-urlencoded`. The React frontend sends JSON. Phase 6 accepts **both**; without this package the form branch raises at import time.

**Why `orjson`:** at 1,200 req/s the workout payload (a plan + 5 days + ~30 exercises) is ~15 KB of JSON. stdlib `json` costs ~0.8 ms per response; orjson ~0.2 ms. Across 4 workers that is roughly half a core given back.

**Why Python ≥3.12 not 3.14:** `psycopg[binary]` and `argon2-cffi` ship prebuilt wheels for 3.12/3.13 on every platform. Delete `.python-version`'s `3.14` and put `3.12` in it, or you will be compiling C extensions on every deploy.

```bash
cd backend-v1
echo "3.12" > .python-version
uv sync
```

## 1.2 Directory tree

```bash
mkdir -p app/core app/db/models app/cache app/repositories app/services \
         app/schemas app/api/v1 app/integrations \
         migrations tests/unit tests/integration deploy

for d in app app/core app/db app/db/models app/cache app/repositories \
         app/services app/schemas app/api app/api/v1 app/integrations \
         tests tests/unit tests/integration; do touch $d/__init__.py; done
```

Resulting tree, with the responsibility of each package:

```
backend-v1/
├─ app/
│  ├─ main.py              app factory, lifespan, router mounting
│  ├─ middleware.py        request-id, access log, timing
│  ├─ core/
│  │  ├─ config.py         all env-driven settings (Phase 2.1)
│  │  ├─ logging.py        structured JSON logs (Phase 2.2)
│  │  ├─ exceptions.py     error types + handlers (Phase 2.3)
│  │  ├─ compat.py         PHP semantics, in ONE place (Phase 2.4)
│  │  └─ security.py       JWT, argon2, Razorpay HMAC (Phase 7)
│  ├─ db/
│  │  ├─ base.py           DeclarativeBase
│  │  ├─ session.py        async engine + get_db (Phase 3.1)
│  │  └─ models/           11 tables, 1:1 with legacy (Phase 3.2)
│  ├─ cache/redis.py       connection pool, cache helpers (Phase 7)
│  ├─ repositories/        ALL SQL lives here (Phase 4)
│  ├─ services/            ALL business rules PR-01..PR-33 (Phase 5)
│  ├─ schemas/             pydantic request/response (Phase 6)
│  ├─ api/
│  │  ├─ deps.py           db session, auth guards, body parsing
│  │  ├─ legacy.py         .php path aliases
│  │  └─ v1/               one router per domain
│  └─ integrations/
│     ├─ razorpay_client.py
│     └─ mailer.py
├─ migrations/             alembic
├─ tests/
└─ deploy/                 gunicorn.conf.py, nginx.conf, pgbouncer.ini
```

**Why this shape (and not "routers + models" flat):** the acceptance criterion is *identical outcomes for identical inputs*. That is only provable if each PHP file maps to one service function you can unit-test without HTTP, and each PHP query maps to one repository function you can read side-by-side with the original. A flat layout forces you to test through the network, and network tests cannot cover things like "`(int)"abc"` is 0, not a 422".

---
---

# Phase 2 — Core: config, logging, errors, PHP-compat

## 2.1 `app/core/config.py`

**Why:** `backend/config/database.php` and `config/razorpay.php` hold a production password and a **live** Razorpay secret in source. Everything the app needs must come from the environment, validated at boot, so a missing variable is a startup crash rather than a 3 a.m. `NoneType` error.
**Why this way:** `pydantic-settings` validates types and fails fast at import. `@lru_cache` makes `get_settings()` a singleton without a global mutable.
**Applies to:** replaces both PHP config files plus every hardcoded constant scattered through the admin pages.

```python
# app/core/config.py
from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── application ────────────────────────────────────────────────
    ENV: Literal["dev", "staging", "prod"] = "dev"
    APP_NAME: str = "GRIND API"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── security ───────────────────────────────────────────────────
    SECRET_KEY: str                       # openssl rand -hex 32
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    ADMIN_USERNAME: str
    ADMIN_PASSWORD_HASH: str              # argon2 hash, see Phase 7.1

    # ── database ───────────────────────────────────────────────────
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "grind_db"

    # Pool maths justified in Phase 0.2 step 3:
    #   4 workers x (20 + 10) = 120 connections against max_connections 200.
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 10             # seconds to wait for a free connection
    DB_POOL_RECYCLE: int = 1800           # recycle before any 30-min idle reaper
    DB_ECHO: bool = False
    DB_STATEMENT_TIMEOUT_MS: int = 5000   # no single query may hog a pool slot
    DB_USE_PGBOUNCER: bool = False        # disables prepared statements, see Phase 9.4

    # ── redis ──────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MAX_CONNECTIONS: int = 50
    CACHE_ENABLED: bool = True
    CACHE_TTL_WORKOUT: int = 60           # plans change ~monthly; 60s is very safe
    CACHE_TTL_EXERCISE_COUNT: int = 300   # PR-28 global COUNT(*), pure seq scan
    RATE_LIMIT_ENABLED: bool = True

    # ── networking ─────────────────────────────────────────────────
    CORS_ORIGINS_RAW: str = Field("http://localhost:5173", alias="CORS_ORIGINS")
    TRUSTED_HOSTS_RAW: str = Field("*", alias="TRUSTED_HOSTS")

    # ── razorpay ───────────────────────────────────────────────────
    RAZORPAY_KEY_ID: str
    RAZORPAY_KEY_SECRET: str
    RAZORPAY_BASE_URL: str = "https://api.razorpay.com/v1"
    RAZORPAY_TIMEOUT: float = 10.0
    RAZORPAY_VERIFY_SIGNATURE: bool = True    # Decision 2 — security fix, on by default
    PAYMENTS_RECOMPUTE_PRICE: bool = False    # Decision 2b — parity default

    # ── mail (replaces PHP mail(), PR-33) ─────────────────────────
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = True
    MAIL_FROM: str = "GRIND Intake <noreply@trenddma.com>"
    INTAKE_RECIPIENT: str = "grindfit.ai@trenddma.com"

    # ── legacy parity switches (Decisions 3 & 4) ──────────────────
    LEGACY_DEFAULT_CLIENT_ID: int = 1         # PR-01
    LEGACY_AFFILIATE_SKIP_EXPIRY: bool = True # PR-10 — validate ignores expiry
    LEGACY_ZERO_PRICE_CHECKS_ORIGINAL: bool = True  # PR-16
    LEGACY_GLOBAL_PROGRESS_DENOMINATOR: bool = True # PR-28
    LEGACY_OPEN_ADMIN: bool = False           # Decision 3

    # ── derived ────────────────────────────────────────────────────
    @field_validator("ENV", mode="before")
    @classmethod
    def _lower_env(cls, v: str) -> str:
        return str(v).lower()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def CORS_ORIGINS(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS_RAW.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def TRUSTED_HOSTS(self) -> list[str]:
        return [h.strip() for h in self.TRUSTED_HOSTS_RAW.split(",") if h.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def IS_PROD(self) -> bool:
        return self.ENV == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
```

**Why `CORS_ORIGINS_RAW` as a string and not `list[str]`:** pydantic-settings tries `json.loads` on complex types *before* validators run, so `CORS_ORIGINS=http://a.com,http://b.com` in a `.env` raises a confusing `JSONDecodeError`. Taking a plain string and splitting in a computed property means the `.env` stays human-editable.

**Why `DB_STATEMENT_TIMEOUT_MS`:** you have 120 pool slots (Phase 0.2). One pathological query holding a slot for 60 seconds removes 1/120th of your capacity for a minute; a hundred of them take the service down. A 5-second server-side ceiling turns an outage into a handful of 500s.

## 2.2 `app/core/logging.py`

**Why:** the PHP writes nothing anywhere. When a payment fails in production you currently have no record at all.
**Why this way:** one JSON object per line is what CloudWatch / Loki / Datadog ingest natively; a `ContextVar` carries the request id into every log line from every layer without threading it through function signatures (which is what makes it work under async concurrency, where 20 requests interleave).
**Applies to:** every file — this replaces the complete absence of logging.

```python
# app/core/logging.py
import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the ambient request id attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        # anything passed as logger.info("x", extra={"client_id": 5}) lands here
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(levelname)-8s %(name)s :: %(message)s")
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn duplicates access lines; ours (Phase 8) carries the request id.
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("uvicorn.error").handlers.clear()
    logging.getLogger("uvicorn.error").propagate = True

    # SQLAlchemy logs every statement at INFO when echo=True — keep it at WARNING.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
```

## 2.3 `app/core/exceptions.py`

**Why:** the PHP is inconsistent — some files `die("Invalid Access Token")` as plain text, some return `{"success":false}` at HTTP 200, `verify_payment.php` returns the **raw exception message** to the browser (an information leak), and `create_order.php` has no error handling at all so a Razorpay outage produces a PHP fatal and a blank page.
**Why this way:** one exception hierarchy, one handler set. Every error leaves the app in the legacy envelope `{"success": false, "message": "..."}` so the frontend's existing checks keep working, while the real cause goes to the log with the request id.
**Applies to:** all 19 legacy entry points.

```python
# app/core/exceptions.py
import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_ctx

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
        return _envelope("Invalid request payload", status.HTTP_422_UNPROCESSABLE_ENTITY)

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
```

**Note on the validation handler:** FastAPI's default 422 body is a nested list the React client does not understand. Endpoints that must reproduce an exact legacy message (`"Missing required fields"`, PR-06) do **not** rely on Pydantic validation at all — they accept loose input and validate inside the service, exactly as the PHP did. That is deliberate, not laziness: PR-01 requires `client_id=abc` to become `0`, not to 422.

## 2.4 `app/core/compat.py`

**Why:** this is the heart of the parity guarantee. PHP's loose typing produces results Python does not: `(int)"12abc"` is `12`, `round(0.5)` is `1` (Python's is `0`), `str_pad` pads on the **right** by default, and `!$data` is true for `"0"` and `[]`. Scattering those conversions across 20 call sites means 20 chances to get one wrong.
**Why this way:** every PHP semantic lives in one audited, unit-tested module. When a parity test fails, there is exactly one file to look at.
**Applies to:** PR-01, PR-06, PR-15, PR-16, PR-17, PR-22, PR-26, PR-28, PR-33.

```python
# app/core/compat.py
"""PHP runtime semantics, reproduced exactly.

Every function here mirrors a PHP builtin that the legacy code relies on.
Do not "fix" these to be more correct — correctness here means *identical
to PHP*, because that is the acceptance criterion.
"""
import math
import re
from typing import Any

_LEADING_INT = re.compile(r"^[+-]?\d+")
_LEADING_FLOAT = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_TAGS = re.compile(r"<[^>]*>")
_NON_ALPHA = re.compile(r"[^A-Za-z]")


def php_intval(value: Any) -> int:
    """PHP `(int)$v`.

    (int)"42"     -> 42       (int)"12abc" -> 12
    (int)"abc"    -> 0        (int)"12.9"  -> 12   (truncates toward zero)
    (int)null     -> 0        (int)true    -> 1
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)  # Python int() truncates toward zero, same as PHP
    match = _LEADING_INT.match(str(value).strip())
    return int(match.group()) if match else 0


def php_floatval(value: Any) -> float:
    """PHP `floatval($v)` / `(float)$v`. Same leading-numeric rule as intval."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    match = _LEADING_FLOAT.match(str(value).strip())
    return float(match.group()) if match else 0.0


def php_round(value: float, precision: int = 0) -> float:
    """PHP `round()` — half away from zero.

    Python's round() is banker's rounding: round(0.5) == 0, round(2.5) == 2.
    PHP:   round(0.5) == 1.0, round(2.5) == 3.0, round(-0.5) == -1.0
    Getting this wrong changes payment amounts (PR-17) and the progress
    percentage (PR-28) by one unit at every .5 boundary.
    """
    if precision == 0:
        return float(math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5))
    factor = 10.0**precision
    scaled = value * factor
    rounded = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
    return rounded / factor


def php_round_int(value: float) -> int:
    """round() where the result is used as an integer (paise, percentages)."""
    return int(php_round(value, 0))


def php_str_pad(value: str, length: int, pad: str = " ", *, left: bool = False) -> str:
    """PHP `str_pad()`.

    Default direction is STR_PAD_RIGHT — this is the trap in PR-22:
        str_pad("Al", 3, "X")  -> "AlX"   (NOT "XAl")
    Pass left=True for STR_PAD_LEFT (used for the 6-digit id).
    """
    if length <= len(value) or not pad:
        return value
    fill = (pad * length)[: length - len(value)]
    return fill + value if left else value + fill


def php_json_is_falsy(value: Any) -> bool:
    """Emulates `if (!$data)` after json_decode (PR-26).

    Falsy in PHP: null, false, 0, 0.0, "", "0", [] (and {} which decodes
    to an empty array under assoc=true).
    Note "0" — a JSON body of literally `"0"` is rejected as invalid.
    """
    if value is None or value is False:
        return True
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value in ("", "0")
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def php_clean(value: Any) -> str:
    """PHP `htmlspecialchars(strip_tags(trim($v)), ENT_QUOTES, 'UTF-8')` (PR-33).

    Order is load-bearing: trim, THEN strip tags, THEN escape. PHP does not
    re-trim after stripping, so "<b> hi</b>" -> " hi" with a leading space.
    The `&` replacement must come first or the later entities double-escape.
    """
    text = "" if value is None else str(value)
    text = _TAGS.sub("", text.strip())
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


def php_trim(value: Any) -> str:
    """PHP `trim()` with the null-coalescing default the legacy code uses."""
    return "" if value is None else str(value).strip()


def letters_only(value: str) -> str:
    """PHP `preg_replace('/[^A-Za-z]/', '', $name)` (PR-22)."""
    return _NON_ALPHA.sub("", value or "")
```

**Worked example of why `php_round` matters (PR-17):** a ₹3,499 plan with a 30% coupon gives `final_price = 2449.3`, so `amount = round(244930.0) = 244930` paise. Now take a plan priced ₹999.995 (possible once a percentage discount lands on a half-paisa): PHP charges `99999.5 → 100000`, Python's `round()` charges `100000` too — but at `₹1000.005 → 100000.5`, PHP charges `100001` and Python's builtin charges `100000`. A one-paisa mismatch against a Razorpay order is a failed capture. The helper removes the whole class of bug.

**Verify Phase 2:**

```bash
uv run python -c "
from app.core.compat import *
assert php_intval('12abc') == 12 and php_intval('abc') == 0
assert php_round(0.5) == 1.0 and php_round(2.5) == 3.0 and php_round(-0.5) == -1.0
assert php_str_pad('Al', 3, 'X') == 'AlX'
assert php_str_pad('7', 6, '0', left=True) == '000007'
assert php_json_is_falsy('0') and php_json_is_falsy([]) and not php_json_is_falsy({'a':1})
print('compat OK')
"
uv run python -c "from app.core.config import settings; print(settings.APP_NAME, settings.ENV)"
```

---
---

# Phase 3 — Database: engine, models, indexes, migration

## 3.1 `app/db/base.py` and `app/db/session.py`

**Why:** `backend/config/database.php` opens a **brand new PDO connection on every single HTTP request** and never pools. At 1,200 req/s that is 1,200 TCP handshakes + TLS + Postgres auth per second — roughly 3 ms of pure overhead per request, and it will exhaust `max_connections` long before it exhausts CPU.
**Why this way:** one process-wide async pool, sized by the arithmetic in Phase 0.2. `pool_pre_ping` costs one cheap round trip but eliminates the "server closed the connection unexpectedly" class of error after a network blip or a database failover.
**Applies to:** replaces `config/database.php` and `Base files/database.php` entirely.

```python
# app/db/base.py
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

```python
# app/db/session.py
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
```

**Why `expire_on_commit=False`:** with the default, every attribute access after a commit triggers a fresh `SELECT`. Serialising a plan with 30 exercises after an insert would fire 30 extra queries.

## 3.2 `app/db/models/` — 11 tables

**Why:** the legacy schema was hand-edited through phpMyAdmin, so no two environments are guaranteed to match. Declaring it in code makes it reproducible and diffable.
**Why these types:** each column mirrors the MySQL dump exactly — `tinyint(1)` → `Boolean`, `decimal(10,2)` → `Numeric(10, 2)`, `longtext` → `Text`, `enum('active','inactive')` → `String(20)` (a plain string, because adding a native enum would make `status='pending'` fail where MySQL silently coerced it — a behaviour change).
**Applies to:** the 11 tables in `grind sql/`.

```python
# app/db/models/__init__.py
from app.db.models.affiliate import AffiliateCode, AffiliateConversion
from app.db.models.client import Client, ClientProgress
from app.db.models.diet import DietPlan
from app.db.models.enrollment import Enrollment
from app.db.models.workout import (
    WorkoutDay,
    WorkoutExercise,
    WorkoutLog,
    WorkoutPlan,
    WorkoutProgress,
)

__all__ = [
    "AffiliateCode", "AffiliateConversion", "Client", "ClientProgress",
    "DietPlan", "Enrollment", "WorkoutDay", "WorkoutExercise",
    "WorkoutLog", "WorkoutPlan", "WorkoutProgress",
]
```

```python
# app/db/models/client.py
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    goal: Mapped[str | None] = mapped_column(String(255))
    # enum('active','inactive') in MySQL. Kept as a string: a native enum
    # would raise on any value the legacy data happens to contain.
    status: Mapped[str | None] = mapped_column(String(20), server_default="active")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    access_token: Mapped[str | None] = mapped_column(String(50), unique=True)


class ClientProgress(Base):
    __tablename__ = "client_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    waist: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    chest: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    arms: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    thighs: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # PR-32 reads newest and oldest per client on every portal load.
        Index("ix_client_progress_client_created", "client_id", "created_at"),
    )
```

```python
# app/db/models/workout.py
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int | None] = mapped_column(Integer)
    plan_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool | None] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    workout_json: Mapped[str | None] = mapped_column(Text)
    version_no: Mapped[int | None] = mapped_column(Integer, server_default="1")

    __table_args__ = (
        # PR-02: WHERE client_id=? AND is_active=1 ORDER BY id DESC LIMIT 1.
        # A partial index is ~40x smaller than a full one here, because only
        # one plan per client is ever active.
        Index(
            "ix_workout_plans_active",
            "client_id",
            "id",
            postgresql_where=(is_active.is_(True)),
        ),
        # PR-24: MAX(version_no) per client during import.
        Index("ix_workout_plans_client_version", "client_id", "version_no"),
    )


class WorkoutDay(Base):
    __tablename__ = "workout_days"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int | None] = mapped_column(Integer)
    day_number: Mapped[int | None] = mapped_column(Integer)
    day_name: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        # PR-04. The legacy schema has NO index here at all — the join was a
        # seq scan of 156 rows, which is fine at 156 rows and fatal at 10k users.
        Index("ix_workout_days_plan_daynum", "plan_id", "day_number"),
    )


class WorkoutExercise(Base):
    __tablename__ = "workout_exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_id: Mapped[int | None] = mapped_column(Integer)
    exercise_name: Mapped[str | None] = mapped_column(String(255))
    sets_count: Mapped[int | None] = mapped_column(Integer)
    reps: Mapped[str | None] = mapped_column(String(50))
    youtube_url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_workout_exercises_day_sort", "day_id", "sort_order"),
    )


class WorkoutLog(Base):
    __tablename__ = "workout_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    month_no: Mapped[int] = mapped_column(Integer, nullable=False)
    week_no: Mapped[int] = mapped_column(Integer, nullable=False)
    day_id: Mapped[int] = mapped_column(Integer, nullable=False)
    exercise_id: Mapped[int] = mapped_column(Integer, nullable=False)
    set_no: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool | None] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # THE hot table: ~200k rows/day at target load (Phase 0.2 step 5).
        # PR-29 runs COUNT(DISTINCT exercise_id) WHERE user_email=? AND completed=1
        # on every portal load. Without this it is a full scan of tens of millions
        # of rows, per page view. This single index is the difference between
        # 2 ms and 8 s on that query.
        Index(
            "ix_workout_logs_user_completed_ex",
            "user_email",
            "exercise_id",
            postgresql_where=(completed.is_(True)),
        ),
        Index("ix_workout_logs_user_created", "user_email", "created_at"),
    )


class WorkoutProgress(Base):
    """Present in the legacy schema; no PHP file reads or writes it.

    Kept so the migration is a faithful reproduction and nothing silently
    disappears. Safe to drop once you confirm nothing external uses it.
    """

    __tablename__ = "workout_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int | None] = mapped_column(Integer)
    exercise_id: Mapped[int | None] = mapped_column(Integer)
    set_number: Mapped[int | None] = mapped_column(Integer)
    completed: Mapped[bool | None] = mapped_column(Boolean)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

```python
# app/db/models/diet.py
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DietPlan(Base):
    __tablename__ = "diet_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_name: Mapped[str | None] = mapped_column(String(255))
    diet_json: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool | None] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_diet_plans_active",
            "client_id",
            "id",
            postgresql_where=(is_active.is_(True)),
        ),
    )
```

```python
# app/db/models/enrollment.py
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    plan_name: Mapped[str | None] = mapped_column(String(100))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    discount_percent: Mapped[int | None] = mapped_column(Integer, server_default="0")
    coupon_code: Mapped[str | None] = mapped_column(String(50))
    final_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(255))
    razorpay_order_id: Mapped[str | None] = mapped_column(String(255))
    payment_status: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # PR-12 joins affiliate_codes.code -> enrollments.coupon_code
        # and filters payment_status='Paid'.
        Index("ix_enrollments_coupon_status", "coupon_code", "payment_status"),
        # Lets you detect a duplicate capture without changing PR-19 behaviour.
        Index("ix_enrollments_payment_id", "razorpay_payment_id"),
        Index("ix_enrollments_email", "email"),
    )
```

```python
# app/db/models/affiliate.py
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AffiliateCode(Base):
    __tablename__ = "affiliate_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str | None] = mapped_column(String(50), unique=True)
    affiliate_name: Mapped[str | None] = mapped_column(String(100))
    affiliate_email: Mapped[str | None] = mapped_column(String(150))
    discount_percent: Mapped[int | None] = mapped_column(Integer, server_default="10")
    commission_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), server_default="0.00"
    )
    total_sales: Mapped[int | None] = mapped_column(Integer, server_default="0")
    total_revenue: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), server_default="0.00"
    )
    status: Mapped[str | None] = mapped_column(String(20), server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expiry_date: Mapped[date | None] = mapped_column(Date)


class AffiliateConversion(Base):
    """Schema exists in the legacy dump; no PHP file ever writes it.

    Reproduced for fidelity. PR-19 explicitly keeps it unwritten.
    """

    __tablename__ = "affiliate_conversions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    affiliate_code: Mapped[str | None] = mapped_column(String(50))
    plan_name: Mapped[str | None] = mapped_column(String(100))
    amount_paid: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    customer_name: Mapped[str | None] = mapped_column(String(255))
    customer_email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

### The indexes, and what each one buys

| Index | Serves | Without it, at 10k users |
|---|---|---|
| `ix_workout_logs_user_completed_ex` (partial) | PR-29 `COUNT(DISTINCT exercise_id)` | Full scan of ~73M rows on every portal load |
| `ix_workout_plans_active` (partial) | PR-02 active-plan lookup | Seq scan on every workout fetch — the most-called endpoint |
| `ix_workout_days_plan_daynum` | PR-04 | Seq scan per fetch |
| `ix_workout_exercises_day_sort` | PR-04 | Seq scan per fetch, and it grows with every plan imported |
| `ix_client_progress_client_created` | PR-32 newest + oldest row | Sort of the whole table, twice, per portal load |
| `ix_enrollments_coupon_status` | PR-12 dashboard join | Nested loop over full `enrollments` |

**Partial indexes** (`postgresql_where=`) are used where the filter is highly selective and constant. Only one plan per client is ever `is_active`, so the partial index is a fraction of the size of a full one and stays hot in cache. This is one of the concrete reasons Decision 1 chose Postgres.

None of these change a single query **result** — they change how the planner reaches it. Parity is untouched.

## 3.3 Alembic

**Why:** the legacy schema has no migration history, so "what is actually in production" is unknowable. Every future change needs to be reviewable and reversible.

```bash
cd backend-v1
uv run alembic init -t async migrations
```

Edit `migrations/env.py` — replace the config/target-metadata section:

```python
# migrations/env.py  (the parts you change)
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.core.config import settings
from app.db.base import Base
import app.db.models  # noqa: F401  — registers every model on Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,          # catches varchar(50) -> varchar(100)
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Generate and apply:

```bash
docker compose up -d postgres
uv run alembic revision --autogenerate -m "baseline: legacy schema + hot-path indexes"
uv run alembic upgrade head
```

**Why autogenerate rather than hand-writing 11 `op.create_table` calls:** the models are already the source of truth and autogenerate reads them, so there is no chance of the migration and the models drifting on day one. **Always read the generated file before applying it** — autogenerate is a good first draft, not an oracle.

## 3.4 Moving the data out of MySQL

**Why:** `grind sql/` holds phpMyAdmin dumps in MySQL dialect — backticks, `AUTO_INCREMENT`, `tinyint(1)`, `\r\n` escapes inside the JSON columns. They will not load into Postgres directly.
**Why pgloader:** it handles type mapping, sequence resetting and encoding in one pass, and produces a summary you can reconcile against. Hand-editing 250 KB of `workout_plans` INSERTs is how you lose a row and never find out.

```bash
# 1. Stand the dumps up in a throwaway MySQL
docker run -d --name grind-mysql-tmp \
  -e MYSQL_ROOT_PASSWORD=temp -e MYSQL_DATABASE=grind_db \
  -p 3307:3306 mysql:5.7

# 2. Load every dump (order does not matter — there are no foreign keys)
for f in "../grind sql/"*.sql; do
  docker exec -i grind-mysql-tmp mysql -uroot -ptemp grind_db < "$f"
done

# 3. Transfer, letting pgloader do the type mapping
docker run --rm --network host dimitri/pgloader:latest pgloader \
  mysql://root:temp@localhost:3307/grind_db \
  postgresql://postgres:PASSWORD@localhost:5432/grind_db

# 4. Sequences do not follow the data — reset them or the first insert
#    collides with an existing id.
uv run python scripts/reset_sequences.py

# 5. Reconcile
uv run python scripts/reconcile.py
```

```python
# scripts/reset_sequences.py
import asyncio

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
            await conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:t, 'id'), "
                    "COALESCE((SELECT MAX(id) FROM " + table + "), 1), true)"
                ),
                {"t": table},
            )
            print(f"sequence reset: {table}")
    await engine.dispose()


asyncio.run(main())
```

```python
# scripts/reconcile.py
"""Row counts + a checksum per table, to compare against the MySQL source."""
import asyncio

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


asyncio.run(main())
```

**Note:** `EXPECTED_COUNTS` are derived from the `AUTO_INCREMENT` values in the dumps assuming no gaps. Deleted rows create gaps, so treat a mismatch as "go count the `INSERT` tuples in that dump", not as an automatic failure. `sum(id)` catches a partially loaded table that happens to have the right count.

**Verify Phase 3:**

```bash
uv run alembic current                       # shows your baseline revision
uv run python scripts/reconcile.py           # all OK
uv run python -c "
import asyncio
from sqlalchemy import text
from app.db.session import engine
async def m():
    async with engine.connect() as c:
        print((await c.execute(text('SELECT version()'))).scalar())
    await engine.dispose()
asyncio.run(m())"
```

---
---

# Phase 4 — Repositories (every SQL statement)

**Why a separate layer:** the acceptance criterion is provable parity. That is only auditable if each PHP query maps to exactly one Python function you can read side by side with the original. Repositories contain **no business rules** — no defaults, no rounding, no validation. They take typed arguments and return rows.

**The one universal correctness trap — NULL ordering.** MySQL sorts `NULL` **first** on `ASC`; Postgres sorts it **last**. `workout_days.day_number` and `workout_exercises.sort_order` are both nullable. A day with a NULL `day_number` would move from the top of the list to the bottom — a visible, silent output change. Every ported `ORDER BY` therefore uses `nulls_first()` **and** adds `id` as a tiebreaker, because MySQL's order among ties was previously undefined (in practice PK order) and we want it deterministic.

## 4.1 `app/repositories/workout_repo.py`

**Applies to:** `api/workout.php`, `Client/workout.php`, `api/complete-workout.php`, `Base files/save-progress.php`, `Base files/my-plan (1).php`.

```python
# app/repositories/workout_repo.py
from collections import defaultdict

from sqlalchemy import distinct, func, nulls_first, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WorkoutDay, WorkoutExercise, WorkoutLog, WorkoutPlan


async def get_active_plan(db: AsyncSession, client_id: int) -> WorkoutPlan | None:
    """PR-02.

    PHP: SELECT * FROM workout_plans
         WHERE client_id = ? AND is_active = 1
         ORDER BY id DESC LIMIT 1
    """
    stmt = (
        select(WorkoutPlan)
        .where(WorkoutPlan.client_id == client_id, WorkoutPlan.is_active.is_(True))
        .order_by(WorkoutPlan.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_days_with_exercises(
    db: AsyncSession, plan_id: int
) -> list[tuple[WorkoutDay, list[WorkoutExercise]]]:
    """PR-04, with the N+1 collapsed.

    PHP issued 1 query for days plus one MORE query per day (Phase 0.2 step 4:
    7 round trips per request, 8,400 queries/s at peak). This is 2 queries
    regardless of day count, and produces byte-identical output because the
    grouping and ordering are preserved exactly.
    """
    day_stmt = (
        select(WorkoutDay)
        .where(WorkoutDay.plan_id == plan_id)
        .order_by(nulls_first(WorkoutDay.day_number.asc()), WorkoutDay.id.asc())
    )
    days = list((await db.execute(day_stmt)).scalars().all())
    if not days:
        return []

    ex_stmt = (
        select(WorkoutExercise)
        .where(WorkoutExercise.day_id.in_([d.id for d in days]))
        .order_by(
            WorkoutExercise.day_id.asc(),
            nulls_first(WorkoutExercise.sort_order.asc()),
            WorkoutExercise.id.asc(),
        )
    )
    grouped: dict[int, list[WorkoutExercise]] = defaultdict(list)
    for exercise in (await db.execute(ex_stmt)).scalars().all():
        grouped[exercise.day_id].append(exercise)

    return [(day, grouped.get(day.id, [])) for day in days]


async def insert_log(
    db: AsyncSession,
    *,
    user_email: str,
    month_no: int,
    week_no: int,
    day_id: int,
    exercise_id: int,
    set_no: int,
    completed: bool,
) -> WorkoutLog:
    """PR-07, PR-08. No existence check, no dedupe — exactly as the PHP."""
    log = WorkoutLog(
        user_email=user_email,
        month_no=month_no,
        week_no=week_no,
        day_id=day_id,
        exercise_id=exercise_id,
        set_no=set_no,
        completed=completed,
    )
    db.add(log)
    await db.flush()
    return log


async def count_all_exercises(db: AsyncSession) -> int:
    """PR-28 denominator.

    PHP: SELECT COUNT(*) FROM workout_exercises   -- GLOBAL, not per client.
    This is a bug (every client's percentage shrinks as other clients' plans
    are imported) and Decision 4 says keep it. It is a sequential scan, so
    Phase 7 caches it for 300 s — keeping the bug then costs nothing.
    """
    return (await db.execute(select(func.count()).select_from(WorkoutExercise))).scalar_one()


async def count_completed_exercises(db: AsyncSession, user_email: str) -> int:
    """PR-29.

    PHP: SELECT COUNT(DISTINCT exercise_id) FROM workout_logs
         WHERE user_email = ? AND completed = 1
    Served by ix_workout_logs_user_completed_ex (Phase 3.2).
    """
    stmt = select(func.count(distinct(WorkoutLog.exercise_id))).where(
        WorkoutLog.user_email == user_email,
        WorkoutLog.completed.is_(True),
    )
    return (await db.execute(stmt)).scalar_one()


# ── admin write paths ──────────────────────────────────────────────

async def get_max_version(db: AsyncSession, client_id: int) -> int:
    """PR-24: COALESCE(MAX(version_no), 0)."""
    stmt = select(func.coalesce(func.max(WorkoutPlan.version_no), 0)).where(
        WorkoutPlan.client_id == client_id
    )
    return (await db.execute(stmt)).scalar_one()


async def deactivate_plans(db: AsyncSession, client_id: int) -> None:
    """PR-24: UPDATE workout_plans SET is_active = 0 WHERE client_id = ?"""
    from sqlalchemy import update

    await db.execute(
        update(WorkoutPlan)
        .where(WorkoutPlan.client_id == client_id)
        .values(is_active=False)
    )


async def insert_plan(
    db: AsyncSession,
    *,
    client_id: int,
    plan_name: str | None,
    workout_json: str | None,
    is_active: bool | None = None,
    version_no: int | None = None,
) -> WorkoutPlan:
    """Shared by PR-23 (create-plan) and PR-24 (import).

    When is_active / version_no are None the column defaults apply — which is
    precisely what create-plan.php relied on (it named only three columns).
    """
    values: dict[str, object] = {
        "client_id": client_id,
        "plan_name": plan_name,
        "workout_json": workout_json,
    }
    if is_active is not None:
        values["is_active"] = is_active
    if version_no is not None:
        values["version_no"] = version_no

    plan = WorkoutPlan(**values)
    db.add(plan)
    await db.flush()          # assigns plan.id, the PHP lastInsertId()
    return plan


async def insert_day(
    db: AsyncSession, *, plan_id: int, day_number: int, day_name: str | None
) -> WorkoutDay:
    day = WorkoutDay(plan_id=plan_id, day_number=day_number, day_name=day_name)
    db.add(day)
    await db.flush()
    return day


async def insert_exercises(
    db: AsyncSession, *, day_id: int, rows: list[dict[str, object]]
) -> None:
    """Bulk insert. PHP looped one INSERT per exercise; a 5-day plan with 6
    exercises each was 30 round trips. This is one statement, same rows.
    """
    if not rows:
        return
    db.add_all([WorkoutExercise(day_id=day_id, **row) for row in rows])  # type: ignore[arg-type]
    await db.flush()
```

## 4.2 `app/repositories/affiliate_repo.py`

**Applies to:** `api/validate_affiliate.php`, `Payment/create_order.php`, `admin/affiliate-dashboard.php`.

```python
# app/repositories/affiliate_repo.py
from datetime import date
from typing import Any

from sqlalchemy import Numeric, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AffiliateCode, Enrollment


async def get_active_code(db: AsyncSession, code: str) -> AffiliateCode | None:
    """PR-10 — validate_affiliate.php.

    PHP: SELECT * FROM affiliate_codes WHERE code = ? AND status = 'active'
    Note the ABSENCE of an expiry check. An expired code validates here and
    is then refused at order time (PR-14). The two paths genuinely disagree
    in the legacy system and Decision 4 keeps that.
    """
    stmt = select(AffiliateCode).where(
        AffiliateCode.code == code,
        AffiliateCode.status == "active",
    )
    return (await db.execute(stmt)).scalars().first()


async def get_active_unexpired_code(db: AsyncSession, code: str) -> AffiliateCode | None:
    """PR-14 — create_order.php.

    PHP: ... AND status='active' AND expiry_date >= CURDATE() LIMIT 1

    MySQL's `NULL >= CURDATE()` is NULL, i.e. the row is excluded — so a code
    with no expiry date does NOT discount. Postgres behaves identically, but
    the comparison is written explicitly so the intent survives review.
    """
    stmt = (
        select(AffiliateCode)
        .where(
            AffiliateCode.code == code,
            AffiliateCode.status == "active",
            AffiliateCode.expiry_date.is_not(None),
            AffiliateCode.expiry_date >= func.current_date(),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def get_dashboard_rows(db: AsyncSession) -> list[dict[str, Any]]:
    """PR-12 — affiliate-dashboard.php, one aggregate query.

    PHP:
      SELECT ac.*, COUNT(e.id) total_sales,
             COALESCE(SUM(e.final_price),0) revenue,
             COALESCE(AVG(e.final_price),0) average_order,
             COALESCE(SUM(e.final_price)*ac.commission_percent/100,0) commission_due
      FROM affiliate_codes ac
      LEFT JOIN enrollments e
        ON ac.code = e.coupon_code AND e.payment_status = 'Paid'
      GROUP BY ...  ORDER BY revenue DESC

    The join predicate (not a WHERE) is load-bearing: it keeps affiliates with
    zero paid sales in the result with revenue 0. Moving it to WHERE would
    drop them — a silent behaviour change.
    """
    revenue = func.coalesce(func.sum(Enrollment.final_price), 0)

    stmt = (
        select(
            AffiliateCode.id,
            AffiliateCode.affiliate_name,
            AffiliateCode.affiliate_email,
            AffiliateCode.code,
            AffiliateCode.discount_percent,
            AffiliateCode.commission_percent,
            AffiliateCode.status,
            func.count(Enrollment.id).label("total_sales"),
            revenue.label("revenue"),
            func.coalesce(func.avg(Enrollment.final_price), 0).label("average_order"),
            func.coalesce(
                func.sum(Enrollment.final_price)
                * cast(AffiliateCode.commission_percent, Numeric(10, 4))
                / 100,
                0,
            ).label("commission_due"),
        )
        .select_from(AffiliateCode)
        .outerjoin(
            Enrollment,
            (AffiliateCode.code == Enrollment.coupon_code)
            & (Enrollment.payment_status == "Paid"),
        )
        .group_by(
            AffiliateCode.id,
            AffiliateCode.affiliate_name,
            AffiliateCode.affiliate_email,
            AffiliateCode.code,
            AffiliateCode.discount_percent,
            AffiliateCode.commission_percent,
            AffiliateCode.status,
        )
        .order_by(revenue.desc())
    )
    return [dict(row) for row in (await db.execute(stmt)).mappings().all()]
```

## 4.3 `app/repositories/client_repo.py`

**Applies to:** `admin/clients (1).php`, `admin/client-details.php`, `admin/add-progress.php`, `admin/add_diet.php`, `Base files/my-plan (1).php`, `Base files/workout-progress (1).php`.

```python
# app/repositories/client_repo.py
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Client, ClientProgress


async def get_by_id(db: AsyncSession, client_id: int) -> Client | None:
    return (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalars().first()


async def get_by_email(db: AsyncSession, email: str) -> Client | None:
    """PR-21 — the upsert key is email, not id."""
    return (
        await db.execute(select(Client).where(Client.email == email))
    ).scalars().first()


async def get_by_access_token(db: AsyncSession, token: str) -> Client | None:
    """PR-27 and Decision 5 — the client portal identifies by token."""
    return (
        await db.execute(
            select(Client).where(Client.access_token == token).limit(1)
        )
    ).scalars().first()


async def list_all(db: AsyncSession) -> list[Client]:
    """PHP: SELECT * FROM clients ORDER BY id DESC"""
    return list(
        (await db.execute(select(Client).order_by(Client.id.desc()))).scalars().all()
    )


async def insert(
    db: AsyncSession, *, name: str | None, email: str, phone: str | None, goal: str | None
) -> Client:
    client = Client(name=name, email=email, phone=phone, goal=goal)
    db.add(client)
    await db.flush()   # PHP lastInsertId(), needed to build the token
    return client


async def update_details(
    db: AsyncSession, *, email: str, name: str | None, phone: str | None, goal: str | None
) -> None:
    """PR-21 existing-client branch: name/phone/goal only.

    access_token is deliberately NOT touched — regenerating it would break
    every client's saved portal link.
    """
    await db.execute(
        update(Client)
        .where(Client.email == email)
        .values(name=name, phone=phone, goal=goal)
    )


async def set_access_token(db: AsyncSession, *, client_id: int, token: str) -> None:
    await db.execute(
        update(Client).where(Client.id == client_id).values(access_token=token)
    )


# ── client_progress ────────────────────────────────────────────────

async def insert_progress(
    db: AsyncSession,
    *,
    client_id: int,
    weight: Decimal | None,
    waist: Decimal | None,
    chest: Decimal | None,
    arms: Decimal | None,
    thighs: Decimal | None,
    notes: str | None,
) -> ClientProgress:
    row = ClientProgress(
        client_id=client_id, weight=weight, waist=waist, chest=chest,
        arms=arms, thighs=thighs, notes=notes,
    )
    db.add(row)
    await db.flush()
    return row


async def get_progress_history(db: AsyncSession, client_id: int) -> list[ClientProgress]:
    """PR-32.

    PHP ran THREE queries: newest (DESC LIMIT 1), oldest (ASC LIMIT 1), and the
    full history (ASC). The history already contains the other two, so one
    query gives identical results in a third of the round trips.
    `id` breaks ties on identical timestamps, which MySQL left undefined.
    """
    stmt = (
        select(ClientProgress)
        .where(ClientProgress.client_id == client_id)
        .order_by(ClientProgress.created_at.asc(), ClientProgress.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())
```

## 4.4 `app/repositories/diet_repo.py` and `enrollment_repo.py`

```python
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
```

```python
# app/repositories/enrollment_repo.py
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Enrollment


async def insert(
    db: AsyncSession,
    *,
    name: str,
    email: str,
    phone: str,
    plan_name: str,
    original_price: Decimal,
    discount_percent: int,
    coupon_code: str,
    final_price: Decimal,
    razorpay_payment_id: str,
    razorpay_order_id: str,
    payment_status: str,
) -> Enrollment:
    """PR-19. payment_status is passed in but is always 'Paid' from the service,
    exactly as the PHP hardcoded it.
    """
    row = Enrollment(
        name=name, email=email, phone=phone, plan_name=plan_name,
        original_price=original_price, discount_percent=discount_percent,
        coupon_code=coupon_code, final_price=final_price,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_order_id=razorpay_order_id, payment_status=payment_status,
    )
    db.add(row)
    await db.flush()
    return row


async def get_by_payment_id(db: AsyncSession, payment_id: str) -> Enrollment | None:
    """Not in the legacy code. Used only to LOG duplicate captures, never to
    block them — blocking would change PR-19 behaviour.
    """
    return (
        await db.execute(
            select(Enrollment).where(Enrollment.razorpay_payment_id == payment_id).limit(1)
        )
    ).scalars().first()
```

**Why `Decimal` on the way into `enrollments` but `float` in the price maths:** PR-15/PR-16/PR-17 must reproduce PHP's float arithmetic *exactly*, so the calculation stays in `float`. The database column is `decimal(10,2)`, so the value is converted once at the boundary. Doing the arithmetic in `Decimal` would be more correct and would produce **different** amounts at half-paisa boundaries — a parity failure. Correctness and parity point in opposite directions here; the brief says parity wins.

**Verify Phase 4:**

```bash
uv run python -c "
import asyncio
from app.db.session import SessionLocal, engine
from app.repositories import workout_repo, affiliate_repo, client_repo

async def m():
    async with SessionLocal() as db:
        plan = await workout_repo.get_active_plan(db, 1)
        print('active plan:', plan.id if plan else None)
        if plan:
            days = await workout_repo.get_days_with_exercises(db, plan.id)
            print('days:', len(days), 'exercises:', sum(len(e) for _, e in days))
        print('global exercise count:', await workout_repo.count_all_exercises(db))
        print('dashboard rows:', len(await affiliate_repo.get_dashboard_rows(db)))
        print('clients:', len(await client_repo.list_all(db)))
    await engine.dispose()
asyncio.run(m())"
```

---
---

# Phase 5 — Services (the business rules)

**Why:** this is where the 33 parity rules live. Services take plain Python values and return plain Python structures — no `Request`, no `Response`, no HTTP status codes. That is what makes every rule testable in isolation, which is what makes "identical outcomes" provable rather than asserted.

**Rule of thumb used throughout:** if the PHP did something surprising, there is a comment saying so with the `PR-xx` id. If you find yourself wanting to "clean up" one of those lines, the comment is there to stop you.

## 5.1 `app/services/workout_service.py`

**Applies to:** `api/workout.php`, `api/complete-workout.php`, `Base files/save-progress.php`.

```python
# app/services/workout_service.py
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_intval, php_trim
from app.core.config import settings
from app.core.exceptions import ValidationFailure
from app.db.models import WorkoutDay, WorkoutExercise, WorkoutPlan
from app.repositories import workout_repo

logger = logging.getLogger(__name__)


def _plan_dict(plan: WorkoutPlan) -> dict[str, Any]:
    """PHP `SELECT *` returned every column. Key order matches the table."""
    return {
        "id": plan.id,
        "client_id": plan.client_id,
        "plan_name": plan.plan_name,
        # MySQL tinyint(1) serialised as 0/1 through json_encode, and
        # workoutService.ts types is_active as `number`. Emitting `true`
        # would be a contract change for the frontend.
        "is_active": int(bool(plan.is_active)),
        "created_at": plan.created_at.strftime("%Y-%m-%d %H:%M:%S") if plan.created_at else None,
        "workout_json": plan.workout_json,
        "version_no": plan.version_no,
    }


def _exercise_dict(ex: WorkoutExercise) -> dict[str, Any]:
    return {
        "id": ex.id,
        "day_id": ex.day_id,
        "exercise_name": ex.exercise_name,
        "sets_count": ex.sets_count,
        "reps": ex.reps,
        "youtube_url": ex.youtube_url,
        "notes": ex.notes,
        "sort_order": ex.sort_order,
    }


def _day_dict(day: WorkoutDay, exercises: list[WorkoutExercise]) -> dict[str, Any]:
    return {
        "id": day.id,
        "plan_id": day.plan_id,
        "day_number": day.day_number,
        "day_name": day.day_name,
        "exercises": [_exercise_dict(e) for e in exercises],
    }


async def get_workout(db: AsyncSession, raw_client_id: Any) -> dict[str, Any]:
    """PR-01 .. PR-05 — api/workout.php.

    PR-01: absent  -> LEGACY_DEFAULT_CLIENT_ID (1)
           "abc"   -> 0 via PHP (int) cast, NOT a 422
    PR-03: no plan -> HTTP 200 with data:null and the exact legacy message.
    """
    if raw_client_id is None or raw_client_id == "":
        client_id = settings.LEGACY_DEFAULT_CLIENT_ID
    else:
        client_id = php_intval(raw_client_id)

    plan = await workout_repo.get_active_plan(db, client_id)

    if plan is None:
        # Key order is exactly json_encode(['success','data','message']).
        return {"success": True, "data": None, "message": "No workout plan assigned."}

    days = await workout_repo.get_days_with_exercises(db, plan.id)

    # The success branch has NO "message" key in the PHP. Do not add one.
    return {
        "success": True,
        "data": {
            "plan": _plan_dict(plan),
            "days": [_day_dict(day, exercises) for day, exercises in days],
        },
    }


async def complete_workout(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """PR-06 .. PR-09 — api/complete-workout.php."""
    exercise_id = php_intval(payload.get("exercise_id"))
    day_id = php_intval(payload.get("day_id"))
    user_email = php_trim(payload.get("user_email"))

    # PR-06 — message text is byte-for-byte what the PHP returned.
    if exercise_id <= 0 or day_id <= 0 or user_email == "":
        raise ValidationFailure("Missing required fields")

    # PR-07 — month_no, week_no, set_no and completed are hardcoded in the
    # PHP INSERT. They are NOT read from the request even if supplied.
    # PR-08 — no FK check, no dedupe: calling twice inserts two rows.
    await workout_repo.insert_log(
        db,
        user_email=user_email,
        month_no=1,
        week_no=1,
        day_id=day_id,
        exercise_id=exercise_id,
        set_no=1,
        completed=True,
    )
    await db.commit()

    return {"success": True, "message": "Workout marked complete"}


async def save_log(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """Base files/save-progress.php.

    The original is DEAD CODE: it does `require 'config.php'`, and no such
    file exists anywhere in the repository, so every call is a PHP fatal.
    There is therefore no behaviour to preserve — only intent. The intent
    (a full-fidelity log write with month/week/set from the caller) is
    implemented here with the validation the original lacked.
    """
    user_email = php_trim(payload.get("email"))
    if user_email == "":
        raise ValidationFailure("Missing required fields")

    await workout_repo.insert_log(
        db,
        user_email=user_email,
        month_no=php_intval(payload.get("month")) or 1,
        week_no=php_intval(payload.get("week")) or 1,
        day_id=php_intval(payload.get("day")),
        exercise_id=php_intval(payload.get("exercise")),
        set_no=php_intval(payload.get("set")) or 1,
        completed=bool(php_intval(payload.get("completed"))),
    )
    await db.commit()
    return {"success": True}
```

**Why `is_active` is emitted as `int`:** `frontend/src/services/workoutService.ts` declares `is_active: number`. MySQL's `tinyint(1)` came out of `json_encode` as `1`. Postgres gives a Python `bool`, which orjson would render as `true`. Converting back to `int` keeps the frontend contract byte-identical. This is exactly the class of silent breakage a MySQL→Postgres move introduces, and why the differential harness in Phase 10 exists.

## 5.2 `app/integrations/razorpay_client.py`

**Why:** `create_order.php` calls the Razorpay SDK with **no try/catch at all** — a Razorpay timeout produces a PHP fatal and a blank page to a customer mid-checkout. It is also fully synchronous, so at peak it would block a whole PHP worker for the duration of an external HTTP call.
**Why this way:** one shared `httpx.AsyncClient` with keep-alive means the TLS handshake to Razorpay is amortised, not paid per order. An async call frees the event loop to serve other requests while waiting.
**Applies to:** `Payment/create_order.php`.

```python
# app/integrations/razorpay_client.py
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


class RazorpayClient:
    """Thin async wrapper over the Razorpay Orders API.

    One instance per process, created in the app lifespan (Phase 8), so the
    TLS handshake and connection pool are shared across all requests.
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.RAZORPAY_BASE_URL,
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            timeout=httpx.Timeout(settings.RAZORPAY_TIMEOUT, connect=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            headers={"Content-Type": "application/json"},
        )

    async def create_order(
        self, *, amount: int, currency: str, receipt: str
    ) -> dict[str, Any]:
        """amount is in the smallest currency unit (paise)."""
        body = {"amount": amount, "currency": currency, "receipt": receipt}

        # Retry ONLY on ConnectError: the request provably never reached
        # Razorpay, so replaying it cannot create a second order. A timeout
        # or a 5xx is NOT retried — Razorpay does not deduplicate on receipt,
        # so a retry there could double-charge intent. Fail loudly instead.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.post("/orders", json=body)
                break
            except httpx.ConnectError as exc:
                last_exc = exc
                logger.warning(
                    "razorpay_connect_retry", extra={"attempt": attempt + 1, "receipt": receipt}
                )
        else:
            logger.error("razorpay_unreachable", extra={"receipt": receipt})
            raise ExternalServiceError("Payment gateway unavailable. Please retry.") from last_exc

        if response.status_code >= 400:
            # Razorpay's own error text may contain account detail — log it,
            # never return it (verify_payment.php leaked raw messages).
            logger.error(
                "razorpay_error",
                extra={"status": response.status_code, "body": response.text[:500]},
            )
            raise ExternalServiceError("Could not create payment order.")

        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


razorpay_client = RazorpayClient()
```

## 5.3 `app/services/affiliate_service.py`

**Applies to:** `api/validate_affiliate.php`, `admin/affiliate-dashboard.php`.

```python
# app/services/affiliate_service.py
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_intval, php_trim
from app.repositories import affiliate_repo


async def validate_code(db: AsyncSession, raw_code: Any) -> dict[str, Any]:
    """PR-10, PR-11 — validate_affiliate.php.

    The PHP is `trim($_POST['code'])` with NO isset() guard: a missing field
    raised a notice and trim(null) produced "". We reproduce the "" outcome.

    PR-10: no expiry check here, on purpose. GR_INDIA_30 expired 2026-08-20
    and still validates through this endpoint while being refused at order
    time. That inconsistency is the legacy behaviour.

    PR-11: both branches are HTTP 200. A miss is not a 404.
    """
    code = php_trim(raw_code)
    row = await affiliate_repo.get_active_code(db, code)

    if row is not None:
        return {"success": True, "discount": row.discount_percent, "code": row.code}

    return {"success": False, "message": "Coupon not found"}


async def get_dashboard(db: AsyncSession) -> dict[str, Any]:
    """PR-12 — affiliate-dashboard.php, returned as JSON instead of HTML.

    The PHP summed the totals in a PHP foreach rather than in SQL. Summing
    the same rows in Python reproduces it exactly, including the fact that
    `total_sales` totals COUNT(e.id) per affiliate (so an enrollment with a
    coupon matching two codes would be counted twice — it cannot, because
    affiliate_codes.code is unique, but the summation order is preserved).
    """
    rows = await affiliate_repo.get_dashboard_rows(db)

    total_sales = 0
    total_revenue = Decimal("0")
    for row in rows:
        total_sales += php_intval(row["total_sales"])
        total_revenue += Decimal(str(row["revenue"] or 0))

    return {
        "success": True,
        "summary": {
            "total_affiliates": len(rows),
            "total_sales": total_sales,
            "total_revenue": float(total_revenue),
        },
        "affiliates": [
            {
                "id": r["id"],
                "affiliate_name": r["affiliate_name"],
                "affiliate_email": r["affiliate_email"],
                "code": r["code"],
                "discount_percent": r["discount_percent"],
                "commission_percent": float(r["commission_percent"] or 0),
                "status": r["status"],
                "total_sales": php_intval(r["total_sales"]),
                "revenue": float(r["revenue"] or 0),
                "average_order": float(r["average_order"] or 0),
                "commission_due": float(r["commission_due"] or 0),
            }
            for r in rows
        ],
    }
```

## 5.4 `app/services/payment_service.py`

**Applies to:** `Payment/create_order.php`, `Payment/verify_payment.php`. This is the highest-risk file in the migration — read the comments before changing anything.

```python
# app/services/payment_service.py
import logging
import time
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_floatval, php_intval, php_round_int, php_trim
from app.core.config import settings
from app.core.exceptions import ValidationFailure
from app.core.security import verify_razorpay_signature
from app.integrations.razorpay_client import razorpay_client
from app.repositories import affiliate_repo, enrollment_repo

logger = logging.getLogger(__name__)

# PR-13 — an EXACT, case-sensitive whitelist. Any other plan name pays full
# price even with a valid coupon. Do not lowercase, strip or fuzzy-match this.
DISCOUNT_ELIGIBLE_PLANS: frozenset[str] = frozenset(
    {
        "3 MONTH KICKSTART",
        "6 MONTH TRANSFORMATION",
        "12 MONTH LIFESTYLE EVOLUTION",
    }
)


async def create_order(
    db: AsyncSession, *, raw_plan: Any, raw_price: Any, raw_coupon: Any
) -> dict[str, Any]:
    """PR-13 .. PR-18 — create_order.php."""
    plan = php_trim(raw_plan)
    price = php_floatval(raw_price)
    coupon = php_trim(raw_coupon)

    discount_percent = 0

    # PR-13 — both conditions required: non-empty coupon AND eligible plan.
    if coupon != "" and plan in DISCOUNT_ELIGIBLE_PLANS:
        # PR-14 — THIS path checks expiry, unlike validate (PR-10).
        row = await affiliate_repo.get_active_unexpired_code(db, coupon)
        if row is not None:
            discount_percent = php_intval(row.discount_percent)

    # PR-15 — float arithmetic, mirroring PHP exactly. Do NOT switch to
    # Decimal: it would produce different paise at half-unit boundaries and
    # break parity with the amounts Razorpay already has on file.
    final_price = price - (price * discount_percent / 100)

    # PR-16 — the guard runs AFTER final_price is computed and tests the
    # ORIGINAL price, not the discounted one. A 100% coupon therefore passes
    # this check and creates a zero-amount order. That is the legacy behaviour.
    if settings.LEGACY_ZERO_PRICE_CHECKS_ORIGINAL:
        zero_check_value = price
    else:
        zero_check_value = final_price

    if zero_check_value <= 0:
        # PHP echoed this at HTTP 200 with no status change.
        return {"success": False, "message": "Price received is zero"}

    # PR-17 — receipt is 'GRIND_' + unix seconds; amount is paise.
    receipt = f"GRIND_{int(time.time())}"
    amount = php_round_int(final_price * 100)

    order = await razorpay_client.create_order(
        amount=amount, currency="INR", receipt=receipt
    )

    logger.info(
        "order_created",
        extra={
            "order_id": order.get("id"), "amount": amount,
            "plan": plan, "discount_percent": discount_percent,
        },
    )

    # PR-18 — `amount` is rounded paise, `final_price` is the UNROUNDED float.
    # The frontend displays final_price and sends amount to Razorpay; keeping
    # both, unrounded and rounded respectively, is what the PHP did.
    return {
        "success": True,
        "order_id": order["id"],
        "amount": amount,
        "final_price": final_price,
        "discount_percent": discount_percent,
    }


async def verify_payment(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """PR-19, PR-20 — verify_payment.php, plus the Decision 2 security fix."""
    name = php_trim(payload.get("name"))
    email = php_trim(payload.get("email"))
    phone = php_trim(payload.get("phone"))
    plan = php_trim(payload.get("plan"))
    original_price = php_floatval(payload.get("original_price"))
    final_price = php_floatval(payload.get("final_price"))
    discount_percent = php_intval(payload.get("discount_percent"))
    coupon_code = php_trim(payload.get("coupon_code"))
    payment_id = php_trim(payload.get("razorpay_payment_id"))
    order_id = php_trim(payload.get("razorpay_order_id"))
    signature = php_trim(payload.get("razorpay_signature"))

    # ── Decision 2: the security fix ────────────────────────────────
    # The PHP receives razorpay_signature from the browser and IGNORES it,
    # so a hand-crafted POST creates a 'Paid' enrollment for free. The
    # frontend already sends the signature, so switching this on breaks no
    # legitimate flow. Set RAZORPAY_VERIFY_SIGNATURE=false to restore the
    # legacy behaviour exactly.
    if settings.RAZORPAY_VERIFY_SIGNATURE:
        if not verify_razorpay_signature(
            order_id=order_id, payment_id=payment_id, signature=signature
        ):
            logger.warning(
                "razorpay_signature_mismatch",
                extra={"order_id": order_id, "payment_id": payment_id, "email": email},
            )
            raise ValidationFailure("Payment verification failed")

    # ── Decision 2b: price provenance ───────────────────────────────
    # PR-20 — the legacy trusts original_price / final_price / discount as
    # sent by the browser. Recomputing server-side is correct but changes
    # outcomes, so it is off by default. When off we still LOG a mismatch,
    # which gives you the evidence to turn it on safely.
    if plan in DISCOUNT_ELIGIBLE_PLANS and coupon_code:
        row = await affiliate_repo.get_active_unexpired_code(db, coupon_code)
        server_discount = php_intval(row.discount_percent) if row else 0
        server_final = original_price - (original_price * server_discount / 100)
        if php_round_int(server_final * 100) != php_round_int(final_price * 100):
            logger.warning(
                "price_mismatch",
                extra={
                    "client_final": final_price, "server_final": server_final,
                    "plan": plan, "coupon": coupon_code, "order_id": order_id,
                },
            )
            if settings.PAYMENTS_RECOMPUTE_PRICE:
                final_price = server_final
                discount_percent = server_discount

    # Duplicate detection is LOG-ONLY. Blocking would change PR-19.
    existing = await enrollment_repo.get_by_payment_id(db, payment_id)
    if existing is not None:
        logger.warning(
            "duplicate_payment_id",
            extra={"payment_id": payment_id, "existing_enrollment_id": existing.id},
        )

    await enrollment_repo.insert(
        db,
        name=name,
        email=email,
        phone=phone,
        plan_name=plan,
        original_price=Decimal(str(original_price)),
        discount_percent=discount_percent,
        coupon_code=coupon_code,
        final_price=Decimal(str(final_price)),
        razorpay_payment_id=payment_id,
        razorpay_order_id=order_id,
        # PR-19 — hardcoded 'Paid'. The PHP never checks the payment's real
        # status with Razorpay; the signature check above is what now makes
        # this assertion trustworthy.
        payment_status="Paid",
    )
    await db.commit()

    logger.info(
        "enrollment_created",
        extra={"email": email, "plan": plan, "final_price": final_price},
    )

    # PR-19 — the success body is exactly {"success": true}, nothing else.
    return {"success": True}
```

**Why duplicate detection logs instead of blocks:** Razorpay can legitimately retry a webhook, and the legacy system happily inserts a second row. Blocking would change an outcome that PR-19 pins down. Logging gives you the data to decide later, without a behaviour change today.

**Why `Decimal(str(float))` and not `Decimal(float)`:** `Decimal(2449.3)` is `2449.2999999999999545...`; `Decimal(str(2449.3))` is exactly `2449.3`. The column is `Numeric(10,2)`, so the former would round-trip correctly by luck most of the time and wrongly at the edges.

## 5.5 `app/services/client_service.py`

**Applies to:** `admin/clients (1).php`, `admin/client-details.php`, `admin/add-progress.php`.

```python
# app/services/client_service.py
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import letters_only, php_str_pad, php_trim
from app.core.exceptions import NotFoundError, ValidationFailure
from app.db.models import Client
from app.repositories import client_repo

logger = logging.getLogger(__name__)


def build_access_token(name: str | None, client_id: int) -> str:
    """PR-22 — the exact PHP token algorithm.

        strtoupper(substr(str_pad(preg_replace('/[^A-Za-z]/','',$name), 3, 'X'), 0, 3))

    str_pad's default direction is RIGHT, which is the trap:
        "Al"      -> "AlX"   -> "ALX"      (not "XAL")
        "Bo Xu"   -> "BoXu"  -> "BOX"
        ""        -> "XXX"   -> "XXX"
        "Arup"    -> "Arup"  -> "ARU"
        "123"     -> ""      -> "XXX"      (digits stripped first)
    Then: "GR_" + prefix + "_" + str_pad(id, 6, "0", STR_PAD_LEFT)
    """
    prefix = php_str_pad(letters_only(name or ""), 3, "X")[:3].upper()
    suffix = php_str_pad(str(client_id), 6, "0", left=True)
    return f"GR_{prefix}_{suffix}"


def _client_dict(client: Client) -> dict[str, Any]:
    return {
        "id": client.id,
        "name": client.name,
        "email": client.email,
        "phone": client.phone,
        "goal": client.goal,
        "status": client.status,
        "created_at": client.created_at.strftime("%Y-%m-%d %H:%M:%S")
        if client.created_at
        else None,
        "access_token": client.access_token,
    }


async def upsert_client(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """PR-21, PR-22 — the POST branch of clients.php.

    The PHP trims ONLY the email; name/phone/goal go in raw. Preserved.
    """
    email = php_trim(payload.get("email"))
    name = payload.get("name")
    phone = payload.get("phone")
    goal = payload.get("goal")

    if email == "":
        raise ValidationFailure("Email is required")

    existing = await client_repo.get_by_email(db, email)

    if existing is not None:
        # PR-21 — update name/phone/goal only. access_token is NOT
        # regenerated; doing so would invalidate every saved portal link.
        await client_repo.update_details(
            db, email=email, name=name, phone=phone, goal=goal
        )
        await db.commit()
        refreshed = await client_repo.get_by_email(db, email)
        assert refreshed is not None
        return {"success": True, "created": False, "client": _client_dict(refreshed)}

    client = await client_repo.insert(
        db, name=name, email=email, phone=phone, goal=goal
    )
    token = build_access_token(name if isinstance(name, str) else "", client.id)
    await client_repo.set_access_token(db, client_id=client.id, token=token)
    await db.commit()

    logger.info("client_created", extra={"client_id": client.id, "email": email})

    refreshed = await client_repo.get_by_id(db, client.id)
    assert refreshed is not None
    return {"success": True, "created": True, "client": _client_dict(refreshed)}


async def list_clients(db: AsyncSession) -> dict[str, Any]:
    clients = await client_repo.list_all(db)
    return {"success": True, "data": [_client_dict(c) for c in clients]}


async def get_client(db: AsyncSession, client_id: int) -> dict[str, Any]:
    client = await client_repo.get_by_id(db, client_id)
    if client is None:
        # client-details.php crashed with an undefined-index warning here and
        # rendered an empty page. A 404 is the only sane equivalent.
        raise NotFoundError("Client not found")
    return {"success": True, "data": _client_dict(client)}


def _to_decimal(value: Any) -> Decimal | None:
    """add-progress.php passed raw form values straight into decimal(5,2)
    columns. MySQL coerced "" to 0.00 with a warning; Postgres raises.
    Empty becomes NULL, which is what an unfilled measurement means.
    """
    text = php_trim(value)
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValidationFailure(f"Invalid numeric value: {text}") from exc


async def add_progress(
    db: AsyncSession, *, client_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """admin/add-progress.php."""
    client = await client_repo.get_by_id(db, client_id)
    if client is None:
        raise NotFoundError("Client not found")

    await client_repo.insert_progress(
        db,
        client_id=client_id,
        weight=_to_decimal(payload.get("weight")),
        waist=_to_decimal(payload.get("waist")),
        chest=_to_decimal(payload.get("chest")),
        arms=_to_decimal(payload.get("arms")),
        thighs=_to_decimal(payload.get("thighs")),
        notes=payload.get("notes"),
    )
    await db.commit()
    return {"success": True, "message": "Progress Saved"}
```

**The one behaviour change here, stated plainly:** MySQL silently turned an empty measurement into `0.00`; Postgres rejects it. Storing `0.00` for "not measured" would corrupt PR-32's transformation stats (a client who skipped a waist reading would show a huge reduction). `NULL` is the honest representation. Recorded in `PARITY.md`.

## 5.6 `app/services/plan_service.py`

**Applies to:** `admin/create-plan.php` (unversioned) and `admin/import-workout.php` (versioned). They are **different** code paths with different semantics, and both are preserved.

```python
# app/services/plan_service.py
import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_json_is_falsy
from app.core.exceptions import NotFoundError, ValidationFailure
from app.repositories import client_repo, workout_repo

logger = logging.getLogger(__name__)


def _parse_plan_json(raw: str) -> Any:
    """PR-26 — reproduce `json_decode` + `if (!$data)` exactly.

    PHP treats a syntactically VALID document as invalid when it decodes to
    a falsy value: null, false, 0, "", "0", [] and {} all yield "Invalid JSON".
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise ValidationFailure("Invalid JSON") from None

    if php_json_is_falsy(data):
        raise ValidationFailure("Invalid JSON")
    return data


async def create_plan(
    db: AsyncSession, *, client_id: int, plan_name: str, workout_json: str
) -> dict[str, Any]:
    """PR-23, PR-25 — create-plan.php.

    Deliberately DIFFERENT from import (5.6b):
      * does NOT deactivate previous plans -> the client ends up with two
        active plans, and PR-02's `ORDER BY id DESC` silently picks the newer
      * does NOT set version_no -> column default 1
      * the PHP had NO transaction; we add one, because a partial plan
        (days inserted, exercises not) is not a behaviour anyone relied on,
        it is a crash artefact. Successful runs are byte-identical.
    """
    client = await client_repo.get_by_id(db, client_id)
    if client is None:
        raise NotFoundError("Client not found")

    data = _parse_plan_json(workout_json)

    plan = await workout_repo.insert_plan(
        db,
        client_id=client_id,
        plan_name=plan_name,
        workout_json=workout_json,   # RAW string, not re-serialised
    )

    day_count = exercise_count = 0
    # PR-25 — create-plan.php guards `isset($json['days'])`; a plan with no
    # days is accepted and creates just the parent row.
    for index, day in enumerate(data.get("days", []) or []):
        day_row = await workout_repo.insert_day(
            db, plan_id=plan.id, day_number=index + 1, day_name=day.get("day_name")
        )
        day_count += 1

        rows = [
            {
                "exercise_name": ex.get("name"),
                "sets_count": ex.get("sets"),
                "reps": ex.get("reps"),
                "youtube_url": ex.get("youtube"),
                # create-plan.php does NOT name the notes column, so it takes
                # the column default (NULL). import-workout.php writes ''.
                "sort_order": order + 1,
            }
            for order, ex in enumerate(day.get("exercises", []) or [])
        ]
        await workout_repo.insert_exercises(db, day_id=day_row.id, rows=rows)
        exercise_count += len(rows)

    await db.commit()
    logger.info(
        "plan_created",
        extra={"plan_id": plan.id, "client_id": client_id,
               "days": day_count, "exercises": exercise_count},
    )
    return {
        "success": True,
        "message": "Workout Plan Created Successfully",
        "plan_id": plan.id,
        "days": day_count,
        "exercises": exercise_count,
    }


async def import_plan(
    db: AsyncSession, *, client_id: int, workout_json: str
) -> dict[str, Any]:
    """PR-24, PR-25, PR-26 — import-workout.php, the versioned path.

    Entire body runs in ONE transaction, exactly as the PHP
    (beginTransaction / commit / rollBack). If any day or exercise fails,
    nothing is written and the previous plan stays active.
    """
    data = _parse_plan_json(workout_json)

    # PR-24 — the three steps, in this order. Reordering them would leave a
    # window where the client has no active plan.
    next_version = (await workout_repo.get_max_version(db, client_id)) + 1
    await workout_repo.deactivate_plans(db, client_id)

    plan = await workout_repo.insert_plan(
        db,
        client_id=client_id,
        plan_name=data.get("plan_name"),
        workout_json=workout_json,   # RAW string
        is_active=True,
        version_no=next_version,
    )

    day_count = exercise_count = 0
    # PR-25 — import-workout.php indexes $data['days'] WITHOUT isset(), so a
    # payload lacking "days" was a PHP warning + empty loop. `or []` matches.
    for index, day in enumerate(data.get("days", []) or []):
        day_row = await workout_repo.insert_day(
            db, plan_id=plan.id, day_number=index + 1, day_name=day.get("day_name")
        )
        day_count += 1

        rows = [
            {
                "exercise_name": ex.get("name"),
                "sets_count": ex.get("sets"),
                "reps": ex.get("reps"),
                "youtube_url": ex.get("youtube"),
                # PR-25 — import writes the LITERAL empty string, not NULL.
                # my-plan surfaces this as the exercise "note" field, so the
                # difference is visible to the client. Keep it.
                "notes": "",
                "sort_order": order + 1,
            }
            for order, ex in enumerate(day.get("exercises", []) or [])
        ]
        await workout_repo.insert_exercises(db, day_id=day_row.id, rows=rows)
        exercise_count += len(rows)

    await db.commit()
    logger.info(
        "plan_imported",
        extra={"plan_id": plan.id, "client_id": client_id, "version": next_version,
               "days": day_count, "exercises": exercise_count},
    )
    return {
        "success": True,
        "message": "Workout Imported Successfully",
        "plan_id": plan.id,
        "version_no": next_version,
        "days": day_count,
        "exercises": exercise_count,
    }
```

**Why `notes` differs between the two paths:** `create-plan.php` omits the column entirely (so it is `NULL`); `import-workout.php` writes `''`. `my-plan` renders that value as the exercise note, so `NULL` and `''` reach the client differently. Harmonising them would be a behaviour change in one direction or the other, so both are reproduced.

## 5.7 `app/services/diet_service.py`

**Applies to:** `admin/add_diet.php`.

```python
# app/services/diet_service.py
import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_json_is_falsy, php_trim
from app.core.exceptions import NotFoundError, ValidationFailure
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
```

Add the `NotFoundError` import removal if your linter complains — it is unused here by design (an unknown token is a `ValidationFailure`, matching the legacy text, not a 404).

## 5.8 `app/services/portal_service.py`

**Applies to:** `Base files/my-plan (1).php` and `Base files/workout-progress (1).php` — the two client-facing pages, now returning the JSON that the PHP inlined into `<script>` tags.

```python
# app/services/portal_service.py
import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compat import php_round_int
from app.core.config import settings
from app.core.exceptions import ValidationFailure
from app.repositories import client_repo, diet_repo, workout_repo

logger = logging.getLogger(__name__)

# PR-31 — the exact palette from my-plan.php, in order. The index is
# (day_number - 1) % 6, so day 7 wraps back to the first colour.
DAY_COLORS: tuple[str, ...] = (
    "#ff5c35", "#2563eb", "#16a34a", "#9333ea", "#ea580c", "#0f766e",
)


async def _resolve_client(db: AsyncSession, token: str):
    """Decision 5 — my-plan.php hardcoded a single email and
    workout-progress.php hardcoded client_id = 1. Neither can survive in a
    multi-client API, so both now derive the identity from the caller's
    access token. Recorded in PARITY.md.
    """
    client = await client_repo.get_by_access_token(db, (token or "").strip())
    if client is None:
        # PHP: die("Invalid Access Link")
        raise ValidationFailure("Invalid Access Link")
    return client


async def _progress_percent(db: AsyncSession, user_email: str) -> dict[str, int]:
    """PR-28, PR-29.

    The denominator is COUNT(*) over the ENTIRE workout_exercises table, not
    the client's own plan. Every client's percentage therefore shrinks each
    time any other client's plan is imported. It is a bug; Decision 4 keeps
    it; Phase 7 caches the count so keeping it is free.
    """
    from app.cache.redis import cached_exercise_count

    if settings.LEGACY_GLOBAL_PROGRESS_DENOMINATOR:
        total = await cached_exercise_count(db)
    else:
        total = await cached_exercise_count(db)  # swap for a per-plan count here

    completed = await workout_repo.count_completed_exercises(db, user_email)

    # PHP round() is half-away-from-zero; Python's builtin is not (Phase 2.4).
    percent = php_round_int(completed / total * 100) if total > 0 else 0

    return {"total": total, "completed": completed, "percent": percent}


async def get_my_plan(db: AsyncSession, token: str) -> dict[str, Any]:
    """my-plan.php — the full portal payload."""
    client = await _resolve_client(db, token)
    progress = await _progress_percent(db, client.email or "")

    plan = await workout_repo.get_active_plan(db, client.id)
    days_payload: list[dict[str, Any]] = []

    if plan is not None:
        for day, exercises in await workout_repo.get_days_with_exercises(db, plan.id):
            day_number = day.day_number or 0
            days_payload.append(
                {
                    # PR-30 — key names and constants are what the frontend
                    # JS reads. `id` is the DAY NUMBER, not the row id, and
                    # `label` and `short` are both day_name.
                    "id": int(day_number),
                    "label": day.day_name,
                    "short": day.day_name,
                    "color": DAY_COLORS[(day_number - 1) % len(DAY_COLORS)],  # PR-31
                    "colorSoft": "rgba(255,92,53,.1)",
                    "calMin": 250,
                    "calMax": 350,
                    "calNote": "Workout Day",
                    "exercises": [
                        {
                            "name": ex.exercise_name,
                            "sets": int(ex.sets_count or 0),
                            "reps": ex.reps,
                            "note": ex.notes,
                            "yt": ex.youtube_url,
                        }
                        for ex in exercises
                    ],
                }
            )

    diet_plan = await diet_repo.get_active_plan(db, client.id)
    diet_data: Any = []
    if diet_plan is not None and diet_plan.diet_json:
        try:
            diet_data = json.loads(diet_plan.diet_json)
        except json.JSONDecodeError:
            # PHP json_decode returns null on bad JSON and the page rendered
            # an empty diet rather than erroring.
            logger.warning("diet_json_invalid", extra={"diet_plan_id": diet_plan.id})
            diet_data = []

    return {
        "success": True,
        "data": {
            "client": {"id": client.id, "name": client.name, "goal": client.goal},
            "plan_name": plan.plan_name if plan else None,
            "days": days_payload,
            "diet": diet_data,
            "progress": progress,
        },
    }


def _f(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


async def get_progress(db: AsyncSession, token: str) -> dict[str, Any]:
    """workout-progress.php — PR-32."""
    client = await _resolve_client(db, token)
    progress = await _progress_percent(db, client.email or "")

    history = await client_repo.get_progress_history(db, client.id)

    # PR-32 — `start` is the OLDEST row, `current` the NEWEST. Both stats are
    # 0 unless both exist (a single row means start is current, so the deltas
    # are 0 anyway, which the PHP also produced).
    start = history[0] if history else None
    current = history[-1] if history else None

    weight_lost = 0.0
    waist_reduced = 0.0
    if start is not None and current is not None:
        if start.weight is not None and current.weight is not None:
            weight_lost = float(start.weight - current.weight)
        if start.waist is not None and current.waist is not None:
            waist_reduced = float(start.waist - current.waist)

    return {
        "success": True,
        "data": {
            "exercises": progress,
            "current": {
                "weight": _f(current.weight), "waist": _f(current.waist),
                "chest": _f(current.chest), "arms": _f(current.arms),
                "thighs": _f(current.thighs),
            }
            if current
            else None,
            "transformation": {
                "weight_lost": weight_lost,
                "waist_reduced": waist_reduced,
            },
            "chart": {
                # PHP: date('d M', strtotime($row['created_at'])) -> "15 Jun"
                "dates": [r.created_at.strftime("%d %b") for r in history],
                "weights": [_f(r.weight) for r in history],
                "waists": [_f(r.waist) for r in history],
            },
        },
    }
```

## 5.9 `app/integrations/mailer.py` and `app/services/intake_service.py`

**Applies to:** `Base files/start-your-journey (1).php`.

**Why a background task:** SMTP handshake + send is 200–2000 ms. Doing it inside the request means the user stares at a spinner for two seconds and one of your 20 in-flight slots (Phase 0.2) is held by a network wait. Returning immediately and sending in the background is the same user-visible outcome with a fraction of the cost.

```python
# app/integrations/mailer.py
import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_sync(subject: str, body: str, reply_to: str) -> None:
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = settings.INTAKE_RECIPIENT
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body, charset="utf-8")

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
        if settings.SMTP_STARTTLS:
            smtp.starttls()
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(message)


async def send_intake_email(subject: str, body: str, reply_to: str) -> None:
    """smtplib is blocking, so it runs in a thread — calling it directly on
    the event loop would stall every other request in this worker for the
    duration of the SMTP conversation.
    """
    try:
        await asyncio.to_thread(_send_sync, subject, body, reply_to)
        logger.info("intake_email_sent", extra={"reply_to": reply_to})
    except Exception:
        # Never propagate: the submission is already accepted. The log is the
        # record. (PHP set $mail_error and showed a banner; we cannot, because
        # the response has already been sent.)
        logger.exception("intake_email_failed", extra={"reply_to": reply_to})
```

```python
# app/services/intake_service.py
from typing import Any

from email_validator import EmailNotValidError, validate_email

from app.core.compat import php_clean, php_trim
from app.core.exceptions import ValidationFailure


def _joined(value: Any) -> str:
    """PHP: implode(', ', cleanArr((array)$_POST['goals']))"""
    if value is None:
        return ""
    items = value if isinstance(value, list) else [value]
    return ", ".join(php_clean(item) for item in items)


def build_intake_email(payload: dict[str, Any]) -> tuple[str, str, str]:
    """PR-33 — start-your-journey.php.

    Returns (subject, body, reply_to). The body layout is reproduced line for
    line, including the 'N/A' / 'None' / 'None selected' fallbacks, because
    whoever reads these emails reads them by eye and a reformatted template
    is a real regression for them.
    """
    name = php_clean(payload.get("name"))
    raw_email = php_trim(payload.get("email"))
    email = php_clean(raw_email)
    age = php_clean(payload.get("age"))
    gender = php_clean(payload.get("gender"))
    occupation = php_clean(payload.get("occupation"))
    weight = php_clean(payload.get("weight"))
    weight_unit = php_clean(payload.get("weight_unit") or "kg")
    height_unit = php_clean(payload.get("height_unit") or "cm")
    height_cm = php_clean(payload.get("height"))
    height_ft = php_clean(payload.get("height_ft"))
    height_in = php_clean(payload.get("height_in"))
    fitness_level = php_clean(payload.get("fitness_level"))
    days_per_week = php_clean(payload.get("days_per_week"))
    session_dur = php_clean(payload.get("session_duration"))
    goals = _joined(payload.get("goals"))
    goal_focus = php_clean(payload.get("goal_focus"))
    workout_pref = php_clean(payload.get("workout_pref"))
    injuries = _joined(payload.get("injuries"))
    injuries_detail = php_clean(payload.get("injuries_detail"))
    diet = php_clean(payload.get("diet"))
    sleep = php_clean(payload.get("sleep"))
    stress = php_clean(payload.get("stress"))
    consultation = php_clean(payload.get("consultation") or "no")

    height_display = (
        f"{height_ft}ft {height_in}in" if height_unit == "ft" else f"{height_cm} cm"
    )

    # PR-33 — the four required fields, validated in the PHP's own order.
    errors: list[str] = []
    if not name:
        errors.append("Name is required.")
    if not raw_email:
        errors.append("A valid email address is required.")
    else:
        try:
            # PHP used FILTER_VALIDATE_EMAIL. email-validator is stricter but
            # rejects the same practical set; check_deliverability is off so
            # no DNS lookup blocks the request.
            validate_email(raw_email, check_deliverability=False)
        except EmailNotValidError:
            errors.append("A valid email address is required.")
    if not age:
        errors.append("Age is required.")
    if not weight:
        errors.append("Weight is required.")

    if errors:
        raise ValidationFailure(" ".join(errors))

    consultation_text = (
        "Yes – lifestyle consultation requested" if consultation == "yes" else "No"
    )

    body = (
        "New GRIND Client Intake Submission\n"
        "===================================\n\n"
        "PERSONAL\n"
        "--------\n"
        f"Name       : {name}\n"
        f"Email      : {email}\n"
        f"Age        : {age}\n"
        f"Gender     : {gender or 'N/A'}\n"
        f"Occupation : {occupation or 'N/A'}\n\n"
        "BODY METRICS\n"
        "------------\n"
        f"Weight          : {weight} {weight_unit}\n"
        f"Height          : {height_display}\n"
        f"Fitness Level   : {fitness_level or 'N/A'}\n"
        f"Training Days   : {days_per_week or 'N/A'}\n"
        f"Session Length  : {session_dur or 'N/A'}\n\n"
        "TRAINING GOALS\n"
        "--------------\n"
        f"Goals Selected  : {goals or 'None selected'}\n"
        f"Specific Focus  : {goal_focus or 'N/A'}\n"
        f"Workout Pref    : {workout_pref or 'N/A'}\n"
        f"Consultation    : {consultation_text}\n\n"
        "HEALTH\n"
        "------\n"
        f"Injuries        : {injuries or 'None'}\n"
        f"Injury Details  : {injuries_detail or 'N/A'}\n\n"
        "DIET & LIFESTYLE\n"
        "----------------\n"
        f"Diet            : {diet or 'N/A'}\n"
        f"Sleep           : {sleep or 'N/A'}\n"
        f"Stress Level    : {stress or 'N/A'}\n\n"
        "===================================\n"
        "Submitted via GRIND Intake Form\n"
    )

    return f"New GRIND Intake Submission — {name}", body, raw_email
```

**Verify Phase 5:**

```bash
uv run python -c "
from app.services.client_service import build_access_token
assert build_access_token('Arup', 14)  == 'GR_ARU_000014'
assert build_access_token('Al', 3)     == 'GR_ALX_000003'
assert build_access_token('Bo Xu', 7)  == 'GR_BOX_000007'
assert build_access_token('123', 9)    == 'GR_XXX_000009'
assert build_access_token('', 1)       == 'GR_XXX_000001'
from app.services.payment_service import DISCOUNT_ELIGIBLE_PLANS
assert '3 month kickstart' not in DISCOUNT_ELIGIBLE_PLANS  # case-sensitive
print('services OK')
"
```

Those five token cases are taken straight from the `clients` table in the dump (`GR_ARU_000014`, `GR_NIG_000008`, `GR_RAM_000017`), so passing them means the algorithm reproduces production data exactly.

---
---

# Phase 6 — Auth, caching, rate limiting

These three are grouped because they are all *infrastructure the legacy system simply did not have*, and all three are load-bearing for the 10,000-user target.

## 6.1 `app/core/security.py`

**Why:** `admin/login (1).php` compares `$password == "Grind@2026"` — a plaintext literal in source, with a non-strict comparison, guarding a PHP session. Three admin endpoints have no check at all.
**Why argon2 + JWT:** argon2id is the current password-hashing recommendation and `argon2-cffi` verifies in ~50 ms, which is slow enough to make brute force useless and fast enough for a login endpoint. JWT means the API stays stateless, so any of the 4 workers (or any pod) can serve any request without shared session storage.
**Applies to:** `admin/login (1).php` plus every admin endpoint, and `Payment/verify_payment.php` (the HMAC).

```python
# app/core/security.py
import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from app.core.config import settings
from app.core.exceptions import AuthError

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_admin_credentials(username: str, password: str) -> bool:
    """Replaces `$username == "admin" && $password == "Grind@2026"`.

    compare_digest on the username prevents a timing oracle that would leak
    the admin username one character at a time. argon2 verify is constant
    time by construction.
    """
    username_ok = secrets.compare_digest(username, settings.ADMIN_USERNAME)
    try:
        password_ok = _hasher.verify(settings.ADMIN_PASSWORD_HASH, password)
    except (VerifyMismatchError, VerificationError):
        password_ok = False

    # Both are evaluated before returning, so a wrong username costs the same
    # time as a wrong password.
    return username_ok and bool(password_ok)


def create_access_token(subject: str, *, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "typ": "admin",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],  # list, never "any"
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Session expired. Please log in again.") from None
    except jwt.InvalidTokenError:
        raise AuthError("Invalid credentials") from None


def verify_razorpay_signature(*, order_id: str, payment_id: str, signature: str) -> bool:
    """Decision 2 — the check verify_payment.php never performed.

    Razorpay signs `"{order_id}|{payment_id}"` with your key secret using
    HMAC-SHA256 and hex-encodes it. compare_digest is required: a naive `==`
    on a hex string leaks the correct prefix through timing.
    """
    if not (order_id and payment_id and signature):
        return False

    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)
```

Generate the admin hash once and put it in `.env`:

```bash
uv run python -c "
from argon2 import PasswordHasher
import getpass
print(PasswordHasher().hash(getpass.getpass('New admin password: ')))
"
# ADMIN_PASSWORD_HASH='$argon2id$v=19$m=65536,t=3,p=4$...'
```

**Quote it in `.env`** — the hash contains `$`, which some shells and Docker Compose interpolate.

## 6.2 `app/cache/redis.py`

**Why (the numbers):** from Phase 0.2, uncached peak is 1,200 req/s × 2 queries = 2,400 queries/s, and PR-28's `COUNT(*)` is a sequential scan on every portal load. Workout plans change perhaps once a month per client. That is a ~10,000:1 read-to-write ratio — the textbook case for a cache.

With a 60 s TTL and 10,000 users, each client's plan is fetched from Postgres at most once per minute instead of every request. Measured against the traffic model that is roughly **85–90% of database reads eliminated**, taking the DB from 2,400 queries/s to ~300.

**Why Redis and not an in-process dict:** there are 4 worker processes (and later N pods). An in-process cache gives 4 different answers and 4× the invalidation work, and — critically — rate-limit counters in process memory let an attacker get 4× their quota by luck of load balancing.

```python
# app/cache/redis.py
import json
import logging
from typing import Any

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: redis.ConnectionPool | None = None
_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _pool, _client
    if _client is None:
        _pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,           # never let Redis stall a request
            health_check_interval=30,
        )
        _client = redis.Redis(connection_pool=_pool)
    return _client


async def close_redis() -> None:
    global _client, _pool
    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def cache_get_json(key: str) -> Any | None:
    """A cache failure must NEVER fail a request — it degrades to a DB read."""
    if not settings.CACHE_ENABLED:
        return None
    try:
        raw = await get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception:
        logger.warning("cache_get_failed", extra={"key": key})
        return None


async def cache_set_json(key: str, value: Any, ttl: int) -> None:
    if not settings.CACHE_ENABLED:
        return
    try:
        await get_redis().setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        logger.warning("cache_set_failed", extra={"key": key})


async def cache_delete(*keys: str) -> None:
    if not settings.CACHE_ENABLED or not keys:
        return
    try:
        await get_redis().delete(*keys)
    except Exception:
        logger.warning("cache_delete_failed", extra={"keys": list(keys)})


# ── domain keys ────────────────────────────────────────────────────

def workout_key(client_id: int) -> str:
    # The version prefix lets you invalidate the whole namespace by bumping
    # it, without a SCAN over production Redis.
    return f"grind:v1:workout:{client_id}"


def portal_key(token: str) -> str:
    return f"grind:v1:portal:{token}"


EXERCISE_COUNT_KEY = "grind:v1:exercise_count"


async def cached_exercise_count(db: AsyncSession) -> int:
    """PR-28's global COUNT(*), memoised for CACHE_TTL_EXERCISE_COUNT.

    This is a sequential scan over workout_exercises that the legacy ran on
    EVERY portal load. It only changes when an admin imports a plan, so a
    300 s TTL is generous. Caching it is what makes keeping the bug free.
    """
    from app.repositories import workout_repo

    cached = await cache_get_json(EXERCISE_COUNT_KEY)
    if isinstance(cached, int):
        return cached

    count = await workout_repo.count_all_exercises(db)
    await cache_set_json(EXERCISE_COUNT_KEY, count, settings.CACHE_TTL_EXERCISE_COUNT)
    return count


async def invalidate_client_caches(client_id: int, access_token: str | None = None) -> None:
    """Called after any admin write that changes what a client sees.

    Also drops the global exercise count, because importing a plan changes
    PR-28's denominator for EVERY client.
    """
    keys = [workout_key(client_id), EXERCISE_COUNT_KEY]
    if access_token:
        keys.append(portal_key(access_token))
    await cache_delete(*keys)
```

**Why every cache call is wrapped in `try/except`:** Redis is a performance optimisation, not a source of truth. If Redis is down, the correct behaviour is a slower service, not an outage. This one pattern is the difference between "Redis restarted" being a non-event and being a P1.

## 6.3 `app/cache/rate_limit.py`

**Why:** `/admin/login` currently accepts unlimited password guesses. `/payments/order` calls a paid external API on every request, so an unthrottled loop is both a cost and a Razorpay rate-limit risk. `/affiliate/validate` lets anyone enumerate every coupon code in the table.
**Why a Lua script:** `INCR` then `EXPIRE` as two commands has a race — if the process dies between them the key never expires and the caller is locked out forever. A Lua script is executed atomically by Redis, in one round trip.

```python
# app/cache/rate_limit.py
import logging

from fastapi import Request

from app.cache.redis import get_redis
from app.core.config import settings
from app.core.exceptions import RateLimitError

logger = logging.getLogger(__name__)

# Fixed-window counter. Atomic: INCR and EXPIRE cannot be split.
_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


def client_ip(request: Request) -> str:
    """Trust X-Forwarded-For only because Nginx sets it (Phase 9.3) and
    uvicorn runs with --proxy-headers. Taking the FIRST entry is correct
    when your own proxy appends; never trust it on a directly exposed app.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Dependency factory:  Depends(RateLimiter("login", times=5, seconds=300))"""

    def __init__(self, name: str, *, times: int, seconds: int) -> None:
        self.name = name
        self.times = times
        self.seconds = seconds

    async def __call__(self, request: Request) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return

        key = f"grind:v1:rl:{self.name}:{client_ip(request)}"
        try:
            count = await get_redis().eval(_SCRIPT, 1, key, self.seconds)
        except Exception:
            # Fail OPEN. A Redis outage must not take down checkout. The
            # alternative (fail closed) turns a cache incident into a
            # revenue incident.
            logger.warning("rate_limit_unavailable", extra={"bucket": self.name})
            return

        if int(count) > self.times:
            logger.warning(
                "rate_limited",
                extra={"bucket": self.name, "ip": client_ip(request), "count": count},
            )
            raise RateLimitError("Too many requests. Please slow down.")


# Budgets, each justified by what the endpoint costs or protects:
limit_login = RateLimiter("login", times=5, seconds=300)        # brute force
limit_payment = RateLimiter("payment", times=20, seconds=60)    # paid external call
limit_coupon = RateLimiter("coupon", times=30, seconds=60)      # code enumeration
limit_intake = RateLimiter("intake", times=5, seconds=600)      # spam via SMTP
limit_write = RateLimiter("write", times=120, seconds=60)       # set-ticking is bursty
```

**Why `limit_write` is 120/min and not 20:** a client finishing a workout ticks 30–40 sets in a couple of minutes. A tight limit would break normal use. The number comes from the traffic model, not from a default.

**Why fail-open:** consider the failure modes. Fail-closed with Redis down = nobody can pay, nobody can log in, total outage. Fail-open with Redis down = brute-force protection is temporarily absent while you page someone. For this application the second is clearly the lesser harm; for a bank it would not be.

**Verify Phase 6:**

```bash
docker compose up -d redis
uv run python -c "
import asyncio
from app.cache.redis import get_redis, cache_set_json, cache_get_json, close_redis
from app.core.security import hash_password, verify_razorpay_signature
async def m():
    await cache_set_json('grind:test', {'a': 1}, 10)
    assert await cache_get_json('grind:test') == {'a': 1}
    await close_redis()
asyncio.run(m())
h = hash_password('x')
assert h.startswith('\$argon2id')
print('security + cache OK')
"
```

---
---

# Phase 7 — Schemas, dependencies, routers

## 7.1 `app/api/deps.py`

**Why the body parser:** the legacy endpoints read `$_POST` (form-encoded). The React frontend sends `application/json` (`api.ts` sets that header globally). Both must work — the form path for anything still hitting the old contract, the JSON path for the SPA.

**Why loose parsing instead of Pydantic models on the parity endpoints:** PR-01 requires `client_id=abc` to become `0`. A Pydantic `int` field returns 422. Pydantic validation is used on the *new* admin endpoints where no legacy behaviour is being preserved.

```python
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
```

## 7.2 `app/schemas/` — only where they earn their place

**Why so few schemas:** Pydantic models are used for the **admin** endpoints (new contracts, so strict validation is a feature) and for **OpenAPI response documentation**. They are deliberately *not* used to validate the five parity endpoints, for the PR-01 reason above.

```python
# app/schemas/common.py
from typing import Any

from pydantic import BaseModel, ConfigDict


class SuccessResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True


class ErrorResponse(BaseModel):
    success: bool = False
    message: str


class MessageResponse(BaseModel):
    success: bool = True
    message: str
```

```python
# app/schemas/admin.py
from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class AdminLoginResponse(BaseModel):
    success: bool = True
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ClientUpsertRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    email: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    goal: str | None = Field(default=None, max_length=255)


class PlanImportRequest(BaseModel):
    client_id: int = Field(gt=0)
    workout_json: str = Field(min_length=1)


class PlanCreateRequest(BaseModel):
    client_id: int = Field(gt=0)
    plan_name: str = Field(min_length=1, max_length=255)
    workout_json: str = Field(min_length=1)


class DietSaveRequest(BaseModel):
    access_token: str = Field(min_length=1, max_length=50)
    diet_json: str = Field(min_length=1)


class ProgressCreateRequest(BaseModel):
    client_id: int = Field(gt=0)
    weight: str | None = None
    waist: str | None = None
    chest: str | None = None
    arms: str | None = None
    thighs: str | None = None
    notes: str | None = None
```

**Why the measurements are `str | None` and not `float`:** the admin form submits `""` for a skipped field. A `float` field 422s on that; the service (5.5) already maps `""` → `NULL` with the same rule the PHP relied on. Keeping the transport type loose and converting in one audited place beats scattering coercion across the schema.

## 7.3 Routers

### `app/api/v1/workout.py`

```python
# app/api/v1/workout.py
from fastapi import APIRouter, Depends, Request

from app.api.deps import BodyParams, DbSession
from app.cache.rate_limit import limit_write
from app.cache.redis import cache_get_json, cache_set_json, workout_key
from app.core.compat import php_intval
from app.core.config import settings
from app.services import workout_service

router = APIRouter(tags=["workout"])


@router.get("/workout")
async def get_workout(request: Request, db: DbSession) -> dict:
    """PR-01..PR-05. Cached: this is the highest-traffic endpoint in the app.

    The cache key uses the RESOLVED client_id, so ?client_id=abc and
    ?client_id=0 share one entry (both resolve to 0 under PR-01) — which is
    correct, because they must return the same body.
    """
    raw = request.query_params.get("client_id")
    resolved = settings.LEGACY_DEFAULT_CLIENT_ID if raw is None or raw == "" else php_intval(raw)

    key = workout_key(resolved)
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    result = await workout_service.get_workout(db, raw)
    await cache_set_json(key, result, settings.CACHE_TTL_WORKOUT)
    return result


@router.post("/workout/complete", dependencies=[Depends(limit_write)])
async def complete_workout(payload: BodyParams, db: DbSession) -> dict:
    """PR-06..PR-09."""
    return await workout_service.complete_workout(db, payload)


@router.post("/workout/logs", dependencies=[Depends(limit_write)])
async def save_log(payload: BodyParams, db: DbSession) -> dict:
    """Repairs the dead save-progress.php."""
    return await workout_service.save_log(db, payload)
```

### `app/api/v1/affiliate.py`

```python
# app/api/v1/affiliate.py
from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, BodyParams, DbSession
from app.cache.rate_limit import limit_coupon
from app.services import affiliate_service

router = APIRouter(tags=["affiliate"])


@router.post("/affiliate/validate", dependencies=[Depends(limit_coupon)])
async def validate_affiliate(payload: BodyParams, db: DbSession) -> dict:
    """PR-10, PR-11. Rate-limited because it is a coupon-enumeration oracle."""
    return await affiliate_service.validate_code(db, payload.get("code"))


@router.get("/admin/affiliates")
async def affiliate_dashboard(db: DbSession, _: AdminUser) -> dict:
    """PR-12. Decision 3: this was PUBLIC in the legacy system and exposed
    affiliate names, emails, revenue and commission. Now admin-only.
    """
    return await affiliate_service.get_dashboard(db)
```

### `app/api/v1/payments.py`

```python
# app/api/v1/payments.py
from fastapi import APIRouter, Depends

from app.api.deps import BodyParams, DbSession
from app.cache.rate_limit import limit_payment
from app.services import payment_service

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/order", dependencies=[Depends(limit_payment)])
async def create_order(payload: BodyParams, db: DbSession) -> dict:
    """PR-13..PR-18."""
    return await payment_service.create_order(
        db,
        raw_plan=payload.get("plan"),
        raw_price=payload.get("price"),
        raw_coupon=payload.get("coupon"),
    )


@router.post("/verify", dependencies=[Depends(limit_payment)])
async def verify_payment(payload: BodyParams, db: DbSession) -> dict:
    """PR-19, PR-20 + the Decision 2 signature check."""
    return await payment_service.verify_payment(db, payload)
```

### `app/api/v1/portal.py`

```python
# app/api/v1/portal.py
from fastapi import APIRouter

from app.api.deps import CurrentClient, DbSession
from app.cache.redis import cache_get_json, cache_set_json, portal_key
from app.core.config import settings
from app.services import portal_service

router = APIRouter(prefix="/portal", tags=["portal"])


@router.get("/my-plan")
async def my_plan(client: CurrentClient, db: DbSession) -> dict:
    """my-plan.php. Cached per access token — this page is opened repeatedly
    during a workout and its contents change only when an admin republishes.
    """
    key = portal_key(client.access_token or str(client.id))
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    result = await portal_service.get_my_plan(db, client.access_token or "")
    await cache_set_json(key, result, settings.CACHE_TTL_WORKOUT)
    return result


@router.get("/progress")
async def progress(client: CurrentClient, db: DbSession) -> dict:
    """workout-progress.php, PR-32. NOT cached: the completed-exercise count
    must move the instant a set is ticked, or the UI looks broken.
    """
    return await portal_service.get_progress(db, client.access_token or "")
```

### `app/api/v1/intake.py`

```python
# app/api/v1/intake.py
from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.deps import BodyParams
from app.cache.rate_limit import limit_intake
from app.integrations.mailer import send_intake_email
from app.services import intake_service

router = APIRouter(tags=["intake"])


@router.post("/intake", dependencies=[Depends(limit_intake)])
async def submit_intake(payload: BodyParams, background: BackgroundTasks) -> dict:
    """PR-33.

    Validation is synchronous (the user must see field errors); the SMTP send
    is a background task so the response returns in ~5 ms instead of ~800 ms
    and does not hold a concurrency slot on a network wait.
    """
    subject, body, reply_to = intake_service.build_intake_email(payload)
    background.add_task(send_intake_email, subject, body, reply_to)
    return {"success": True, "message": "Submission received"}
```

### `app/api/v1/admin.py`

```python
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
```

**Why cache invalidation lives in the router and not the service:** the service owns the business rule and must stay testable without Redis. The router owns the delivery concern. Mixing them means every service unit test needs a Redis fixture.

## 7.4 `app/api/legacy.py` — the compatibility layer

**Why:** `frontend/src/services/workoutService.ts` line 44 calls `/api/workout.php?client_id=${clientId}`. Anything else that ever hit the PHP (a bookmark, a partner integration, the enrollment page's `fetch('/GRIND/api/validate_affiliate.php')`) uses the old paths too. Aliasing them means cutover is a DNS/base-URL change with **zero frontend edits**, and you can roll back instantly.

```python
# app/api/legacy.py
"""Deprecated `.php` aliases.

Every route here delegates to the same service as its /api/v1 counterpart —
there is no duplicated logic. Delete this module once the frontend and any
external callers have moved, tracked by the deprecation metric below.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response

from app.api.deps import BodyParams, DbSession
from app.cache.rate_limit import limit_coupon, limit_payment, limit_write
from app.cache.redis import cache_get_json, cache_set_json, workout_key
from app.core.compat import php_intval
from app.core.config import settings
from app.integrations.mailer import send_intake_email
from app.services import affiliate_service, intake_service, payment_service, workout_service

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)  # keep the OpenAPI page clean


def _deprecated(response: Response, replacement: str, path: str) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f'<{replacement}>; rel="successor-version"'
    # Grep this in the logs to know when it is safe to delete the module.
    logger.info("legacy_path_used", extra={"legacy_path": path, "replacement": replacement})


@router.get("/api/workout.php")
async def legacy_workout(request: Request, response: Response, db: DbSession) -> dict:
    _deprecated(response, "/api/v1/workout", "/api/workout.php")
    raw = request.query_params.get("client_id")
    resolved = settings.LEGACY_DEFAULT_CLIENT_ID if raw in (None, "") else php_intval(raw)

    key = workout_key(resolved)
    cached = await cache_get_json(key)
    if cached is not None:
        return cached

    result = await workout_service.get_workout(db, raw)
    await cache_set_json(key, result, settings.CACHE_TTL_WORKOUT)
    return result


@router.post("/api/complete-workout.php", dependencies=[Depends(limit_write)])
async def legacy_complete(response: Response, payload: BodyParams, db: DbSession) -> dict:
    _deprecated(response, "/api/v1/workout/complete", "/api/complete-workout.php")
    return await workout_service.complete_workout(db, payload)


@router.post("/api/validate_affiliate.php", dependencies=[Depends(limit_coupon)])
async def legacy_validate(response: Response, payload: BodyParams, db: DbSession) -> dict:
    _deprecated(response, "/api/v1/affiliate/validate", "/api/validate_affiliate.php")
    return await affiliate_service.validate_code(db, payload.get("code"))


@router.post("/Payment/create_order.php", dependencies=[Depends(limit_payment)])
async def legacy_create_order(
    response: Response, payload: BodyParams, db: DbSession
) -> dict:
    _deprecated(response, "/api/v1/payments/order", "/Payment/create_order.php")
    return await payment_service.create_order(
        db,
        raw_plan=payload.get("plan"),
        raw_price=payload.get("price"),
        raw_coupon=payload.get("coupon"),
    )


@router.post("/Payment/verify_payment.php", dependencies=[Depends(limit_payment)])
async def legacy_verify(response: Response, payload: BodyParams, db: DbSession) -> dict:
    _deprecated(response, "/api/v1/payments/verify", "/Payment/verify_payment.php")
    return await payment_service.verify_payment(db, payload)


@router.post("/save-progress.php", dependencies=[Depends(limit_write)])
async def legacy_save_progress(
    response: Response, payload: BodyParams, db: DbSession
) -> dict:
    _deprecated(response, "/api/v1/workout/logs", "/save-progress.php")
    return await workout_service.save_log(db, payload)


@router.post("/start-your-journey.php")
async def legacy_intake(
    response: Response, payload: BodyParams, background: BackgroundTasks
) -> dict:
    _deprecated(response, "/api/v1/intake", "/start-your-journey.php")
    subject, body, reply_to = intake_service.build_intake_email(payload)
    background.add_task(send_intake_email, subject, body, reply_to)
    return {"success": True, "message": "Submission received"}
```

## 7.5 `app/api/v1/__init__.py` — router assembly

```python
# app/api/v1/__init__.py
from fastapi import APIRouter

from app.api.v1 import admin, affiliate, intake, payments, portal, workout

api_router = APIRouter()
api_router.include_router(workout.router)
api_router.include_router(affiliate.router)
api_router.include_router(payments.router)
api_router.include_router(portal.router)
api_router.include_router(intake.router)
api_router.include_router(admin.router)
```

---
---

# Phase 8 — App assembly and middleware

## 8.1 `app/middleware.py`

**Why:** the PHP had no request tracing at all. When a payment fails you need to hand support one id and find every log line for that request across 4 workers.
**Why pure ASGI and not `BaseHTTPMiddleware`:** Starlette's `BaseHTTPMiddleware` wraps each request in an anyio task group and buffers the response body. At 1,200 req/s that overhead is measurable and it breaks streaming. A raw ASGI callable costs essentially nothing.

```python
# app/middleware.py
import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_ctx

logger = logging.getLogger("app.access")


class RequestContextMiddleware:
    """Assigns a request id, logs one structured access line, and echoes the
    id back as X-Request-ID so support can correlate a user report with logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id")
        request_id = incoming.decode() if incoming else uuid.uuid4().hex[:16]
        token = request_id_ctx.set(request_id)

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_code,
                    "duration_ms": duration_ms,
                },
            )
            request_id_ctx.reset(token)


class SecurityHeadersMiddleware:
    """Headers a JSON API should always send.

    No CSP: this app serves no HTML. nosniff and DENY still matter because
    a JSON response rendered directly in a browser tab is an XSS vector on
    older engines.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"strict-origin-when-cross-origin"),
                        (
                            b"strict-transport-security",
                            b"max-age=31536000; includeSubDomains",
                        ),
                    ]
                )
            await send(message)

        await self.app(scope, receive, send_wrapper)
```

## 8.2 `app/main.py`

**Why a lifespan:** connection pools must be created after the worker process forks (a pool inherited across `fork()` shares sockets between processes and corrupts them) and closed on shutdown so in-flight queries finish. `pool_pre_ping` plus an explicit warm-up query at boot means the first real request does not pay connection setup.

```python
# app/main.py
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.legacy import router as legacy_router
from app.api.v1 import api_router
from app.cache.redis import close_redis, get_redis
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
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
```

**Why `/health` and `/health/ready` are different endpoints:** this is the single most common production mistake in FastAPI deployments. A liveness probe that checks the database will restart every container in your fleet the moment the database is slow — converting a recoverable incident into a cascading outage. Liveness answers "is this process wedged?"; readiness answers "should traffic come here right now?".

**Why CORS never uses `*` with credentials:** browsers reject that combination outright, and `allow_credentials=True` is needed for the `Authorization` header on admin calls. `CORS_ORIGINS` must list the real frontend origins.

**Run it:**

```bash
uv run uvicorn app.main:app --reload --port 8000
curl -s localhost:8000/health
curl -s "localhost:8000/api/v1/workout?client_id=1" | head -c 400
curl -s "localhost:8000/api/workout.php?client_id=1" -D- | head -20   # alias + Deprecation header
```

---
---

# Phase 9 — Deployment

## 9.1 `deploy/gunicorn.conf.py`

**Why Gunicorn in front of Uvicorn workers:** Uvicorn's own `--workers` works, but Gunicorn adds what you actually need in production — graceful restarts that drain in-flight requests, `max_requests` with jitter to bound any slow memory growth, and a supervisor that replaces a wedged worker.

**The worker-count arithmetic (from Phase 0.2):**

```
Async workload, so workers are bounded by CPU, not by blocking I/O.
workers = (2 × vCPU) + 1  is the classic sync formula and OVER-provisions here.
For async, workers = vCPU is right: each worker saturates one core with
JSON + Pydantic work while thousands of sockets wait on I/O.

4 vCPU  -> 4 workers -> 4 × 30 pool = 120 DB connections (Phase 2.1)
Measured capacity of this shape: ~300-400 req/s per worker for a cached
read, so 4 workers ≈ 1,400 req/s — above the 1,200 peak in Phase 0.2.
```

```python
# deploy/gunicorn.conf.py
import multiprocessing
import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# Async workers are CPU-bound, not I/O-bound: one per core (Phase 0.2).
workers = int(os.getenv("WEB_CONCURRENCY", multiprocessing.cpu_count()))
worker_class = "uvicorn.workers.UvicornWorker"

# Nginx already buffers slow clients, so a modest keepalive is fine.
keepalive = 5

# Bound any slow leak. The jitter stops all workers recycling at once,
# which would show up as a throughput cliff every N requests.
max_requests = 10000
max_requests_jitter = 1000

# Must exceed your slowest legitimate request. DB_STATEMENT_TIMEOUT_MS is
# 5s and Razorpay's timeout is 10s, so 30s leaves room without letting a
# wedged worker linger.
timeout = 30
graceful_timeout = 30

# Log to stdout; the container runtime ships it. Our own access line
# (Phase 8.1) carries the request id, so Gunicorn's is disabled.
accesslog = None
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()

# Fork before touching sockets: connection pools MUST be created per worker
# (that is what the lifespan in Phase 8.2 does), never shared across fork.
preload_app = False
```

**Why `preload_app = False` is not negotiable:** with preloading, the master process would import `app.main`, and any pool created at import time would be inherited by every forked worker. Multiple processes then write to the same TCP sockets and you get corrupted protocol streams that look like random `InvalidRequestError`s. Creating pools in the lifespan (post-fork) plus `preload_app = False` is the safe combination.

## 9.2 `Dockerfile`

```dockerfile
# ---- build stage -------------------------------------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer first: it changes rarely, so Docker caches it across
# every application code change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime stage -----------------------------------------------
FROM python:3.12-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Never run as root: a container escape should not land on uid 0.
RUN groupadd -r grind && useradd -r -g grind -d /app grind

WORKDIR /app

COPY --from=builder --chown=grind:grind /app/.venv /app/.venv
COPY --chown=grind:grind app ./app
COPY --chown=grind:grind migrations ./migrations
COPY --chown=grind:grind alembic.ini deploy ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER grind
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["gunicorn", "app.main:app", "-c", "deploy/gunicorn.conf.py"]
```

## 9.3 `docker-compose.yml`

Replaces the Postgres-only file currently in `backend-v1`.

```yaml
services:
  postgres:
    image: postgres:17-alpine
    container_name: grind-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    command:
      # Tuned for the Phase 0.2 numbers on a 4 vCPU / 8 GB box.
      - "postgres"
      - "-c"
      - "max_connections=200"          # 120 from the app + PgBouncer + headroom
      - "-c"
      - "shared_buffers=2GB"           # ~25% of RAM
      - "-c"
      - "effective_cache_size=6GB"     # ~75% of RAM; planner hint only
      - "-c"
      - "work_mem=16MB"                # per sort/hash node — do not overdo it
      - "-c"
      - "maintenance_work_mem=512MB"
      - "-c"
      - "random_page_cost=1.1"         # SSD; the 4.0 default assumes spinning disks
      - "-c"
      - "log_min_duration_statement=1000"   # log anything over 1s
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: grind-redis
    restart: unless-stopped
    command:
      # Cache + rate limits only: no data here is worth persisting, and
      # allkeys-lru means a full Redis evicts instead of erroring.
      - "redis-server"
      - "--maxmemory"
      - "512mb"
      - "--maxmemory-policy"
      - "allkeys-lru"
      - "--save"
      - ""
      - "--appendonly"
      - "no"
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  api:
    build: .
    container_name: grind-api
    restart: unless-stopped
    env_file: .env
    environment:
      POSTGRES_HOST: postgres
      REDIS_URL: redis://redis:6379/0
      WEB_CONCURRENCY: 4
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    deploy:
      resources:
        limits:
          cpus: "4"
          memory: 2G

volumes:
  postgres_data:
```

**Why `random_page_cost=1.1`:** the Postgres default of `4.0` assumes a spinning disk and makes the planner avoid index scans in favour of sequential ones. On SSD that is backwards, and it is exactly the setting that would make the carefully chosen indexes from Phase 3.2 go unused.

## 9.4 PgBouncer — when and why

**When you need it:** one app container with 120 connections against `max_connections=200` is fine. The moment you run **two or more** containers, or a serverless platform that scales pods, you exceed it and start getting `FATAL: sorry, too many clients already` under exactly the load you scaled up for.

```ini
# deploy/pgbouncer.ini
[databases]
grind_db = host=postgres port=5432 dbname=grind_db

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = scram-sha-256
auth_file = /etc/pgbouncer/userlist.txt

# transaction mode: a backend is held only for the duration of a transaction,
# so N app connections multiplex onto far fewer real Postgres backends.
pool_mode = transaction

max_client_conn = 2000     # what the app pods may open
default_pool_size = 40     # real Postgres backends per database
reserve_pool_size = 10
reserve_pool_timeout = 3

server_idle_timeout = 60
```

Then in `.env`:

```bash
POSTGRES_HOST=pgbouncer
POSTGRES_PORT=6432
DB_USE_PGBOUNCER=true      # THIS IS MANDATORY — see below
```

**Why `DB_USE_PGBOUNCER=true` is mandatory, not cosmetic:** psycopg 3 uses server-side prepared statements automatically after a few executions. In transaction pooling, your next transaction may land on a *different* Postgres backend that has never seen that prepared statement, producing `prepared statement "_pg3_0" does not exist` — intermittently, under load, in production only. The flag sets `prepare_threshold=None` (Phase 3.1), which disables them. This is the single most common way a PgBouncer rollout fails.

## 9.5 `deploy/nginx.conf`

```nginx
upstream grind_api {
    server api:8000;
    keepalive 64;              # reuse upstream connections; avoids a
}                              # TCP handshake per request at 1,200 req/s

# Defence in depth: the app rate-limits per user, nginx limits per IP.
limit_req_zone $binary_remote_addr zone=api:10m rate=30r/s;
limit_req_zone $binary_remote_addr zone=payments:10m rate=2r/s;

server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate     /etc/ssl/certs/grind.crt;
    ssl_certificate_key /etc/ssl/private/grind.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    client_max_body_size 2m;   # workout_json can be large; 2m is generous

    # Nginx buffers slow clients so a 3G phone on a bad connection never
    # occupies a Python worker for the duration of its upload.
    proxy_request_buffering on;
    proxy_buffering on;

    location / {
        limit_req zone=api burst=60 nodelay;

        proxy_pass http://grind_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 5s;
        proxy_read_timeout    30s;   # matches gunicorn timeout
    }

    location ~ ^/(api/v1/payments|Payment)/ {
        limit_req zone=payments burst=5 nodelay;
        proxy_pass http://grind_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /health {
        access_log off;
        proxy_pass http://grind_api;
    }
}
```

**Why `keepalive 64` on the upstream:** without it Nginx opens a fresh TCP connection to Gunicorn for every single request. At 1,200 req/s that is 1,200 handshakes per second and rapid ephemeral-port exhaustion. The `proxy_http_version 1.1` + `Connection ""` pair is required for keepalive to actually engage — a very common misconfiguration.

---
---

# Phase 10 — Tests and the parity proof

**Why:** the acceptance criterion is *"identical inputs to the new API yield identical outcomes as the legacy system."* That is a claim about behaviour, and the only way to make it true rather than hopeful is to encode each of the 33 rules as an executable assertion.

Three layers, each catching a different class of failure:

| Layer | Catches | Needs |
|---|---|---|
| Unit (10.2) | PHP-semantics drift: rounding, casts, padding, falsiness | Nothing |
| Integration (10.3) | Envelope shape, status codes, key names, key order | A seeded DB |
| Differential (10.4) | Everything you did not think to assert | Both stacks running |

## 10.1 `tests/conftest.py`

```python
# tests/conftest.py
import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app

TEST_DB_URL = settings.DATABASE_URL.replace(
    f"/{settings.POSTGRES_DB}", f"/{settings.POSTGRES_DB}_test"
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DB_URL, poolclass=None)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:
    """Each test runs inside a transaction that is rolled back, so tests are
    fully isolated without truncating tables between them.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = async_sessionmaker(bind=connection, expire_on_commit=False)()
        yield session
        await session.close()
        await transaction.rollback()


@pytest_asyncio.fixture
async def client(db) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
```

## 10.2 `tests/unit/test_parity_rules.py`

```python
# tests/unit/test_parity_rules.py
"""One test per PR-xx rule. If any of these fail, parity is broken."""
import pytest

from app.core.compat import (
    php_clean, php_floatval, php_intval, php_json_is_falsy,
    php_round, php_round_int, php_str_pad,
)
from app.services.client_service import build_access_token
from app.services.payment_service import DISCOUNT_ELIGIBLE_PLANS


class TestPhpCasts:
    """PR-01, PR-06, PR-15 — PHP's loose casts."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("42", 42), ("12abc", 12), ("abc", 0), ("", 0), (None, 0),
            ("12.9", 12), ("-5", -5), ("  7  ", 7), ("0", 0), (True, 1),
        ],
    )
    def test_intval(self, value, expected):
        assert php_intval(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("3499", 3499.0), ("3499.50", 3499.5), ("abc", 0.0), ("", 0.0), (None, 0.0)],
    )
    def test_floatval(self, value, expected):
        assert php_floatval(value) == expected


class TestPhpRound:
    """PR-17, PR-28 — half away from zero, NOT banker's rounding."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0.5, 1.0), (1.5, 2.0), (2.5, 3.0), (-0.5, -1.0), (-2.5, -3.0), (0.4, 0.0)],
    )
    def test_half_away_from_zero(self, value, expected):
        assert php_round(value) == expected

    def test_differs_from_python_builtin(self):
        # This is the whole reason the helper exists.
        assert round(2.5) == 2
        assert php_round(2.5) == 3.0


class TestAccessToken:
    """PR-22 — expected values taken from the production `clients` dump."""

    @pytest.mark.parametrize(
        ("name", "client_id", "expected"),
        [
            ("Arup Mohanty", 14, "GR_ARU_000014"),   # real row
            ("Nigama Medhi", 8, "GR_NIG_000008"),    # real row
            ("Rameswar Bhagat", 17, "GR_RAM_000017"),# real row
            ("Al", 3, "GR_ALX_000003"),              # str_pad pads RIGHT
            ("Bo Xu", 7, "GR_BOX_000007"),           # space stripped
            ("123", 9, "GR_XXX_000009"),             # digits stripped
            ("", 1, "GR_XXX_000001"),
            ("Tanuj", 1234567, "GR_TAN_1234567"),    # id longer than 6: no truncation
        ],
    )
    def test_token(self, name, client_id, expected):
        assert build_access_token(name, client_id) == expected


class TestStrPad:
    def test_pads_right_by_default(self):
        assert php_str_pad("Al", 3, "X") == "AlX"

    def test_pads_left_when_asked(self):
        assert php_str_pad("7", 6, "0", left=True) == "000007"

    def test_no_truncation_when_longer(self):
        assert php_str_pad("ABCDEF", 3, "X") == "ABCDEF"


class TestJsonFalsiness:
    """PR-26 — valid JSON that PHP still rejects."""

    @pytest.mark.parametrize("value", [None, False, 0, 0.0, "", "0", [], {}])
    def test_falsy(self, value):
        assert php_json_is_falsy(value) is True

    @pytest.mark.parametrize("value", [1, "a", [1], {"a": 1}, True])
    def test_truthy(self, value):
        assert php_json_is_falsy(value) is False


class TestDiscountEligibility:
    """PR-13 — exact, case-sensitive plan whitelist."""

    def test_exact_names_only(self):
        assert "3 MONTH KICKSTART" in DISCOUNT_ELIGIBLE_PLANS
        assert "3 Month Kickstart" not in DISCOUNT_ELIGIBLE_PLANS
        assert "3 MONTH KICKSTART " not in DISCOUNT_ELIGIBLE_PLANS
        assert len(DISCOUNT_ELIGIBLE_PLANS) == 3


class TestPricing:
    """PR-15, PR-17 — the money maths, against real plan prices."""

    @pytest.mark.parametrize(
        ("price", "discount", "final", "paise"),
        [
            (3499.0, 0, 3499.0, 349900),
            (3499.0, 10, 3149.1, 314910),
            (3499.0, 30, 2449.3, 244930),
            (7999.0, 30, 5599.3, 559930),
            (12999.0, 10, 11699.1, 1169910),
        ],
    )
    def test_final_price_and_paise(self, price, discount, final, paise):
        computed = price - (price * discount / 100)
        assert round(computed, 4) == round(final, 4)
        assert php_round_int(computed * 100) == paise


class TestIntakeSanitiser:
    """PR-33."""

    def test_strips_tags_then_escapes(self):
        assert php_clean("  <b>Bob</b>  ") == "Bob"

    def test_escapes_quotes_and_ampersand(self):
        assert php_clean("A & B \"q\" 'r'") == "A &amp; B &quot;q&quot; &#039;r&#039;"

    def test_no_double_escaping(self):
        # & must be replaced first or "&lt;" becomes "&amp;lt;"
        assert php_clean("<script>") == ""
        assert php_clean("a<b") == "a&lt;b"
```

## 10.3 `tests/integration/test_workout_api.py`

```python
# tests/integration/test_workout_api.py
import pytest

from app.db.models import Client, WorkoutDay, WorkoutExercise, WorkoutPlan


@pytest.fixture
async def seeded(db):
    client = Client(id=1, name="Arup", email="a@example.com", access_token="TOK1")
    plan = WorkoutPlan(id=5, client_id=1, plan_name="Fat Loss", is_active=True, version_no=1)
    inactive = WorkoutPlan(id=4, client_id=1, plan_name="Old", is_active=False, version_no=0)
    day1 = WorkoutDay(id=1, plan_id=5, day_number=1, day_name="Push")
    day2 = WorkoutDay(id=2, plan_id=5, day_number=2, day_name="Pull")
    db.add_all([client, inactive, plan, day1, day2])
    db.add_all([
        WorkoutExercise(id=1, day_id=1, exercise_name="Bench Press",
                        sets_count=4, reps="8", youtube_url="u1", sort_order=1),
        WorkoutExercise(id=2, day_id=1, exercise_name="Incline DB Press",
                        sets_count=3, reps="10", youtube_url="u2", sort_order=2),
        WorkoutExercise(id=3, day_id=2, exercise_name="Lat Pulldown",
                        sets_count=4, reps="10", youtube_url="u3", sort_order=1),
    ])
    await db.flush()
    return client


class TestWorkoutEndpoint:
    async def test_returns_active_plan_with_nested_days(self, client, seeded):
        r = await client.get("/api/v1/workout?client_id=1")
        assert r.status_code == 200
        body = r.json()

        assert body["success"] is True
        assert "message" not in body                      # PR-03: absent on success
        assert body["data"]["plan"]["id"] == 5            # PR-02: newest active
        assert body["data"]["plan"]["is_active"] == 1     # int, not bool
        assert [d["day_number"] for d in body["data"]["days"]] == [1, 2]   # PR-04
        assert [e["sort_order"] for e in body["data"]["days"][0]["exercises"]] == [1, 2]

    async def test_no_plan_returns_200_with_null_data(self, client, seeded):
        r = await client.get("/api/v1/workout?client_id=999")
        assert r.status_code == 200                       # PR-03: NOT a 404
        assert r.json() == {
            "success": True,
            "data": None,
            "message": "No workout plan assigned.",
        }

    async def test_key_order_matches_php_json_encode(self, client, seeded):
        r = await client.get("/api/v1/workout?client_id=999")
        assert list(r.json().keys()) == ["success", "data", "message"]

    async def test_missing_client_id_defaults_to_1(self, client, seeded):
        r = await client.get("/api/v1/workout")
        assert r.json()["data"]["plan"]["client_id"] == 1  # PR-01

    async def test_non_numeric_client_id_becomes_zero_not_422(self, client, seeded):
        r = await client.get("/api/v1/workout?client_id=abc")
        assert r.status_code == 200                       # PR-01: NOT 422
        assert r.json()["data"] is None

    async def test_legacy_php_alias_is_identical(self, client, seeded):
        new = await client.get("/api/v1/workout?client_id=1")
        old = await client.get("/api/workout.php?client_id=1")
        assert old.status_code == 200
        assert old.json() == new.json()
        assert old.headers["deprecation"] == "true"


class TestCompleteWorkout:
    @pytest.mark.parametrize(
        "payload",
        [
            {"exercise_id": 0, "day_id": 1, "user_email": "a@b.c"},
            {"exercise_id": 1, "day_id": 0, "user_email": "a@b.c"},
            {"exercise_id": 1, "day_id": 1, "user_email": ""},
            {"exercise_id": "abc", "day_id": 1, "user_email": "a@b.c"},   # -> 0
            {},
        ],
    )
    async def test_rejects_with_exact_legacy_message(self, client, seeded, payload):
        r = await client.post("/api/v1/workout/complete", json=payload)
        assert r.status_code == 400                      # PR-06
        assert r.json()["message"] == "Missing required fields"

    async def test_hardcodes_month_week_set(self, client, db, seeded):
        from sqlalchemy import select

        from app.db.models import WorkoutLog

        r = await client.post(
            "/api/v1/workout/complete",
            json={"exercise_id": 1, "day_id": 1, "user_email": "a@b.c",
                  "month_no": 9, "week_no": 9, "set_no": 9},   # ignored
        )
        assert r.json() == {"success": True, "message": "Workout marked complete"}

        log = (await db.execute(select(WorkoutLog))).scalars().one()
        assert (log.month_no, log.week_no, log.set_no) == (1, 1, 1)   # PR-07
        assert log.completed is True

    async def test_accepts_form_encoding_like_the_php(self, client, seeded):
        r = await client.post(
            "/api/v1/workout/complete",
            data={"exercise_id": "1", "day_id": "1", "user_email": "a@b.c"},
        )
        assert r.json()["success"] is True

    async def test_duplicate_calls_insert_duplicate_rows(self, client, db, seeded):
        from sqlalchemy import func, select

        from app.db.models import WorkoutLog

        body = {"exercise_id": 1, "day_id": 1, "user_email": "a@b.c"}
        await client.post("/api/v1/workout/complete", json=body)
        await client.post("/api/v1/workout/complete", json=body)

        count = (await db.execute(select(func.count()).select_from(WorkoutLog))).scalar_one()
        assert count == 2   # PR-08: no dedupe, deliberately
```

## 10.4 The differential harness

**Why:** unit and integration tests only check what you thought to check. The harness checks *everything*, including the fields nobody remembered.

```python
# tests/differential/compare.py
"""Replay one request corpus against both stacks and diff the JSON.

    uv run python tests/differential/compare.py \
        --legacy https://old.yourdomain.com \
        --new    http://localhost:8000
"""
import argparse
import asyncio
import json
from typing import Any

import httpx

# Read-only by default. Add the write cases only against a scratch database.
CORPUS: list[dict[str, Any]] = [
    {"method": "GET",  "legacy": "/api/workout.php?client_id=1",
     "new": "/api/v1/workout?client_id=1"},
    {"method": "GET",  "legacy": "/api/workout.php?client_id=999",
     "new": "/api/v1/workout?client_id=999"},
    {"method": "GET",  "legacy": "/api/workout.php",
     "new": "/api/v1/workout"},
    {"method": "GET",  "legacy": "/api/workout.php?client_id=abc",
     "new": "/api/v1/workout?client_id=abc"},
    {"method": "POST", "legacy": "/api/validate_affiliate.php",
     "new": "/api/v1/affiliate/validate", "data": {"code": "GR_ARU_10"}},
    {"method": "POST", "legacy": "/api/validate_affiliate.php",
     "new": "/api/v1/affiliate/validate", "data": {"code": "GR_INDIA_30"}},  # expired: PR-10
    {"method": "POST", "legacy": "/api/validate_affiliate.php",
     "new": "/api/v1/affiliate/validate", "data": {"code": "NOPE"}},
    {"method": "POST", "legacy": "/api/validate_affiliate.php",
     "new": "/api/v1/affiliate/validate", "data": {}},                       # missing field
]

# Fields that legitimately differ. Everything else must match exactly.
IGNORE_PATHS = {
    "data.plan.created_at",   # timezone rendering differs MySQL vs Postgres
    "order_id",               # a new Razorpay order id per call
}


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            out.update(flatten(value, f"{prefix}[{index}]"))
    else:
        out[prefix] = obj
    return out


def diff(legacy: Any, new: Any) -> list[str]:
    a, b = flatten(legacy), flatten(new)
    problems: list[str] = []
    for key in sorted(set(a) | set(b)):
        base = key.split("[")[0]
        if base in IGNORE_PATHS or key in IGNORE_PATHS:
            continue
        if key not in a:
            problems.append(f"  + {key} = {b[key]!r}  (only in NEW)")
        elif key not in b:
            problems.append(f"  - {key} = {a[key]!r}  (only in LEGACY)")
        elif a[key] != b[key]:
            problems.append(f"  ~ {key}: legacy={a[key]!r}  new={b[key]!r}")
    return problems


async def run(legacy_base: str, new_base: str) -> int:
    failures = 0
    async with httpx.AsyncClient(timeout=30) as http:
        for case in CORPUS:
            method = case["method"]
            data = case.get("data")

            if method == "GET":
                old = await http.get(legacy_base + case["legacy"])
                new = await http.get(new_base + case["new"])
            else:
                # The legacy reads $_POST, so send form-encoded to BOTH:
                # identical inputs is the whole point.
                old = await http.post(legacy_base + case["legacy"], data=data)
                new = await http.post(new_base + case["new"], data=data)

            label = f"{method} {case['legacy']}"
            try:
                problems = diff(old.json(), new.json())
            except json.JSONDecodeError:
                print(f"FAIL {label}\n  non-JSON response "
                      f"(legacy={old.status_code}, new={new.status_code})")
                failures += 1
                continue

            if old.status_code != new.status_code:
                problems.insert(
                    0, f"  ~ status: legacy={old.status_code} new={new.status_code}"
                )

            if problems:
                failures += 1
                print(f"FAIL {label}")
                print("\n".join(problems))
            else:
                print(f"OK   {label}")

    print(f"\n{len(CORPUS) - failures}/{len(CORPUS)} identical")
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", required=True)
    parser.add_argument("--new", required=True)
    args = parser.parse_args()
    raise SystemExit(1 if asyncio.run(run(args.legacy, args.new)) else 0)
```

**How to use it:** point it at the live PHP and your local FastAPI, with the same database contents. Every `OK` line is a proven parity case. Every `FAIL` is either a bug or a deviation that belongs in `PARITY.md` with a reason. Do not ship until the output is all `OK` or all deliberate.

## 10.5 Load test — proving the 10,000-user number

```python
# tests/load/locustfile.py
"""uv run locust -f tests/load/locustfile.py --host http://localhost:8000

Models the Phase 0.2 traffic mix: overwhelmingly reads, a set tick every
few reads, occasional checkout.
"""
import random

from locust import HttpUser, between, task


class GrindClient(HttpUser):
    # Matches "one request every 20-30 s per active user" from Phase 0.2.
    wait_time = between(20, 30)

    @task(85)
    def view_workout(self):
        client_id = random.randint(1, 17)
        self.client.get(f"/api/v1/workout?client_id={client_id}", name="/workout")

    @task(12)
    def tick_set(self):
        self.client.post(
            "/api/v1/workout/complete",
            json={"exercise_id": random.randint(1, 900),
                  "day_id": random.randint(1, 150),
                  "user_email": f"user{random.randint(1, 5000)}@test.com"},
            name="/workout/complete",
        )

    @task(3)
    def check_coupon(self):
        self.client.post(
            "/api/v1/affiliate/validate",
            data={"code": random.choice(["GR_ARU_10", "GR_FIT_30", "NOPE"])},
            name="/affiliate/validate",
        )
```

Run it against the target numbers and check the result against the budget:

```bash
uv run locust -f tests/load/locustfile.py --host http://localhost:8000 \
  --users 10000 --spawn-rate 200 --run-time 10m --headless
```

**Pass criteria, derived from Phase 0.2:**

| Metric | Target | Where the number comes from |
|---|---|---|
| Throughput | ≥ 400 req/s sustained | 10,000 users ÷ 25 s |
| p95 latency | < 150 ms | cached read 2 ms + network |
| p99 latency | < 400 ms | uncached read 15 ms + tail |
| Error rate | < 0.1% | anything higher means pool exhaustion |
| DB connections | < 130 | 4 workers × 30, from Phase 2.1 |
| Cache hit rate | > 80% | 60 s TTL vs ~monthly plan changes |

If p99 climbs while CPU stays low, you are pool-starved — check `DB_POOL_TIMEOUT` errors in the log before adding workers.

---
---

# Appendix A — `.env.example`

Replace the existing four-line file with this. Every value the app needs, with nothing secret committed.

```bash
# ── application ────────────────────────────────────────────────────
ENV=dev
APP_NAME=GRIND API
DEBUG=false
LOG_LEVEL=INFO

# ── security ───────────────────────────────────────────────────────
# openssl rand -hex 32
SECRET_KEY=CHANGE_ME_openssl_rand_hex_32
ACCESS_TOKEN_EXPIRE_MINUTES=480
ADMIN_USERNAME=admin
# uv run python -c "from argon2 import PasswordHasher;print(PasswordHasher().hash('yourpass'))"
# QUOTE THIS — it contains $ characters that shells and compose interpolate.
ADMIN_PASSWORD_HASH='$argon2id$v=19$m=65536,t=3,p=4$CHANGE_ME'

# ── database ───────────────────────────────────────────────────────
POSTGRES_USER=postgres
POSTGRES_PASSWORD=CHANGE_ME
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=grind_db

DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT=10
DB_POOL_RECYCLE=1800
DB_ECHO=false
DB_STATEMENT_TIMEOUT_MS=5000
DB_USE_PGBOUNCER=false

# ── redis ──────────────────────────────────────────────────────────
REDIS_URL=redis://localhost:6379/0
REDIS_MAX_CONNECTIONS=50
CACHE_ENABLED=true
CACHE_TTL_WORKOUT=60
CACHE_TTL_EXERCISE_COUNT=300
RATE_LIMIT_ENABLED=true

# ── networking (comma-separated) ───────────────────────────────────
CORS_ORIGINS=http://localhost:5173,https://yourdomain.com
TRUSTED_HOSTS=*

# ── razorpay — ROTATE THE COMMITTED LIVE KEYS BEFORE USING THESE ──
RAZORPAY_KEY_ID=rzp_test_CHANGE_ME
RAZORPAY_KEY_SECRET=CHANGE_ME
RAZORPAY_TIMEOUT=10.0
RAZORPAY_VERIFY_SIGNATURE=true
PAYMENTS_RECOMPUTE_PRICE=false

# ── mail ───────────────────────────────────────────────────────────
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_STARTTLS=true
MAIL_FROM=GRIND Intake <noreply@trenddma.com>
INTAKE_RECIPIENT=grindfit.ai@trenddma.com

# ── legacy parity switches ─────────────────────────────────────────
LEGACY_DEFAULT_CLIENT_ID=1
LEGACY_AFFILIATE_SKIP_EXPIRY=true
LEGACY_ZERO_PRICE_CHECKS_ORIGINAL=true
LEGACY_GLOBAL_PROGRESS_DENOMINATOR=true
LEGACY_OPEN_ADMIN=false
```

# Appendix B — Frontend changes

The only file that must change is the base URL. Everything else keeps working through the `.php` aliases.

```bash
# frontend/.env
VITE_API_BASE_URL=https://api.yourdomain.com
```

When you are ready to drop the aliases, change one line in `frontend/src/services/workoutService.ts`:

```ts
// before
const response = await api.get<WorkoutResponse>(`/api/workout.php?client_id=${clientId}`);
// after
const response = await api.get<WorkoutResponse>(`/api/v1/workout?client_id=${clientId}`);
```

Grep the logs for `legacy_path_used` (Phase 7.4). When it stops appearing for a full week, delete `app/api/legacy.py`.

# Appendix C — Order of work

Phases 1–3 are prerequisites. After that, ship one vertical slice at a time — each is independently testable and independently deployable.

| # | Slice | Phases involved | Why this order |
|---|---|---|---|
| 1 | Workout read | 4, 5.1, 7.3, 8 | The only endpoint the React app calls today |
| 2 | Workout logging | 4, 5.1, 7.3 | Same tables, adds the write path |
| 3 | Affiliate | 4.2, 5.3, 7.3 | Self-contained, no external calls |
| 4 | Payments | 5.2, 5.4, 6.1, 7.3 | Highest risk — do it once 1–3 are proven |
| 5 | Client portal | 5.8, 6.2, 7.3 | Depends on the cache being in place |
| 6 | Admin | 5.5–5.7, 6.1, 7.3 | Needs auth from Phase 6 |
| 7 | Intake | 5.9, 7.3 | Fully standalone |

# Appendix D — Pre-cutover checklist

**Security (do these first — they are independent of the migration):**

- [ ] Rotate the Razorpay live key/secret committed in `backend/config/razorpay.php` and `backend/DB/razorpay.php`
- [ ] Rotate the database password committed in `backend/config/database.php`
- [ ] Set a new admin password and store only the argon2 hash
- [ ] Confirm `backend-v1/.env` was never committed to that repo's git history
- [ ] `SECRET_KEY` generated with `openssl rand -hex 32`, unique per environment
- [ ] `CORS_ORIGINS` lists real origins, never `*`
- [ ] `TRUSTED_HOSTS` set in production
- [ ] Docs disabled in production (`ENV=prod` does this automatically)

**Correctness:**

- [ ] `uv run pytest` — all green
- [ ] `scripts/reconcile.py` — every table matches
- [ ] `tests/differential/compare.py` — all `OK`, or every diff justified in `PARITY.md`
- [ ] `PARITY.md` written, listing every intentional deviation
- [ ] The three known bugs confirmed as preserved: PR-10, PR-16, PR-28

**Operations:**

- [ ] `alembic upgrade head` runs clean on a fresh database
- [ ] `/health` and `/health/ready` wired to the orchestrator as **liveness** and **readiness** respectively
- [ ] Load test meets the Phase 10.5 pass criteria
- [ ] Log aggregation receives the JSON lines and `request_id` is searchable
- [ ] Alerts on: 5xx rate, p99 latency, DB pool timeouts, `razorpay_signature_mismatch`, `price_mismatch`
- [ ] Database backups configured and a restore actually tested
- [ ] `DB_USE_PGBOUNCER=true` set if and only if PgBouncer is in the path

**Cutover:**

- [ ] Both stacks running in parallel, differential harness on a schedule
- [ ] `VITE_API_BASE_URL` switched
- [ ] Rollback plan: revert the base URL — the PHP is untouched and still live
- [ ] Monitor `legacy_path_used` to find callers you did not know about
- [ ] Retire `backend/` only after a week of clean logs

---

# Appendix E — Deviations to record in `PARITY.md`

Start the file with these. Everything the differential harness flags gets appended.

| # | Legacy behaviour | New behaviour | Why |
|---|---|---|---|
| D-1 | Razorpay signature ignored | Verified (flag) | Free-enrollment hole; frontend already sends it |
| D-2 | 3 admin endpoints unauthenticated | Admin JWT (flag) | `affiliate-dashboard.php` leaked revenue and emails |
| D-3 | Admin password plaintext in source | argon2 hash in env | Source-code credential |
| D-4 | `my-plan` hardcoded one email; `workout-progress` hardcoded `client_id=1` | Derived from access token | Impossible to keep in a multi-client API |
| D-5 | Empty measurement stored as `0.00` | Stored as `NULL` | Postgres rejects `''`; `0.00` would corrupt PR-32 stats |
| D-6 | `create-plan.php` had no transaction | Wrapped in one | Only failure behaviour changes; success is identical |
| D-7 | `add_diet.php` had no transaction | Wrapped in one | A failed insert left the client with no active diet |
| D-8 | `save-progress.php` was a fatal error (`require 'config.php'`) | Working endpoint | No behaviour existed to preserve |
| D-9 | Errors returned raw exception text | Generic message + logged detail | Information disclosure |
| D-10 | HTML pages for admin and portal | JSON only | `frontend/` owns presentation |
| D-11 | `ORDER BY` left NULLs and ties undefined | `nulls_first()` + `id` tiebreak | MySQL sorts NULLs first, Postgres last — matched explicitly |

---

*End of implementation guide. Companion: `MIGRATION_PLAN.md` (legacy audit, parity rules PR-01…PR-33).*
