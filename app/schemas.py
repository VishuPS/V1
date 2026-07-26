from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
