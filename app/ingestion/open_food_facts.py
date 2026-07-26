import bz2
import gzip
import json
import lzma
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.barcodes import BarcodeError, parse_barcode
from app.ingestion.base import ProductSource
from app.models import Product
from app.tools.db_check import database_size_bytes, format_size


SOURCE_NAME = "Open Food Facts"


def clean_text(value: Any) -> str | None:
    if not isinstance(value, (str, int, float)):
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = clean_text(item)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


def normalize_nutrition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = (str, int, float, bool)
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, allowed) and item != ""
    }


def parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (
            isinstance(value, str) and value.isdigit()
        ):
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _open_text(path: Path) -> TextIO:
    suffix = path.suffix.lower()
    if suffix == ".gz":
        return gzip.open(path, mode="rt", encoding="utf-8")
    if suffix == ".bz2":
        return bz2.open(path, mode="rt", encoding="utf-8")
    if suffix in {".xz", ".lzma"}:
        return lzma.open(path, mode="rt", encoding="utf-8")
    return path.open(encoding="utf-8")


class OpenFoodFactsSource(ProductSource):
    """Stream Open Food Facts JSONL (plain/gzip/bzip2/xz) or small JSON arrays."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.errors = 0

    def records(self) -> Iterator[dict[str, Any]]:
        with _open_text(self.path) as source:
            first = source.read(1)
            if not first:
                return
            if first == "[":
                # Kept for small development fixtures; bulk exports use JSONL.
                payload = json.load(source_with_prefix(source, first))
                for record in payload:
                    if isinstance(record, dict):
                        yield record
                return
            line = first + source.readline()
            while line:
                if line.strip():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        self.errors += 1
                    else:
                        if isinstance(record, dict):
                            yield record
                        else:
                            self.errors += 1
                line = source.readline()


def source_with_prefix(source: TextIO, prefix: str) -> TextIO:
    """Return a tiny text wrapper used only for bounded JSON-array fixtures."""
    from io import StringIO

    return StringIO(prefix + source.read())


# Backward-compatible name used by the original sample importer and tests.
OpenFoodFactsJsonSource = OpenFoodFactsSource


@dataclass(slots=True)
class ImportStats:
    processed: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    invalid_barcodes: int = 0
    unsupported_barcodes: int = 0
    missing_product_data: int = 0
    errors: int = 0
    database_products: int = 0
    database_size_bytes: int | None = None
    elapsed_seconds: float = 0.0

    @property
    def rate(self) -> float:
        return self.processed / self.elapsed_seconds if self.elapsed_seconds else 0.0

    def report(self) -> str:
        return "\n".join(
            [
                f"Processed: {self.processed:,}",
                f"Inserted: {self.inserted:,}",
                f"Updated: {self.updated:,}",
                f"Skipped: {self.skipped:,}",
                f"Invalid barcode: {self.invalid_barcodes:,}",
                f"Unsupported barcode: {self.unsupported_barcodes:,}",
                f"Missing product data: {self.missing_product_data:,}",
                f"Errors: {self.errors:,}",
                f"Database products: {self.database_products:,}",
                f"Approximate database size: {format_size(self.database_size_bytes)}",
                f"Time: {self.elapsed_seconds:.2f} s",
                f"Rate: {self.rate:.1f} records/s",
            ]
        )


def normalize_record(record: dict[str, Any], stats: ImportStats) -> Product | None:
    source_barcode = clean_text(record.get("code") or record.get("_id"))
    try:
        parsed = parse_barcode(source_barcode or "")
    except BarcodeError as exc:
        if "8, 12, 13, or 14" in str(exc):
            stats.unsupported_barcodes += 1
        else:
            stats.invalid_barcodes += 1
        stats.skipped += 1
        return None

    product_name = clean_text(record.get("product_name"))
    generic_name = clean_text(record.get("generic_name"))
    brand = clean_text(record.get("brands"))
    name = product_name or generic_name or brand
    if not name:
        stats.missing_product_data += 1
        stats.skipped += 1
        return None

    # GTIN-14 is the single internal identity; source_id retains the original code.
    return Product(
        barcode=parsed.gtin14,
        barcode_type=parsed.barcode_type,
        name=name,
        brand=brand,
        categories=normalize_tags(record.get("categories_tags") or record.get("categories")),
        quantity=clean_text(record.get("quantity")),
        image_url=clean_text(record.get("image_url")),
        ingredients=clean_text(record.get("ingredients_text")),
        allergens=normalize_tags(record.get("allergens_tags") or record.get("allergens")),
        nutrition=normalize_nutrition(record.get("nutriments")),
        countries=normalize_tags(record.get("countries_tags") or record.get("countries")),
        source=SOURCE_NAME,
        source_id=source_barcode,
        source_updated_at=parse_timestamp(
            record.get("last_modified_t") or record.get("last_modified_datetime")
        ),
    )


def _apply_batch(session: Session, products: list[Product], stats: ImportStats) -> None:
    # Last record wins for duplicate source rows in the same batch.
    incoming = {product.barcode: product for product in products}
    canonical_codes = list(incoming)
    candidates = set(canonical_codes)
    for code in canonical_codes:
        if code.startswith("0"):
            candidates.add(code[1:])
        if code.startswith("00"):
            candidates.add(code[2:])

    existing_rows = list(
        session.scalars(select(Product).where(Product.barcode.in_(candidates)))
    )
    existing_by_canonical: dict[str, Product] = {}
    for row in existing_rows:
        try:
            canonical = parse_barcode(row.barcode).gtin14
        except BarcodeError:
            canonical = row.barcode
        current = existing_by_canonical.get(canonical)
        if current is None or row.barcode == canonical:
            existing_by_canonical[canonical] = row

    fields = (
        "barcode_type",
        "name",
        "brand",
        "categories",
        "quantity",
        "image_url",
        "ingredients",
        "allergens",
        "nutrition",
        "countries",
        "source",
        "source_id",
        "source_updated_at",
    )
    for canonical, product in incoming.items():
        existing = existing_by_canonical.get(canonical)
        if existing is None:
            session.add(product)
            stats.inserted += 1
            continue
        for field in fields:
            setattr(existing, field, getattr(product, field))
        if existing.barcode != canonical and session.get(Product, canonical) is None:
            existing.barcode = canonical
        stats.updated += 1
    session.commit()


def import_dataset(
    source: ProductSource,
    session_factory: sessionmaker[Session],
    *,
    limit: int | None = None,
    batch_size: int = 1_000,
    progress_every: int = 10_000,
) -> ImportStats:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")

    stats = ImportStats()
    source_errors_before = getattr(source, "errors", 0)
    batch: list[Product] = []
    started = time.perf_counter()
    with session_factory() as session:
        iterator = source.records()
        while limit is None or stats.processed < limit:
            try:
                record = next(iterator)
            except StopIteration:
                break
            except (json.JSONDecodeError, UnicodeError, OSError):
                stats.errors += 1
                stats.skipped += 1
                break
            stats.processed += 1
            try:
                product = normalize_record(record, stats)
            except Exception:
                stats.errors += 1
                stats.skipped += 1
                product = None
            if product is not None:
                batch.append(product)
            if len(batch) >= batch_size:
                _apply_batch(session, batch, stats)
                batch.clear()
            if progress_every and stats.processed % progress_every == 0:
                elapsed = time.perf_counter() - started
                rate = stats.processed / elapsed if elapsed else 0
                print(f"Processed {stats.processed:,} records ({rate:.1f} records/s)")
        if batch:
            _apply_batch(session, batch, stats)
        stats.database_products = session.scalar(
            select(func.count()).select_from(Product)
        ) or 0
        stats.database_size_bytes = database_size_bytes(session)
    source_errors = getattr(source, "errors", 0) - source_errors_before
    stats.processed += source_errors
    stats.errors += source_errors
    stats.skipped += source_errors
    stats.elapsed_seconds = time.perf_counter() - started
    return stats


def is_plausible_dataset(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if path.suffix.lower() == ".gz":
        with path.open("rb") as source:
            return source.read(2) == b"\x1f\x8b"
    return True


def download_dataset(
    url: str,
    destination: Path,
    *,
    force: bool = False,
    progress_bytes: int = 100 * 1024 * 1024,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        if is_plausible_dataset(destination):
            print(
                f"Dataset already exists; keeping {destination} "
                f"({format_size(destination.stat().st_size)})."
            )
            return destination
        raise ValueError(
            f"Existing dataset looks invalid: {destination}. "
            "Remove it or use --force-download."
        )
    partial = destination.with_suffix(destination.suffix + ".part")
    if force:
        partial.unlink(missing_ok=True)
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "GroceryBarcodeAPI/0.1 (dataset importer)"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(
        url, headers=headers
    )
    try:
        with urllib.request.urlopen(request) as response:
            resumed = offset > 0 and getattr(response, "status", None) == 206
            mode = "ab" if resumed else "wb"
            if offset and not resumed:
                offset = 0
            downloaded = offset
            next_progress = downloaded + progress_bytes
            with partial.open(mode) as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    downloaded += len(chunk)
                    if progress_bytes and downloaded >= next_progress:
                        print(f"Downloaded {format_size(downloaded)} ...")
                        next_progress = downloaded + progress_bytes
        partial.replace(destination)
    except Exception:
        raise
    if not is_plausible_dataset(destination):
        raise ValueError(f"Downloaded dataset looks invalid: {destination}")
    print(f"Dataset ready: {destination} ({format_size(destination.stat().st_size)})")
    return destination
