from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_source_identity", "source", "source_id"),
    )

    barcode: Mapped[str] = mapped_column(String(14), primary_key=True)
    barcode_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(512))
    categories: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    quantity: Mapped[str | None] = mapped_column(String(128))
    image_url: Mapped[str | None] = mapped_column(String(2048))
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
