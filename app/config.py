from functools import lru_cache
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class PlanLimit(BaseModel):
    monthly_lookups: int
    requests_per_minute: int


def default_plan_limits() -> dict[str, PlanLimit]:
    return {
        "FREE": PlanLimit(monthly_lookups=250, requests_per_minute=30),
        "STARTER": PlanLimit(monthly_lookups=2_000, requests_per_minute=300),
        "GROWTH": PlanLimit(monthly_lookups=5_000, requests_per_minute=1_200),
    }


class Settings(BaseSettings):
    app_name: str = "Grocery & Retail Product Barcode API"
    app_env: str = "development"
    database_url: str = "sqlite:///./products.db"
    auto_create_tables: bool = True
    cors_allowed_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]
    trusted_hosts: Annotated[list[str], NoDecode] = [
        "localhost",
        "127.0.0.1",
        "testserver",
    ]
    log_level: str = "INFO"
    batch_limit: int = 100
    open_food_facts_dataset_url: str = (
        "https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz"
    )
    ingestion_batch_size: int = 1_000
    lookup_analytics_retention_days: int = Field(default=180, ge=30, le=730)
    fallback_lookups_enabled: bool = False
    fallback_user_agent: str = "BarcodeNest/1.0 (support@barcodenest.com)"
    open_facts_fallback_enabled: bool = True
    open_facts_timeout_seconds: float = Field(default=2.5, gt=0, le=10)
    open_facts_negative_ttl_seconds: int = Field(default=86400, ge=60)
    upcitemdb_enabled: bool = True
    upcitemdb_api_key: str | None = None
    upcitemdb_persistence_enabled: bool = True
    upcitemdb_timeout_seconds: float = Field(default=2.5, gt=0, le=10)
    upcitemdb_negative_ttl_seconds: int = Field(default=86400, ge=60)
    upcitemdb_min_interval_seconds: float = Field(default=10.0, ge=0)
    # Disabled by default until the product surface implements Google's
    # required attribution/linking treatment.
    google_books_enabled: bool = False
    google_books_api_key: str | None = None
    google_books_timeout_seconds: float = Field(default=2.5, gt=0, le=10)
    google_books_negative_ttl_seconds: int = Field(default=86400, ge=60)
    open_library_enabled: bool = True
    open_library_timeout_seconds: float = Field(default=2.5, gt=0, le=10)
    open_library_negative_ttl_seconds: int = Field(default=86400, ge=60)
    api_key_hash_secret: str = "development-only-change-me"
    jwt_secret: str = "development-only-jwt-secret-change-me"
    jwt_issuer: str = "https://api.barcodenest.com"
    jwt_audience: str = "barcodenest-account"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    auth_cookie_secure: bool = False
    registration_enabled: bool = True
    website_url: str = "http://localhost:3000"
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: str | None = None
    oauth_state_minutes: int = 10
    resend_api_key: str | None = None
    email_from: str = "BarcodeNest <notifications@barcodenest.com>"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_starter_price_id: str | None = None
    stripe_growth_price_id: str | None = None
    stripe_starter_annual_price_id: str | None = None
    stripe_growth_annual_price_id: str | None = None
    stripe_starter_payment_link: str | None = "https://buy.stripe.com/8x25kw3UB0DH81kbtF0Ny01"
    stripe_growth_payment_link: str | None = "https://buy.stripe.com/3cI8wIezffyB81k1T50Ny00"
    stripe_starter_annual_payment_link: str | None = "https://buy.stripe.com/aFa4gs8aR5Y1a9sfJV0Ny03"
    stripe_growth_annual_payment_link: str | None = "https://buy.stripe.com/eVqaEQ9eV5Y16Xg0P10Ny02"
    plan_limits: dict[str, PlanLimit] = Field(default_factory=default_plan_limits)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("trusted_hosts", mode="before")
    @classmethod
    def parse_trusted_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def effective_cors_allowed_origins(self) -> list[str]:
        """Include the canonical website origins required by browser account flows."""
        origins = list(self.cors_allowed_origins)
        website_origin = self.website_url.rstrip("/")
        if website_origin and website_origin not in origins:
            origins.append(website_origin)
        if website_origin == "https://barcodenest.com":
            www_origin = "https://www.barcodenest.com"
            if www_origin not in origins:
                origins.append(www_origin)
        return origins

    @field_validator("database_url")
    @classmethod
    def select_psycopg_driver(cls, value: str) -> str:
        """Use psycopg v3 for standard PostgreSQL URLs supplied by cloud hosts."""
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @field_validator("batch_limit")
    @classmethod
    def validate_batch_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("BATCH_LIMIT must be at least 1")
        return value

    @field_validator("plan_limits")
    @classmethod
    def normalize_plan_names(
        cls, value: dict[str, PlanLimit]
    ) -> dict[str, PlanLimit]:
        return {name.upper(): limits for name, limits in value.items()}

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if self.app_env.lower() not in {"production", "prod"}:
            return self
        insecure_secrets = {
            "",
            "development-only-change-me",
            "replace-with-a-long-random-production-secret",
            "development-only-jwt-secret-change-me",
            "changeme",
        }
        if (
            self.api_key_hash_secret.lower() in insecure_secrets
            or len(self.api_key_hash_secret) < 32
        ):
            raise ValueError(
                "Production requires API_KEY_HASH_SECRET with at least 32 "
                "characters and no placeholder value"
            )
        if len(self.jwt_secret) < 32 or self.jwt_secret.lower() in insecure_secrets:
            raise ValueError(
                "Production requires JWT_SECRET with at least 32 characters "
                "and no placeholder value"
            )
        if not self.auth_cookie_secure:
            raise ValueError("Production requires AUTH_COOKIE_SECURE=true")
        if self.auto_create_tables:
            raise ValueError(
                "Production requires AUTO_CREATE_TABLES=false; use Alembic migrations"
            )
        if not self.database_url.startswith(("postgresql://", "postgresql+")):
            raise ValueError("Production requires a PostgreSQL DATABASE_URL")
        if self.website_url.rstrip("/") != "https://barcodenest.com":
            raise ValueError("Production requires WEBSITE_URL=https://barcodenest.com")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
