from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.ingestion.multi_source import MappedSourceProduct
from app.ingestion.open_food_facts import ImportStats, OpenFoodFactsSource, normalize_record


@dataclass(frozen=True, slots=True)
class OpenFactsDefinition:
    key: str
    source: str
    dataset_url: str
    product_url: str
    default_category: str
    license: str = "ODbL-1.0"
    priority: int = 180


OPEN_FACTS_SOURCES = {
    "beauty": OpenFactsDefinition("beauty", "OPEN_BEAUTY_FACTS", "https://world.openbeautyfacts.org/data/openbeautyfacts-products.jsonl.gz", "https://world.openbeautyfacts.org/product/{source_id}", "Beauty and personal care"),
    "pet": OpenFactsDefinition("pet", "OPEN_PET_FOOD_FACTS", "https://world.openpetfoodfacts.org/data/openpetfoodfacts-products.jsonl.gz", "https://world.openpetfoodfacts.org/product/{source_id}", "Pet food"),
    "products": OpenFactsDefinition("products", "OPEN_PRODUCTS_FACTS", "https://world.openproductsfacts.org/data/openproductsfacts-products.jsonl.gz", "https://world.openproductsfacts.org/product/{source_id}", "General products"),
}


class OpenFactsAdapter:
    """Reuse the proven Open Food Facts JSONL parser with explicit provenance."""

    def __init__(self, path: Path, definition: OpenFactsDefinition) -> None:
        self.path = path
        self.definition = definition
        self.invalid_barcodes = 0
        self.skipped = 0

    def records(self) -> Iterator[MappedSourceProduct]:
        local_stats = ImportStats()
        for raw in OpenFoodFactsSource(self.path).records():
            before_invalid = local_stats.invalid_barcodes + local_stats.unsupported_barcodes
            product = normalize_record(raw, local_stats)
            self.invalid_barcodes += local_stats.invalid_barcodes + local_stats.unsupported_barcodes - before_invalid
            if product is None:
                self.skipped += 1
                continue
            categories = list(product.categories or [])
            if self.definition.default_category not in categories:
                categories.append(self.definition.default_category)
            yield MappedSourceProduct(
                canonical_gtin=product.barcode, barcode_type=product.barcode_type,
                name=product.name, brand=product.brand, categories=categories,
                quantity=product.quantity, image_url=product.image_url,
                ingredients=product.ingredients, allergens=product.allergens,
                nutrition=product.nutrition, countries=product.countries,
                source=self.definition.source, source_product_id=product.source_id,
                source_gtin=product.source_id,
                source_url=self.definition.product_url.format(source_id=product.source_id),
                license=self.definition.license, priority=self.definition.priority,
                source_updated_at=product.source_updated_at,
                source_metadata={"dataset": self.definition.key},
            )
