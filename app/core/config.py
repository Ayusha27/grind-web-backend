from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

  # Database
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
    INTAKE_RECIPIENT: str = "grindfit.ai@trenddma.com"

    #  legacy parity switches (Decisions 3 & 4) 
    LEGACY_DEFAULT_CLIENT_ID: int = 1         # PR-01
    LEGACY_AFFILIATE_SKIP_EXPIRY: bool = True # PR-10 — validate ignores expiry
    LEGACY_ZERO_PRICE_CHECKS_ORIGINAL: bool = True  # PR-16
    LEGACY_GLOBAL_PROGRESS_DENOMINATOR: bool = True # PR-28
    LEGACY_OPEN_ADMIN: bool = False           # Decision 3


    # Derived data
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
        # quote_plus: a password containing @ / : / # otherwise corrupts the
        # URL — "pw@2026" makes SQLAlchemy read the host as "2026@localhost".
        user = quote_plus(self.POSTGRES_USER)
        password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql+psycopg://{user}:{password}"
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

