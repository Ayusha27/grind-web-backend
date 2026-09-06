import os
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import quote_plus
import os
from dotenv import load_dotenv
from pydantic import Field, computed_field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# The database credentials, and only these, are pinned to .env — see
# Settings.settings_customise_sources below for why that needs enforcing.
DB_CREDENTIAL_FIELDS = frozenset({
    "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME",
})

load_dotenv()
class _FilteredSource(PydanticBaseSettingsSource):
    """Wraps a settings source, keeping or dropping a fixed set of field names.

    Used in pairs: keep=True over the .env source and keep=False over the
    process-environment source, which together make .env the sole origin of
    a field while leaving every other setting's precedence untouched.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        inner: PydanticBaseSettingsSource,
        fields: frozenset[str],
        *,
        keep: bool,
    ) -> None:
        super().__init__(settings_cls)
        self._inner = inner
        self._fields = fields
        self._keep = keep

    def get_field_value(self, field: Any, field_name: str) -> Any:
        # Required by the ABC, but never called: __call__ is overridden and
        # delegates whole-dict extraction to the wrapped source.
        raise NotImplementedError

    def __call__(self) -> dict[str, Any]:
        # case_sensitive=False means the wrapped source lowercases its keys.
        return {
            key: value
            for key, value in self._inner().items()
            if (key.upper() in self._fields) is self._keep
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file = ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # application
    ENV: Literal["dev", "staging", "prod"] = "dev"
    APP_NAME: str = "GRIND API"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Security
    SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    ADMIN_USERNAME: str
    ADMIN_PASSWORD_HASH: str

    # Database — BigRock MySQL.
    # Read from .env and nowhere else (settings_customise_sources). No
    # os.getenv() defaults either: os.getenv() returning None as the "default"
    # for a str field turns a missing variable into a confusing validation
    # error instead of a plain "field required".
    DB_HOST: str = os.getenv("DB_HOST")
    DB_PORT: int = os.getenv("DB_PORT")
    DB_USER: str = os.getenv("DB_USER")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD")
    DB_NAME: str = os.getenv("DB_NAME")

    # Socket-level connect timeout, handed to PyMySQL via aiomysql. Bounds how
    # long a pool checkout can block when BigRock is unreachable, which matters
    # far more now that the server is remote rather than a local container.
    DB_CONNECT_TIMEOUT: int = 10
    DB_CHARSET: str = "utf8mb4"           # utf8mb4 is the only sane MySQL charset

    # Pool maths justified in Phase 0.2 step 3:
    #   4 workers x (20 + 10) = 120 connections.
    # NOTE: that ceiling was sized against a dedicated Postgres with
    # max_connections=200. Shared BigRock MySQL plans commonly cap
    # max_user_connections far lower — verify with
    #   SHOW VARIABLES LIKE 'max_user_connections';
    # and lower DB_POOL_SIZE/DB_MAX_OVERFLOW in .env if the cap is below ~120.
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 10             # seconds to wait for a free connection
    DB_POOL_RECYCLE: int = 1800           # must stay under MySQL wait_timeout
    DB_ECHO: bool = False
    DB_STATEMENT_TIMEOUT_MS: int = 5000   # no single query may hog a pool slot
    # MySQL spells the statement ceiling max_execution_time (ms); MariaDB spells
    # it max_statement_time (seconds). Shared hosting is often MariaDB, and the
    # wrong name makes every connection fail in init_command, so it is explicit.
    DB_SERVER_FLAVOR: Literal["mysql", "mariadb"] = "mysql"

    #  redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MAX_CONNECTIONS: int = 50
    CACHE_ENABLED: bool = True
    CACHE_TTL_WORKOUT: int = 60           # plans change ~monthly; 60s is very safe
    CACHE_TTL_EXERCISE_COUNT: int = 300   # PR-28 global COUNT(*), pure seq scan
    RATE_LIMIT_ENABLED: bool = True

    #  networking 
    CORS_ORIGINS_RAW: str = Field("http://localhost:5173", alias="CORS_ORIGINS")
    TRUSTED_HOSTS_RAW: str = Field("*", alias="TRUSTED_HOSTS")

    #  razorpay 
    RAZORPAY_KEY_ID: str
    RAZORPAY_KEY_SECRET: str
    RAZORPAY_BASE_URL: str = "https://api.razorpay.com/v1"
    RAZORPAY_TIMEOUT: float = 10.0
    RAZORPAY_VERIFY_SIGNATURE: bool = True    # Decision 2 — security fix, on by default
    PAYMENTS_RECOMPUTE_PRICE: bool = False    # Decision 2b — parity default

    #  mail (replaces PHP mail(), PR-33) ─
    # Off in local dev where no SMTP server is listening: without this every
    # intake submission logs a ConnectionRefusedError traceback and spends
    # ~4s in connection attempts. Must stay true in staging/production.
    MAIL_ENABLED: bool = True
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = True
    MAIL_FROM: str = "GRIND Intake <noreply@trenddma.com>"
    INTAKE_RECIPIENT: str = os.getenv("GMAIL_EMAIL")

    #  gmail notification for GET /api/v1/workout (PR-N/A, additive)
    # Deliberately a SEPARATE credential block from SMTP_* above: that one
    # belongs to the intake mailer, and sharing it would couple two unrelated
    # features to one mailbox.
    #
    # Default OFF. The endpoint is the highest-traffic route in the app and
    # Gmail caps a free account at ~500 recipients/day, so this must be
    # switched on deliberately, with the recipient understood — see
    # send_workout_email() for the full rate-limit note.
    WORKOUT_EMAIL_ENABLED: bool = False
    # True  -> mail on EVERY successful response, cache hits included.
    # False -> mail only when the response was actually built from the DB,
    #          which caps volume at roughly one message per client per
    #          CACHE_TTL_WORKOUT (60s) instead of one per request.
    WORKOUT_EMAIL_ON_CACHE_HIT: bool = True
    GMAIL_HOST: str = "smtp.gmail.com"
    # 587 = STARTTLS submission, 465 = implicit TLS. _send_workout_sync picks
    # the transport from this value, so either port works unchanged.
    GMAIL_PORT: int = 587
    GMAIL_EMAIL: str = os.getenv("GMAIL_EMAIL")         # full gmail address = SMTP username
    GMAIL_PASSWORD: str = os.getenv("GMAIL_PASSWORD")     # 16-char App Password, NOT the account password
    GMAIL_FROM: str = os.getenv("GMAIL_FROM")        # blank -> GMAIL_EMAIL
    GMAIL_TO: str = os.getenv("GMAIL_TO")           # blank -> GMAIL_EMAIL
    GMAIL_TIMEOUT: int = 15        # seconds for the whole SMTP exchange

    #  applicants table (intake persistence)
    # The table is created at startup if absent and left alone if present —
    # see ensure_applicants_table() in app/db/session.py. Set false once the
    # Alembic revision has been applied in a managed environment, so schema
    # changes come from migrations only.
    APPLICANTS_AUTO_CREATE: bool = True
    # Retention window, stamped onto each row as expires_at at insert time.
    # scripts/purge_applicants.py deletes rows whose expires_at has passed;
    # a NULL expires_at is never purged (converted leads).
    APPLICANT_RETENTION_DAYS: int = 30

    #  mail log file (both mailers)
    # Mail is fire-and-forget from a background task, so nothing about it ever
    # reaches an HTTP status code. This file is the only durable answer to
    # "did it send, and what was in it?" — see app/core/logging.py.
    MAIL_LOG_ENABLED: bool = True
    MAIL_LOG_PATH: str = "logs/mail.log"        # relative -> project root
    # False keeps the audit trail (who/when/outcome) but drops the message text,
    # for deployments where plan contents must not sit on disk.
    MAIL_LOG_BODY: bool = True
    MAIL_LOG_MAX_BYTES: int = 5_000_000         # ~5 MB per file before rotation
    MAIL_LOG_BACKUP_COUNT: int = 3              # mail.log.1 .. mail.log.3

    #  legacy parity switches (Decisions 3 & 4) 
    LEGACY_DEFAULT_CLIENT_ID: int = 1         # PR-01
    LEGACY_AFFILIATE_SKIP_EXPIRY: bool = True # PR-10 — validate ignores expiry
    LEGACY_ZERO_PRICE_CHECKS_ORIGINAL: bool = True  # PR-16
    LEGACY_GLOBAL_PROGRESS_DENOMINATOR: bool = True # PR-28
    LEGACY_OPEN_ADMIN: bool = False           # Decision 3


    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Pin the database credentials to .env.

        pydantic-settings' default precedence is process environment > .env, so
        a DB_HOST left exported in a shell, inherited from a parent process, or
        injected by a docker-compose `environment:` block would silently
        outrank the file and point the app at the wrong database. Hoisting a
        .env-only source above the environment — and masking those same names
        out of the environment source — makes .env the single origin for
        DB_HOST/PORT/USER/PASSWORD/NAME.

        Everything else keeps stock precedence, so the operational overrides
        that are meant to come from the environment (REDIS_URL, LOG_LEVEL,
        WEB_CONCURRENCY, pool sizes) still work.
        """
        return (
            init_settings,
            _FilteredSource(settings_cls, dotenv_settings, DB_CREDENTIAL_FIELDS, keep=True),
            _FilteredSource(settings_cls, env_settings, DB_CREDENTIAL_FIELDS, keep=False),
            dotenv_settings,
            file_secret_settings,
        )

    # Derived data
    @field_validator("ENV", mode="before")
    @classmethod
    def _lower_env(cls, v: str) -> str:
        return str(v).lower()

    @field_validator("SMTP_PASSWORD", "GMAIL_PASSWORD", mode="before")
    @classmethod
    def _strip_app_password(cls, v: str | None) -> str:
        """Drop whitespace from a Gmail app password.

        Google presents app passwords as four space-separated groups of four
        ("abcd efgh ijkl mnop") purely for legibility; the credential is the
        16 characters. Pasted verbatim it authenticates as a 19-character
        string and fails with 535, which reads exactly like a wrong password
        and costs an hour to spot. Stripping here is safe: no SMTP password
        may contain a space.
        """
        return "" if v is None else "".join(str(v).split())

    
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
        """SQLAlchemy DSN for BigRock MySQL.

        aiomysql is PyMySQL's asyncio wrapper — it imports pymysql and reuses
        its protocol implementation — so this is the PyMySQL driver, reached
        through the one dialect that keeps AsyncSession working. Every route
        depends on get_db() yielding an AsyncSession, so a synchronous
        pymysql.connect() here would break all of them.
        """
        # quote_plus: a password containing @ / : / # otherwise corrupts the
        # URL — "pw@2026" makes SQLAlchemy read the host as "2026@localhost".
        user = quote_plus(self.DB_USER)
        password = quote_plus(self.DB_PASSWORD)
        return (
            f"mysql+aiomysql://{user}:{password}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            f"?charset={self.DB_CHARSET}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def IS_PROD(self) -> bool:
        return self.ENV == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()

