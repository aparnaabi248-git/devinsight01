"""Central configuration. All secrets are read from environment variables only."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings JSON-decodes complex fields *before* any validator runs, so a plain
# comma-separated `CORS_ORIGINS=a,b,c` in .env raises a SettingsError instead of reaching
# the validator that is meant to split it. `NoDecode` opts out of that step and lets the
# validator below handle both `a,b,c` and `["a","b"]`.
#
# The TYPE_CHECKING branch makes this a real type alias for mypy; the runtime branch keeps
# the project installable against pydantic-settings < 2.3, which has no `NoDecode`.
if TYPE_CHECKING:  # pragma: no cover
    from pydantic_settings import NoDecode

    _CsvList = Annotated[list[str], NoDecode]
else:
    try:
        from pydantic_settings import NoDecode

        _CsvList = Annotated[list[str], NoDecode]
    except ImportError:  # pragma: no cover - older pydantic-settings
        _CsvList = list[str]

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Resolved against the repository root, not the current working directory, so
        # `alembic` run from backend/ and `uvicorn` started from the repo root load the
        # same configuration instead of silently falling back to different defaults.
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -----------------------------------------------------
    PROJECT_NAME: str = "DevInsight"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    # --- Database --------------------------------------------------------
    POSTGRES_USER: str = "devinsight"
    POSTGRES_PASSWORD: str = "devinsight"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "devinsight"
    DATABASE_URL: str | None = None
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # --- Security --------------------------------------------------------
    SECRET_KEY: str = "dev-only-insecure-key-change-me-in-production-0123456789"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    BCRYPT_ROUNDS: int = 12

    # --- CORS ------------------------------------------------------------
    CORS_ORIGINS: _CsvList = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:3000",
            "http://localhost:8080",
        ]
    )

    # --- GitHub ----------------------------------------------------------
    GITHUB_TOKEN: str | None = None
    GITHUB_API_URL: str = "https://api.github.com"
    GITHUB_TIMEOUT: float = 30.0
    GITHUB_MAX_RETRIES: int = 4
    GITHUB_PER_PAGE: int = 100
    GITHUB_CACHE_TTL: int = 900  # seconds for ETag-backed response cache

    # --- ML --------------------------------------------------------------
    MLFLOW_TRACKING_URI: str = "http://localhost:5000"
    MLFLOW_TRACKING_ENABLED: bool = False
    MODEL_DIR: Path = REPO_ROOT / "ml" / "artifacts"
    ML_MIN_CONFIDENCE: float = 0.0

    # --- Ingestion -------------------------------------------------------
    INGEST_MAX_COMMITS: int = 4000
    INGEST_MAX_ISSUES: int = 1500
    RAW_DIR: Path = REPO_ROOT / "data" / "raw"
    PROCESSED_DIR: Path = REPO_ROOT / "data" / "processed"
    FEATURES_DIR: Path = REPO_ROOT / "data" / "features"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v: Any) -> list[str]:
        """Accept `a,b,c` and `["a","b"]` alike."""
        if isinstance(v, str):
            text = v.strip()
            if text.startswith("["):  # a JSON array
                import json

                try:
                    return [str(o).strip() for o in json.loads(text) if str(o).strip()]
                except json.JSONDecodeError:
                    text = text.strip("[]\"' ")
            return [o.strip() for o in text.split(",") if o.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(o).strip() for o in v if str(o).strip()]
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def sqlalchemy_url(self) -> str:
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            # Normalise the common `postgres://` alias used by hosting providers.
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            return url
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in {"production", "prod"}

    def ensure_dirs(self) -> None:
        for d in (self.RAW_DIR, self.PROCESSED_DIR, self.FEATURES_DIR, self.MODEL_DIR):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and s.SECRET_KEY.startswith("dev-only"):
        raise RuntimeError(
            "SECRET_KEY must be set to a strong random value when ENVIRONMENT=production"
        )
    s.ensure_dirs()
    return s


settings = get_settings()
