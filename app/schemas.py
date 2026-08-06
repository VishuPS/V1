from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic import field_validator


class ProductData(BaseModel):
    name: str
    brand: str | None = None
    categories: list[str] = Field(default_factory=list)
    quantity: str | None = None
    image_url: str | None = None
    ingredients: str | None = None
    allergens: list[str] = Field(default_factory=list)
    nutrition: dict[str, Any] = Field(default_factory=dict)
    countries: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class SourceData(BaseModel):
    name: str
    source_id: str


class LookupResult(BaseModel):
    barcode: str
    barcode_type: str | None
    canonical_gtin: str | None = None
    valid: bool
    found: bool
    product: ProductData | None = None
    source: SourceData | None = None
    error: str | None = None


class BatchRequest(BaseModel):
    barcodes: list[str]


class BatchResponse(BaseModel):
    results: list[LookupResult]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class RegistrationCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=2, max_length=160)
    organization: str | None = Field(default=None, max_length=200)
    use_case: str | None = Field(default=None, max_length=2000)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("name", "organization", "use_case")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        cleaned = value.strip().casefold()
        if cleaned.count("@") != 1:
            raise ValueError("Enter a valid email address")
        local, domain = cleaned.split("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("Enter a valid email address")
        return cleaned


class RegistrationVerified(BaseModel):
    email: str
    plan: str
    api_key: str
    key_prefix: str
    monthly_lookups: int
    requests_per_minute: int


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        return RegistrationCreate.validate_email(value)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    organization: str | None
    plan: str
    created_at: datetime
    is_admin: bool


class ApiKeyCreate(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class ApiKeySummary(BaseModel):
    id: str
    name: str | None
    key_prefix: str
    active: bool
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreated(ApiKeySummary):
    api_key: str


class UsageSummary(BaseModel):
    period_start: date
    request_count: int
    lookup_count: int
    monthly_limit: int
    remaining: int


class SubscriptionSummary(BaseModel):
    plan: str
    status: str
    price_cents: int | None
    currency: str
    current_period_end: datetime | None
    cancel_at_period_end: bool
