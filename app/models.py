from datetime import date, datetime, timezone
from calendar import monthrange
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid4())


def current_month_end() -> datetime:
    now = utcnow()
    return now.replace(day=monthrange(now.year, now.month)[1], hour=23, minute=59, second=59, microsecond=999999)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_source_identity", "source", "source_id"),
    )

    barcode: Mapped[str] = mapped_column(String(14), primary_key=True)
    barcode_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    quantity: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    ingredients: Mapped[str | None] = mapped_column(Text)
    allergens: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    nutrition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    countries: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    source_records: Mapped[list["ProductSourceRecord"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ApiClient(Base):
    __tablename__ = "api_clients"
    free_credits_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    free_credits_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)


    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    identifier: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="FREE")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    owner: Mapped["User | None"] = relationship(back_populates="api_clients")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str | None] = mapped_column(String(128))
    key_prefix: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    client: Mapped[ApiClient] = relationship(back_populates="api_keys")
    owner: Mapped["User | None"] = relationship(back_populates="api_keys")
    usage: Mapped[list["MonthlyUsage"]] = relationship(
        back_populates="api_key", cascade="all, delete-orphan"
    )


class MonthlyUsage(Base):
    __tablename__ = "monthly_usage"
    __table_args__ = (
        UniqueConstraint("api_key_id", "period_start", name="uq_monthly_usage_key_period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    api_key_id: Mapped[str] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lookup_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    api_key: Mapped[ApiKey] = relationship(back_populates="usage")


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    __table_args__ = (
        UniqueConstraint("api_key_id", "usage_date", name="uq_daily_usage_key_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    api_key_id: Mapped[str] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lookup_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

class ProductSourceRecord(Base):
    """Traceable source contribution for one canonical GTIN product."""

    __tablename__ = "product_sources"
    __table_args__ = (
        UniqueConstraint("source", "source_product_id", name="uq_product_sources_identity"),
        Index("ix_product_sources_product_source", "product_barcode", "source"),
        Index("ix_product_sources_source_gtin", "source", "source_gtin"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    product_barcode: Mapped[str] = mapped_column(
        ForeignKey("products.barcode", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_product_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_gtin: Mapped[str] = mapped_column(String(14), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[int] = mapped_column(nullable=False, default=100)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    product: Mapped[Product] = relationship(back_populates="source_records")


class ProductSourceSync(Base):
    """Dataset synchronization state and resumable record checkpoint."""

    __tablename__ = "product_source_syncs"
    __table_args__ = (
        UniqueConstraint("source", "dataset_fingerprint", name="uq_product_source_sync_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dataset_url: Mapped[str | None] = mapped_column(Text)
    dataset_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    checkpoint_record: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    processed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    inserted: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    enriched: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class LookupAnalytics(Base):
    """Minimal product-coverage telemetry; never stores credentials or network identity."""

    __tablename__ = "lookup_analytics"
    __table_args__ = (
        Index("ix_lookup_analytics_occurred_found", "occurred_at", "found"),
        Index("ix_lookup_analytics_gtin_found", "canonical_gtin", "found"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    api_key_id: Mapped[str | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    canonical_gtin: Mapped[str] = mapped_column(String(14), nullable=False)
    barcode_type: Mapped[str] = mapped_column(String(16), nullable=False)
    endpoint_type: Mapped[str] = mapped_column(String(16), nullable=False)
    found: Mapped[bool] = mapped_column(Boolean, nullable=False)
    local_found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fallback_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    providers_attempted: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    provider_found: Mapped[str | None] = mapped_column(String(64))
    resolution_source: Mapped[str | None] = mapped_column(String(64))
    resolution_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class FallbackProviderState(Base):
    """Provider-specific negative/check state; never stores API credentials."""

    __tablename__ = "fallback_provider_states"
    __table_args__ = (
        UniqueConstraint("canonical_gtin", "provider", name="uq_fallback_state_gtin_provider"),
        Index("ix_fallback_state_expires", "provider", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    canonical_gtin: Mapped[str] = mapped_column(String(14), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retry_after_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[str | None] = mapped_column(String(128))


class RegistrationRequest(Base):
    __tablename__ = "registration_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    organization: Mapped[str | None] = mapped_column(String(200))
    use_case: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    api_client_id: Mapped[str | None] = mapped_column(ForeignKey("api_clients.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"
    workspace_policy: Mapped[str] = mapped_column(String(24),nullable=False,default="platform_v1",server_default="platform_v1")

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    organization: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    free_tier_registration_ip_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    api_clients: Mapped[list[ApiClient]] = relationship(back_populates="owner")
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="owner")
    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    oauth_identities: Mapped[list["OAuthIdentity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ProductSubmission(Base):
    __tablename__ = "product_submissions"
    __table_args__ = (
        UniqueConstraint("submitted_by_user_id", "canonical_gtin", name="uq_product_submission_user_gtin"),
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','NEEDS_CHANGES')", name="ck_product_submission_status"),
        Index("ix_product_submissions_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    submitted_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    brand_profile_id: Mapped[str | None] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"), index=True)
    store_profile_id: Mapped[str | None] = mapped_column(ForeignKey("stores.id", ondelete="SET NULL"), index=True)
    submitted_gtin: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_gtin: Mapped[str] = mapped_column(String(14), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str] = mapped_column(Text, nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    net_content: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    mpn: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    country_of_sale: Mapped[str | None] = mapped_column(String(120))
    product_url: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    contribution_source: Mapped[str] = mapped_column(String(32), nullable=False, default="USER_CONTRIBUTED")
    terms_version: Mapped[str] = mapped_column(String(32), nullable=False, default="2026-08")
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    review_notes: Mapped[str | None] = mapped_column(Text)
    contributor_message: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class StoreSubmission(Base):
    __tablename__ = "store_submissions"
    __table_args__ = (
        UniqueConstraint("normalized_name", "normalized_website", name="uq_store_submission_identity"),
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','NEEDS_CHANGES')", name="ck_store_submission_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    submitted_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    website: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_website: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(String(160))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    terms_version: Mapped[str] = mapped_column(String(32), nullable=False, default="2026-08")
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    review_notes: Mapped[str | None] = mapped_column(Text)
    contributor_message: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    source_submission_id: Mapped[str] = mapped_column(ForeignKey("store_submissions.id", ondelete="RESTRICT"), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(180), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    website: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class BrandSubmission(Base):
    __tablename__ = "brand_submissions"
    __table_args__ = (
        UniqueConstraint("normalized_name", "normalized_website", name="uq_brand_submission_identity"),
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','NEEDS_CHANGES')", name="ck_brand_submission_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    submitted_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    company: Mapped[str | None] = mapped_column(String(240))
    website: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_website: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(String(120))
    contact_name: Mapped[str | None] = mapped_column(String(160))
    business_email: Mapped[str | None] = mapped_column(String(320))
    description: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    terms_version: Mapped[str] = mapped_column(String(32), nullable=False, default="2026-08")
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    review_notes: Mapped[str | None] = mapped_column(Text)
    contributor_message: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Brand(Base):
    __tablename__ = "brands"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    source_submission_id: Mapped[str] = mapped_column(ForeignKey("brand_submissions.id", ondelete="RESTRICT"), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(180), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str | None] = mapped_column(String(240))
    website: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ProductOffer(Base):
    __tablename__ = "product_offers"
    __table_args__ = (
        UniqueConstraint("store_id", "product_barcode", "product_url", name="uq_product_offer_identity"),
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')", name="ck_product_offer_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    product_barcode: Mapped[str] = mapped_column(ForeignKey("products.barcode", ondelete="CASCADE"), nullable=False, index=True)
    product_url: Mapped[str] = mapped_column(Text, nullable=False)
    price_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    availability: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class BulkSubmission(Base):
    __tablename__ = "bulk_submissions"
    __table_args__ = (CheckConstraint("status IN ('PENDING','APPROVED','REJECTED','NEEDS_CHANGES')", name="ck_bulk_submission_status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    submitted_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    row_count: Mapped[int] = mapped_column(nullable=False)
    valid_row_count: Mapped[int] = mapped_column(nullable=False)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    terms_version: Mapped[str] = mapped_column(String(32), nullable=False, default="2026-08")
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    review_notes: Mapped[str | None] = mapped_column(Text)
    contributor_message: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class OAuthIdentity(Base):
    __tablename__ = "oauth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_oauth_provider_subject"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="oauth_identities")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions")


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    monthly_lookups: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requests_per_minute: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_cents: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Subscription(Base):
    __tablename__ = "subscriptions"
    free_credits_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    free_credits_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow)


    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_code: Mapped[str] = mapped_column(
        ForeignKey("subscription_plans.code"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    provider: Mapped[str | None] = mapped_column(String(32))
    provider_customer_id: Mapped[str | None] = mapped_column(String(255), index=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    provider_price_id: Mapped[str | None] = mapped_column(String(255), index=True)
    billing_interval: Mapped[str] = mapped_column(String(16), nullable=False, default="month")
    monthly_call_limit: Mapped[int] = mapped_column(BigInteger, nullable=False, default=250)
    monthly_calls_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    usage_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    usage_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=current_month_end, nullable=False)
    usage_warning_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_limit_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="subscriptions")
    plan: Mapped[SubscriptionPlan] = relationship()


class CallPack(Base):
    __tablename__ = "call_packs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    checkout_session_id: Mapped[str] = mapped_column(String(255), unique=True)
    calls: Mapped[int] = mapped_column(BigInteger, nullable=False, default=500)
    used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ProductRecovery(Base):
    __tablename__ = "product_recovery"

    canonical_gtin: Mapped[str] = mapped_column(String(14), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    infrastructure_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    provider: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[str | None] = mapped_column(String(256))


class StripeWebhookEvent(Base):
    __tablename__ = "stripe_webhook_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProviderCooldown(Base):
    __tablename__ = "provider_cooldowns"
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_allowed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(128))


class VaultProduct(Base):
    __tablename__ = "vault_products"
    __table_args__ = (
        UniqueConstraint("user_id", "identity_key", name="uq_vault_user_identity"),
        Index("ix_vault_user_updated", "user_id", "updated_at"),
        Index("ix_vault_variant_parent","variant_of"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    identity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    barcode: Mapped[str] = mapped_column(String(500), nullable=False)
    barcode_type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    brand: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    collection: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    price_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    custom_fields: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(nullable=False, default=1)
    supplier: Mapped[str] = mapped_column(String(200),nullable=False,default="",server_default="")
    tags: Mapped[list[str]] = mapped_column(JSON,nullable=False,default=list,server_default="[]")
    variant_of: Mapped[str | None] = mapped_column(ForeignKey("vault_products.id",ondelete="SET NULL",name="fk_vault_variant_parent"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProductIdentifier(Base):
    __tablename__="product_identifiers"
    __table_args__=(UniqueConstraint("user_id","identity_key",name="uq_identifier_owner_identity"),Index("ix_identifier_owner_product","user_id","product_id"),Index("ix_identifier_product","product_id"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_uuid)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False)
    product_id: Mapped[str]=mapped_column(ForeignKey("vault_products.id",ondelete="CASCADE"),nullable=False)
    identity_key: Mapped[str]=mapped_column(String(64),nullable=False)
    value: Mapped[str]=mapped_column(String(500),nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,nullable=False)


class SkuSequence(Base):
    __tablename__="sku_sequences"
    __table_args__=(UniqueConstraint("user_id","sequence_key",name="uq_sku_owner_sequence"),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_uuid)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False)
    sequence_key: Mapped[str]=mapped_column(String(64),nullable=False)
    prefix: Mapped[str]=mapped_column(String(20),nullable=False)
    category_code: Mapped[str]=mapped_column(String(20),nullable=False)
    pattern: Mapped[str]=mapped_column(String(100),nullable=False)
    next_value: Mapped[int]=mapped_column(BigInteger,nullable=False)


class SkuAllocation(Base):
    __tablename__="sku_allocations"
    __table_args__=(UniqueConstraint("user_id","code",name="uq_sku_owner_code"),Index("ix_sku_owner_created","user_id","created_at"),Index("ix_sku_product","product_id"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_uuid)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False)
    product_id: Mapped[str | None]=mapped_column(ForeignKey("vault_products.id",ondelete="SET NULL"))
    code: Mapped[str]=mapped_column(String(80),nullable=False)
    category: Mapped[str]=mapped_column(String(100),nullable=False,default="")
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,nullable=False)


class LabelTemplate(Base):
    __tablename__="label_templates"
    __table_args__=(UniqueConstraint("user_id","name",name="uq_label_owner_name"),Index("ix_label_owner_updated","user_id","updated_at"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_uuid)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False)
    name: Mapped[str]=mapped_column(String(100),nullable=False)
    definition: Mapped[dict]=mapped_column(JSON,nullable=False)
    revision: Mapped[int]=mapped_column(nullable=False,default=1)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=utcnow)


class InventoryItem(Base):
    __tablename__="inventory_items"
    __table_args__=(UniqueConstraint("user_id","product_id",name="uq_inventory_product"),CheckConstraint("quantity >= 0 AND quantity <= 1000000000",name="ck_inventory_quantity"),Index("ix_inventory_owner","user_id","updated_at"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_uuid)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False)
    product_id: Mapped[str]=mapped_column(ForeignKey("vault_products.id",ondelete="RESTRICT"),nullable=False)
    quantity: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    low_stock_threshold: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    location: Mapped[str]=mapped_column(String(200),nullable=False,default="")
    purchase_minor: Mapped[int | None]=mapped_column(BigInteger)
    currency: Mapped[str]=mapped_column(String(3),nullable=False,default="USD")
    revision: Mapped[int]=mapped_column(nullable=False,default=1)
    archived: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,nullable=False)


class StockMovement(Base):
    __tablename__="stock_movements"
    __table_args__=(UniqueConstraint("user_id","request_id",name="uq_stock_request"),Index("ix_stock_owner_created","user_id","created_at"),Index("ix_stock_item","inventory_id","created_at"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_uuid)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False)
    inventory_id: Mapped[str]=mapped_column(ForeignKey("inventory_items.id",ondelete="RESTRICT"),nullable=False)
    request_id: Mapped[str]=mapped_column(String(64),nullable=False)
    delta: Mapped[int]=mapped_column(BigInteger,nullable=False)
    balance: Mapped[int]=mapped_column(BigInteger,nullable=False)
    reason: Mapped[str]=mapped_column(String(500),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,nullable=False)


class AssetRecord(Base):
    __tablename__="asset_records"
    __table_args__=(UniqueConstraint("user_id","asset_code",name="uq_asset_owner_code"),Index("ix_asset_owner_updated","user_id","updated_at"),Index("ix_asset_product","product_id"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_uuid)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False)
    product_id: Mapped[str|None]=mapped_column(ForeignKey("vault_products.id",ondelete="SET NULL"))
    asset_code: Mapped[str]=mapped_column(String(80),nullable=False)
    name: Mapped[str]=mapped_column(String(200),nullable=False)
    description: Mapped[str]=mapped_column(String(4000),nullable=False,default="")
    location: Mapped[str]=mapped_column(String(200),nullable=False,default="")
    assigned_person: Mapped[str]=mapped_column(String(200),nullable=False,default="")
    acquired_on: Mapped[date|None]=mapped_column(Date)
    purchase_minor: Mapped[int|None]=mapped_column(BigInteger)
    currency: Mapped[str]=mapped_column(String(3),nullable=False,default="USD")
    condition: Mapped[str]=mapped_column(String(20),nullable=False,default="good")
    status: Mapped[str]=mapped_column(String(20),nullable=False,default="available")
    checked_out_to: Mapped[str]=mapped_column(String(200),nullable=False,default="")
    revision: Mapped[int]=mapped_column(nullable=False,default=1)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,nullable=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,nullable=False)


class AssetEvent(Base):
    __tablename__="asset_events"
    __table_args__=(Index("ix_asset_event_owner_created","user_id","created_at"),Index("ix_asset_event_record","asset_id","created_at"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_uuid)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False)
    asset_id: Mapped[str]=mapped_column(ForeignKey("asset_records.id",ondelete="RESTRICT"),nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    note: Mapped[str]=mapped_column(String(1000),nullable=False,default="")
    event_date: Mapped[date|None]=mapped_column(Date)
    before: Mapped[dict]=mapped_column(JSON,nullable=False)
    after: Mapped[dict]=mapped_column(JSON,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,nullable=False)


class FunnelEvent(Base):
    __tablename__="funnel_events"
    __table_args__=(UniqueConstraint("dedupe_key",name="uq_funnel_dedupe"),Index("ix_funnel_created_event","created_at","event"),Index("ix_funnel_user_created","user_id","created_at"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_uuid)
    user_id: Mapped[str|None]=mapped_column(ForeignKey("users.id",ondelete="SET NULL"))
    visit_hash: Mapped[str|None]=mapped_column(String(64))
    event: Mapped[str]=mapped_column(String(32),nullable=False)
    tool: Mapped[str]=mapped_column(String(32),nullable=False,default="platform")
    dedupe_key: Mapped[str|None]=mapped_column(String(160))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,nullable=False)


class WorkspaceReceipt(Base):
    __tablename__="workspace_receipts"
    __table_args__=(UniqueConstraint("user_id","request_id",name="uq_workspace_receipt"),Index("ix_receipt_created","created_at"))
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_uuid)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False)
    request_id: Mapped[str]=mapped_column(String(64),nullable=False)
    operation: Mapped[str]=mapped_column(String(20),nullable=False)
    digest: Mapped[str]=mapped_column(String(64),nullable=False)
    result: Mapped[dict]=mapped_column(JSON,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,nullable=False)


class PlatformStatistic(Base):
    __tablename__ = "platform_statistics"
    key: Mapped[str] = mapped_column(String(64),primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger,nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    method: Mapped[str] = mapped_column(String(32),nullable=False)


class ServiceUsage(Base):
    __tablename__ = "service_usage"
    __table_args__ = (
        UniqueConstraint("account_scope", "category", "period_start", name="uq_service_usage_scope_category_period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    account_scope: Mapped[str] = mapped_column(String(48), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
