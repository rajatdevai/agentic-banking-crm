"""
Centralised settings for the RM Copilot platform.

Rules:
- Every service imports from here. No service reads os.environ directly.
- All settings have types and defaults. Missing required values surface
  immediately at startup with a clear error — not at runtime.
- Pydantic-settings reads from environment variables AND a .env file.
- Nested models (DatabaseSettings, OpenAISettings, etc.) keep related
  config grouped and documented.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
class DatabaseSettings(BaseSettings):
    DATABASE_URL: str = Field(..., description="asyncpg cloud PostgreSQL connection URL")
    DATABASE_POOL_SIZE: int = Field(20, description="SQLAlchemy pool size")
    DATABASE_MAX_OVERFLOW: int = Field(40, description="SQLAlchemy max overflow connections")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------
class RedisSettings(BaseSettings):
    REDIS_URL: str = Field("redis://localhost:6379/0", description="Redis connection URL")
    REDIS_CACHE_TTL_SECONDS: int = Field(
        14400, description="Default cache TTL in seconds (4 hours)"
    )
    # Session TTL for PII token vault (matches JWT expiry)
    REDIS_PII_VAULT_TTL_SECONDS: int = Field(
        28800, description="PII token vault TTL in seconds (8 hours)"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
class OpenAISettings(BaseSettings):
    OPENAI_API_KEY: str = Field(..., description="OpenAI API key — required")
    OPENAI_PRIMARY_MODEL: str = Field(
        "gpt-4o", description="Primary model for quality tasks (explainability, outreach)"
    )
    OPENAI_FAST_MODEL: str = Field(
        "gpt-4o-mini", description="Cost-optimised model for summarisation and classification"
    )
    OPENAI_EMBEDDING_MODEL: str = Field(
        "text-embedding-3-large", description="Embedding model for RAG ingestion"
    )
    OPENAI_MAX_RETRIES: int = Field(3, description="Max retry attempts on transient API errors")
    OPENAI_TIMEOUT_SECONDS: float = Field(
        30.0, description="Per-request timeout for OpenAI API calls"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ---------------------------------------------------------------------------
# JWT Authentication
# ---------------------------------------------------------------------------
class JWTSettings(BaseSettings):
    SECRET_KEY: str = Field(..., description="JWT signing secret — must be long and random in production")
    JWT_ALGORITHM: str = Field("HS256", description="JWT signing algorithm")
    JWT_EXPIRY_MINUTES: int = Field(480, description="Access token validity in minutes (8 hours)")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ---------------------------------------------------------------------------
# PII Masking
# ---------------------------------------------------------------------------
class PIIMaskSettings(BaseSettings):
    PII_MASK_ENABLED: bool = Field(
        True, description="Master switch for Presidio PII masking — NEVER disable in production"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ---------------------------------------------------------------------------
# Notification Providers
# ---------------------------------------------------------------------------
class NotificationSettings(BaseSettings):
    # WhatsApp (Meta Graph API)
    WHATSAPP_API_URL: str = Field("https://graph.facebook.com/v19.0")
    WHATSAPP_PHONE_NUMBER_ID: Optional[str] = Field(None)
    WHATSAPP_ACCESS_TOKEN: Optional[str] = Field(None)

    # SMS (Twilio)
    TWILIO_ACCOUNT_SID: Optional[str] = Field(None)
    TWILIO_AUTH_TOKEN: Optional[str] = Field(None)
    TWILIO_FROM_NUMBER: Optional[str] = Field(None)

    # Email (SendGrid)
    SENDGRID_API_KEY: Optional[str] = Field(None)
    SENDGRID_FROM_EMAIL: Optional[str] = Field(None)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ---------------------------------------------------------------------------
# ML Models
# ---------------------------------------------------------------------------
class MLSettings(BaseSettings):
    CONVERSION_MODEL_PATH: str = Field(
        "services/ml/models/conversion_score/model.ubj",
        description="Path to trained XGBoost conversion probability model artifact",
    )
    CHURN_MODEL_PATH: str = Field(
        "services/ml/models/churn_score/model.ubj",
        description="Path to trained LightGBM churn model artifact",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------
class RAGSettings(BaseSettings):
    VECTOR_SIMILARITY_THRESHOLD: float = Field(
        0.75, description="Minimum cosine similarity score for RAG retrieval to include a chunk"
    )
    RAG_TOP_K: int = Field(5, description="Number of chunks returned after reranking")
    RAG_RETRIEVAL_CANDIDATES: int = Field(
        30, description="Candidates fetched before reranking (dense + sparse merged)"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
class AppSettings(BaseSettings):
    APP_ENV: str = Field("development", description="Environment: development | staging | production")
    LOG_LEVEL: str = Field("INFO", description="Logging level")
    ALLOWED_ORIGINS: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173", "http://localhost:8000"],
        description="CORS allowed origins for the gateway",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# ---------------------------------------------------------------------------
# Master Settings — composes all groups
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    """
    Master settings object. Import this everywhere:

        from shared.config.settings import get_settings
        settings = get_settings()
        settings.OPENAI_API_KEY
    """

    # Application
    APP_ENV: str = Field("development")
    LOG_LEVEL: str = Field("INFO")
    ALLOWED_ORIGINS: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173", "http://localhost:8000"]
    )

    # Database
    DATABASE_URL: str = Field(...)
    DATABASE_POOL_SIZE: int = Field(20)
    DATABASE_MAX_OVERFLOW: int = Field(40)

    # Redis
    REDIS_URL: str = Field("redis://localhost:6379/0")
    REDIS_CACHE_TTL_SECONDS: int = Field(14400)
    REDIS_PII_VAULT_TTL_SECONDS: int = Field(28800)

    # OpenAI
    OPENAI_API_KEY: str = Field(...)
    OPENAI_PRIMARY_MODEL: str = Field("gpt-4o")
    OPENAI_FAST_MODEL: str = Field("gpt-4o-mini")
    OPENAI_EMBEDDING_MODEL: str = Field("text-embedding-3-large")
    OPENAI_MAX_RETRIES: int = Field(3)
    OPENAI_TIMEOUT_SECONDS: float = Field(30.0)

    # JWT
    SECRET_KEY: str = Field(...)
    JWT_ALGORITHM: str = Field("HS256")
    JWT_EXPIRY_MINUTES: int = Field(480)

    # PII
    PII_MASK_ENABLED: bool = Field(True)

    # Notifications
    WHATSAPP_API_URL: str = Field("https://graph.facebook.com/v19.0")
    WHATSAPP_PHONE_NUMBER_ID: Optional[str] = Field(None)
    WHATSAPP_ACCESS_TOKEN: Optional[str] = Field(None)
    TWILIO_ACCOUNT_SID: Optional[str] = Field(None)
    TWILIO_AUTH_TOKEN: Optional[str] = Field(None)
    TWILIO_FROM_NUMBER: Optional[str] = Field(None)
    SENDGRID_API_KEY: Optional[str] = Field(None)
    SENDGRID_FROM_EMAIL: Optional[str] = Field(None)

    # ML
    CONVERSION_MODEL_PATH: str = Field("services/ml/models/conversion_score/model.ubj")
    CHURN_MODEL_PATH: str = Field("services/ml/models/churn_score/model.ubj")

    # RAG
    VECTOR_SIMILARITY_THRESHOLD: float = Field(0.75)
    RAG_SIMILARITY_THRESHOLD: float = Field(0.70)   # Alias used by retriever (slightly lower for better recall)
    RAG_TOP_K: int = Field(5)
    RAG_RETRIEVAL_CANDIDATES: int = Field(30)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_required_secrets(self) -> "Settings":
        """
        Fail fast at startup if critical secrets are missing.
        Better to crash on boot than to silently operate without them.
        """
        if not self.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY is not set. "
                "Set a real OpenAI API key in your .env file."
            )
        if not self.DATABASE_URL:
            raise ValueError(
                "DATABASE_URL is not set. "
                "Set your cloud Postgres URL (Neon/Supabase) in your .env file."
            )
        if not self.SECRET_KEY or len(self.SECRET_KEY) < 16:
            raise ValueError(
                "SECRET_KEY is not set or too short (minimum 16 characters). "
                "Generate one: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns the singleton Settings instance.
    Cached with lru_cache so the .env file is read exactly once per process.

    Usage:
        from shared.config.settings import get_settings
        settings = get_settings()
    """
    return Settings()
