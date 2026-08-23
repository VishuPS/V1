from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Status = Literal["PENDING", "APPROVED", "REJECTED", "NEEDS_CHANGES"]


class ProductCheck(BaseModel):
    submitted_gtin: str
    canonical_gtin: str
    barcode_type: str
    exists: bool
    product: dict[str, Any] | None = None


class ProductSubmissionCreate(BaseModel):
    accepted_terms: Literal[True]
    barcode: str = Field(min_length=1, max_length=32)
    product_name: str = Field(min_length=1, max_length=500)
    brand: str = Field(min_length=1, max_length=500)
    manufacturer: str | None = Field(None, max_length=500)
    category: str | None = Field(None, max_length=500)
    net_content: str | None = Field(None, max_length=200)
    quantity: str | None = Field(None, max_length=200)
    model: str | None = Field(None, max_length=200)
    mpn: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=5000)
    country_of_sale: str | None = Field(None, max_length=120)
    product_url: str | None = Field(None, max_length=2048)
    image_url: str | None = Field(None, max_length=2048)
    brand_profile_id: str | None = Field(None, min_length=36, max_length=36)
    store_profile_id: str | None = Field(None, min_length=36, max_length=36)


class StoreSubmissionCreate(BaseModel):
    accepted_terms: Literal[True]
    name: str = Field(min_length=1, max_length=200)
    website: str = Field(min_length=1, max_length=2048)
    country: str = Field(min_length=1, max_length=120)
    description: str | None = Field(None, max_length=3000)
    logo_url: str | None = Field(None, max_length=2048)
    contact_name: str | None = Field(None, max_length=160)
    contact_email: str | None = Field(None, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class BrandSubmissionCreate(BaseModel):
    accepted_terms: Literal[True]
    name: str = Field(min_length=1, max_length=200)
    company: str | None = Field(None, max_length=240)
    website: str = Field(min_length=1, max_length=2048)
    country: str | None = Field(None, max_length=120)
    contact_name: str | None = Field(None, max_length=160)
    business_email: str | None = Field(None, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    description: str | None = Field(None, max_length=3000)
    logo_url: str | None = Field(None, max_length=2048)


class BulkSubmissionCreate(BaseModel):
    accepted_terms: Literal[True]
    filename: str = Field(min_length=1, max_length=255)
    csv_content: str = Field(min_length=1, max_length=1_000_000)


class OfferCreate(BaseModel):
    store_id: str = Field(min_length=36, max_length=36)
    barcode: str = Field(min_length=1, max_length=32)
    product_url: str = Field(min_length=1, max_length=2048)
    price_minor: int | None = Field(None, ge=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    availability: Literal["IN_STOCK", "OUT_OF_STOCK", "PREORDER", "UNKNOWN"] | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else None


class SubmissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: Status
    created_at: datetime


class ContributionItem(BaseModel):
    id: str
    type: Literal["PRODUCT", "STORE", "BRAND", "BULK"]
    label: str
    status: Status
    created_at: datetime
    contributor_message: str | None = None


class MyContributions(BaseModel):
    items: list[ContributionItem]
    totals: dict[str, int]


class AdminContributionSummary(BaseModel):
    pending_products: int
    pending_stores: int
    pending_brands: int
    pending_bulk_submissions: int


class AdminContributionItem(BaseModel):
    id: str
    type: Literal["PRODUCT", "STORE", "BRAND", "BULK"]
    label: str
    secondary: str | None
    status: Status
    contributor_name: str
    contributor_email: str
    created_at: datetime
    data: dict[str, Any]


class ReviewAction(BaseModel):
    action: Literal["APPROVE", "REJECT", "NEEDS_CHANGES"]
    review_notes: str | None = Field(None, max_length=5000)
    contributor_message: str | None = Field(None, max_length=2000)


class PublicProfile(BaseModel):
    slug: str
    name: str
    website: str
    country: str | None
    description: str | None
    logo_url: str | None
