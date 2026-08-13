from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.barcodes import BarcodeError, parse_barcode
from app.ingestion.multi_source import MappedSourceProduct
from app.ingestion.open_food_facts import (
    OpenFoodFactsSource,
    clean_text,
    normalize_nutrition,
    normalize_tags,
    parse_timestamp,
    select_image_url,
)


@dataclass(frozen=True, slots=True)
class OpenFactsDefinition:
    key: str
    source: str
    dataset_url: str
    product_url: str
    default_category: str
    supports_nutrition: bool = False
    license: str = "ODbL-1.0"
    contents_license: str = "DbCL-1.0"
    image_license: str = "CC-BY-SA"
    priority: int = 180


OPEN_FACTS_SOURCES = {
    "beauty": OpenFactsDefinition(
        "beauty",
        "OPEN_BEAUTY_FACTS",
        "https://world.openbeautyfacts.org/data/openbeautyfacts-products.jsonl.gz",
        "https://world.openbeautyfacts.org/product/{source_id}",
        "Beauty and personal care",
    ),
    "pet": OpenFactsDefinition(
        "pet",
        "OPEN_PET_FOOD_FACTS",
        "https://world.openpetfoodfacts.org/data/openpetfoodfacts-products.jsonl.gz",
        "https://world.openpetfoodfacts.org/product/{source_id}",
        "Pet food",
        supports_nutrition=True,
    ),
    "products": OpenFactsDefinition(
        "products",
        "OPEN_PRODUCTS_FACTS",
        "https://world.openproductsfacts.org/data/openproductsfacts-products.jsonl.gz",
        "https://world.openproductsfacts.org/product/{source_id}",
        "General products",
    ),
}


def _metadata(record: dict[str, Any], definition: OpenFactsDefinition, source_id: str) -> dict[str, Any]:
    """Keep useful source-only fields bounded and JSON-compatible."""
    metadata: dict[str, Any] = {
        "dataset": definition.key,
        "database_license": definition.license,
        "contents_license": definition.contents_license,
        "image_license": definition.image_license,
    }
    scalar_fields = (
        "generic_name",
        "packaging",
        "packaging_text",
        "product_type",
        "periods_after_opening",
    )
    for field in scalar_fields:
        if value := clean_text(record.get(field)):
            metadata[field] = value
    tag_fields = (
        "labels_tags",
        "packaging_tags",
        "stores_tags",
        "brands_tags",
    )
    for field in tag_fields:
        if values := normalize_tags(record.get(field)):
            metadata[field.removesuffix("_tags")] = values
    # Image content has separate licensing. Retain a source reference for
    # provenance, but do not add it to the canonical product or download it.
    if image_url := select_image_url(record, source_id):
        metadata["source_image_url"] = image_url
    return metadata


class OpenFactsAdapter:
    """Stream a Product Opener JSONL export into canonical source records."""

    def __init__(self, path: Path, definition: OpenFactsDefinition) -> None:
        self.path = path
        self.definition = definition
        self.records_seen = 0
        self.invalid_barcodes = 0
        self.skipped = 0
        self.errors = 0

    def records(self) -> Iterator[MappedSourceProduct]:
        source = OpenFoodFactsSource(self.path)
        for raw in source.records():
            self.records_seen += 1
            source_id = clean_text(raw.get("code") or raw.get("_id"))
            try:
                barcode = parse_barcode(source_id or "")
            except BarcodeError:
                self.invalid_barcodes += 1
                continue

            product_name = clean_text(raw.get("product_name"))
            generic_name = clean_text(raw.get("generic_name"))
            brand = clean_text(raw.get("brands"))
            name = product_name or generic_name or brand
            if not name:
                self.skipped += 1
                continue

            categories = normalize_tags(raw.get("categories_tags") or raw.get("categories"))
            if not categories:
                categories = [self.definition.default_category]
            yield MappedSourceProduct(
                canonical_gtin=barcode.gtin14,
                barcode_type=barcode.barcode_type,
                name=name,
                brand=brand,
                categories=categories,
                quantity=clean_text(raw.get("quantity") or raw.get("product_quantity")),
                image_url=None,
                ingredients=clean_text(raw.get("ingredients_text")),
                allergens=normalize_tags(raw.get("allergens_tags") or raw.get("allergens")),
                nutrition=(
                    normalize_nutrition(raw.get("nutriments"))
                    if self.definition.supports_nutrition
                    else {}
                ),
                countries=normalize_tags(raw.get("countries_tags") or raw.get("countries")),
                source=self.definition.source,
                source_product_id=source_id or barcode.value,
                source_gtin=source_id or barcode.value,
                source_url=self.definition.product_url.format(source_id=source_id or barcode.value),
                license=self.definition.license,
                priority=self.definition.priority,
                source_updated_at=parse_timestamp(
                    raw.get("last_modified_t") or raw.get("last_modified_datetime")
                ),
                source_metadata=_metadata(raw, self.definition, source_id or barcode.value),
            )
        self.errors = source.errors
