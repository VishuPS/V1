from functools import lru_cache
from typing import Annotated

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class PlanLimit(BaseModel):
    monthly_lookups: int
    requests_per_minute: int


class ServiceLimit(BaseModel):
    scans: int = Field(default=50, ge=0)
    generation: int = Field(ge=0)
    labels: int = Field(ge=0)
    enrichment: int = Field(ge=0)
    batch_generation_max: int = Field(default=0, ge=0, le=100)


def default_service_limits() -> dict[str, ServiceLimit]:
    return {
        "CREATOR": ServiceLimit(scans=500, generation=1_000, labels=250, enrichment=0, batch_generation_max=25),
        "FREE": ServiceLimit(generation=100, labels=25, enrichment=250),
        "ONE_TIME": ServiceLimit(generation=100, labels=25, enrichment=250),
        "STARTER": ServiceLimit(scans=2_000, generation=2_000, labels=500, enrichment=2_000, batch_generation_max=25),
        "GROWTH": ServiceLimit(scans=5_000, generation=10_000, labels=2_500, enrichment=5_000, batch_generation_max=100),
    }


def default_plan_limits() -> dict[str, PlanLimit]:
    return {
        "CREATOR": PlanLimit(monthly_lookups=0, requests_per_minute=120),
        "FREE": PlanLimit(monthly_lookups=250, requests_per_minute=30),
        "ONE_TIME": PlanLimit(monthly_lookups=500, requests_per_minute=30),
        "STARTER": PlanLimit(monthly_lookups=2_000, requests_per_minute=300),
        "GROWTH": PlanLimit(monthly_lookups=5_000, requests_per_minute=1_200),
    }


class Settings(BaseSettings):
    new_customer_vault_limits: dict[str,int] = {"FREE":25,"ONE_TIME":25,"CREATOR":500,"STARTER":2000,"GROWTH":10000}
    workspace_batch_limit: int = Field(default=100,ge=1,le=1000)
    workspace_upload_bytes: int = Field(default=5_000_000,ge=1000,le=20_000_000)
    vault_limits: dict[str, int] = {"FREE":25,"ONE_TIME":25,"CREATOR":250,"STARTER":2000,"GROWTH":10000}
    service_limits: dict[str, ServiceLimit] = Field(default_factory=default_service_limits)
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
    recovery_enabled: bool = False
    recovery_worker_enabled: bool = False
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
    eandb_enabled: bool = False
    eandb_api_key: str | None = None
    eandb_user_agent: str | None = None
    eandb_persistence_enabled: bool = False
    eandb_timeout_seconds: float = Field(default=2.5, gt=0, le=10)
    eandb_negative_ttl_seconds: int = Field(default=86400, ge=60)
    open_icecat_enabled: bool = False
    open_icecat_api_token: str | None = None
    open_icecat_persistence_enabled: bool = False
    open_icecat_timeout_seconds: float = Field(default=3.0, gt=0, le=10)
    open_icecat_negative_ttl_seconds: int = Field(default=86400, ge=60)
    open_icecat_min_interval_seconds: float = Field(default=1.0, ge=0)
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
    free_tier_ip_limit_enabled: bool = False
    website_url: str = "http://localhost:3000"
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: str | None = None
    oauth_state_minutes: int = 10
    support_mail_address: str = "support@barcodenest.com"
    support_mail_password: SecretStr | None = None
    resend_api_key: str | None = None
    email_from: str = "BarcodeNest <notifications@barcodenest.com>"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_one_time_price_id: str | None = None
    stripe_creator_price_id: str | None = None
    stripe_starter_price_id: str | None = None
    stripe_growth_price_id: str | None = None
    stripe_starter_annual_price_id: str | None = None
    stripe_growth_annual_price_id: str | None = None
    stripe_starter_payment_link: str | None = "https://buy.stripe.com/8x25kw3UB0DH81kbtF0Ny01"
    stripe_growth_payment_link: str | None = "https://buy.stripe.com/3cI8wIezffyB81k1T50Ny00"
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

    @field_validator("service_limits")
    @classmethod
    def normalize_service_names(cls, value: dict[str, ServiceLimit]) -> dict[str, ServiceLimit]:
        limits = {name.upper(): item for name, item in value.items()}
        defaults = default_service_limits()
        for name, item in limits.items():
            if "scans" not in item.model_fields_set and name in defaults:
                limits[name] = item.model_copy(update={"scans": defaults[name].scans})
        if "FREE" in limits:
            limits.setdefault("ONE_TIME", limits["FREE"])
        limits.setdefault("CREATOR", default_service_limits()["CREATOR"])
        return limits

    @field_validator("plan_limits")
    @classmethod
    def normalize_plan_names(
        cls, value: dict[str, PlanLimit]
    ) -> dict[str, PlanLimit]:
        return {"CREATOR": PlanLimit(monthly_lookups=0, requests_per_minute=120), "ONE_TIME": PlanLimit(monthly_lookups=500, requests_per_minute=30), **{name.upper(): limits for name, limits in value.items()}}

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
