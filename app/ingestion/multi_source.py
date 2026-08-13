from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Product, ProductSourceRecord, ProductSourceSync, new_uuid


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class MappedSourceProduct:
    canonical_gtin: str
    barcode_type: str
    name: str
    source: str
    source_product_id: str
    source_gtin: str
    license: str
    priority: int = 100
    source_url: str | None = None
    source_updated_at: datetime | None = None
    brand: str | None = None
    categories: list[str] = field(default_factory=list)
    quantity: str | None = None
    image_url: str | None = None
    ingredients: str | None = None
    allergens: list[str] = field(default_factory=list)
    nutrition: dict[str, Any] = field(default_factory=dict)
    countries: list[str] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SourceImportStats:
    source: str
    processed: int = 0
    inserted: int = 0
    enriched: int = 0
    unchanged: int = 0
    provenance_inserted: int = 0
    provenance_updated: int = 0
    skipped: int = 0
    invalid_barcodes: int = 0
    errors: int = 0
    elapsed_seconds: float = 0.0
    database_products: int | None = None

    @property
    def rate(self) -> float:
        return self.processed / self.elapsed_seconds if self.elapsed_seconds else 0.0

    def report(self) -> str:
        return "\n".join([
            f"Source: {self.source}", f"Processed: {self.processed:,}",
            f"Canonical products inserted: {self.inserted:,}",
            f"Existing products enriched: {self.enriched:,}",
            f"Existing products unchanged: {self.unchanged:,}",
            f"Provenance inserted: {self.provenance_inserted:,}",
            f"Provenance updated: {self.provenance_updated:,}",
            f"Invalid GTINs rejected: {self.invalid_barcodes:,}",
            f"Records skipped: {self.skipped:,}", f"Errors: {self.errors:,}",
            f"Database products: {self.database_products:,}" if self.database_products is not None else "Database products: not queried (dry run)",
            f"Time: {self.elapsed_seconds:.2f} s", f"Rate: {self.rate:.1f} records/s",
        ])


def _merge_unique(existing: list[str], incoming: list[str]) -> tuple[list[str], bool]:
    result = list(existing or [])
    seen = {value.casefold() for value in result}
    for value in incoming or []:
        if value.casefold() not in seen:
            result.append(value)
            seen.add(value.casefold())
    return result, result != (existing or [])


def merge_canonical(product: Product, incoming: MappedSourceProduct) -> bool:
    """Conservatively fill gaps; never replace a useful existing scalar value."""
    changed = False
    for field_name in ("brand", "quantity", "image_url", "ingredients"):
        if not getattr(product, field_name) and getattr(incoming, field_name):
            setattr(product, field_name, getattr(incoming, field_name))
            changed = True
    for field_name in ("categories", "allergens", "countries"):
        merged, field_changed = _merge_unique(getattr(product, field_name), getattr(incoming, field_name))
        if field_changed:
            setattr(product, field_name, merged)
            changed = True
    if incoming.nutrition:
        merged_nutrition = dict(product.nutrition or {})
        before = len(merged_nutrition)
        for key, value in incoming.nutrition.items():
            if key not in merged_nutrition or merged_nutrition[key] in (None, ""):
                merged_nutrition[key] = value
        if len(merged_nutrition) != before:
            product.nutrition = merged_nutrition
            changed = True
    return changed


def _apply_batch(session: Session, records: list[MappedSourceProduct], stats: SourceImportStats) -> None:
    # Last occurrence of a source identity wins within the batch.
    unique = {(item.source, item.source_product_id): item for item in records}
    records = list(unique.values())
    gtins = {item.canonical_gtin for item in records}
    source_ids = {item.source_product_id for item in records}
    source = records[0].source
    products = {item.barcode: item for item in session.scalars(select(Product).where(Product.barcode.in_(gtins)))}
    provenances = {
        item.source_product_id: item
        for item in session.scalars(
            select(ProductSourceRecord).where(
                ProductSourceRecord.source == source,
                ProductSourceRecord.source_product_id.in_(source_ids),
            )
        )
    }
    now = utcnow()
    for incoming in records:
        product = products.get(incoming.canonical_gtin)
        if product is None:
            product = Product(
                barcode=incoming.canonical_gtin, barcode_type=incoming.barcode_type,
                name=incoming.name, brand=incoming.brand, categories=incoming.categories,
                quantity=incoming.quantity, image_url=incoming.image_url,
                ingredients=incoming.ingredients, allergens=incoming.allergens,
                nutrition=incoming.nutrition, countries=incoming.countries,
                source=incoming.source, source_id=incoming.source_product_id,
                source_updated_at=incoming.source_updated_at,
            )
            session.add(product)
            products[incoming.canonical_gtin] = product
            stats.inserted += 1
        elif merge_canonical(product, incoming):
            stats.enriched += 1
        else:
            stats.unchanged += 1
        provenance = provenances.get(incoming.source_product_id)
        if provenance is None:
            provenance = ProductSourceRecord(
                id=new_uuid(), product_barcode=incoming.canonical_gtin,
                source=incoming.source, source_product_id=incoming.source_product_id,
                source_gtin=incoming.source_gtin, source_url=incoming.source_url,
                license=incoming.license, priority=incoming.priority,
                imported_at=now, source_updated_at=incoming.source_updated_at,
                last_seen_at=now, source_metadata=incoming.source_metadata,
            )
            session.add(provenance)
            provenances[incoming.source_product_id] = provenance
            stats.provenance_inserted += 1
        else:
            provenance.product_barcode = incoming.canonical_gtin
            provenance.source_gtin = incoming.source_gtin
            provenance.source_url = incoming.source_url
            provenance.license = incoming.license
            provenance.priority = incoming.priority
            provenance.source_updated_at = incoming.source_updated_at
            provenance.last_seen_at = now
            provenance.source_metadata = incoming.source_metadata
            stats.provenance_updated += 1


def import_mapped_records(
    records: Iterable[MappedSourceProduct], session_factory: sessionmaker[Session], *,
    source: str, dataset_url: str | None, dataset_fingerprint: str,
    batch_size: int = 1_000, limit: int | None = None, dry_run: bool = False,
    resume: bool = False, progress_every: int = 10_000,
) -> SourceImportStats:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    stats = SourceImportStats(source=source)
    started = time.perf_counter()
    if dry_run:
        for item in records:
            if limit is not None and stats.processed >= limit:
                break
            stats.processed += 1
            if progress_every and stats.processed % progress_every == 0:
                print(f"Validated {stats.processed:,} {source} records", flush=True)
        stats.elapsed_seconds = time.perf_counter() - started
        return stats
    with session_factory() as session:
        sync = session.scalar(select(ProductSourceSync).where(
            ProductSourceSync.source == source,
            ProductSourceSync.dataset_fingerprint == dataset_fingerprint,
        ))
        skip_until = sync.checkpoint_record if resume and sync else 0
        if sync is None:
            sync = ProductSourceSync(source=source, dataset_url=dataset_url, dataset_fingerprint=dataset_fingerprint)
            session.add(sync)
            session.commit()
        batch: list[MappedSourceProduct] = []
        for position, item in enumerate(records, start=1):
            if position <= skip_until:
                continue
            if limit is not None and stats.processed >= limit:
                break
            stats.processed += 1
            batch.append(item)
            if len(batch) >= batch_size:
                _apply_batch(session, batch, stats)
                if sync:
                    sync.checkpoint_record = position
                    sync.processed = skip_until + stats.processed
                    sync.inserted, sync.enriched, sync.skipped = stats.inserted, stats.enriched, stats.skipped
                session.commit()
                batch.clear()
            if progress_every and stats.processed % progress_every == 0:
                print(f"Processed {stats.processed:,} {source} records", flush=True)
        if batch:
            _apply_batch(session, batch, stats)
            if sync:
                sync.checkpoint_record = skip_until + stats.processed
            session.commit()
        if sync:
            sync.status = "completed"
            sync.completed_at = utcnow()
            sync.processed = skip_until + stats.processed
            sync.inserted, sync.enriched, sync.skipped = stats.inserted, stats.enriched, stats.skipped
            session.commit()
        stats.database_products = session.scalar(select(func.count()).select_from(Product)) or 0
    stats.elapsed_seconds = time.perf_counter() - started
    return stats
