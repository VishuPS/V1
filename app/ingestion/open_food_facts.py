import bz2
import gzip
import json
import logging
import lzma
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from sqlalchemy import bindparam, func, select, update
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, sessionmaker

from app.barcodes import BarcodeError, parse_barcode
from app.ingestion.base import ProductSource
from app.models import Product
from app.tools.db_check import database_size_bytes, format_size


SOURCE_NAME = "Open Food Facts"
IMAGE_BASE_URL = "https://images.openfoodfacts.org/images/products"
logger = logging.getLogger(__name__)


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


def _preferred_languages(record: dict[str, Any], available: dict[str, Any]) -> list[str]:
    preferred: list[str] = []
    for value in (record.get("lang"), record.get("lc"), "en"):
        language = clean_text(value)
        if language and language in available and language not in preferred:
            preferred.append(language)
    preferred.extend(sorted(language for language in available if language not in preferred))
    return preferred


def _product_image_folder(source_barcode: str) -> str:
    folder = source_barcode.zfill(13) if len(source_barcode) < 13 else source_barcode
    if len(folder) <= 8:
        return folder
    return "/".join((folder[:3], folder[3:6], folder[6:9], folder[9:]))


def _selected_metadata_url(
    source_barcode: str,
    image_type: str,
    language: str,
    metadata: Any,
) -> str | None:
    if not isinstance(metadata, dict):
        return None
    revision = clean_text(metadata.get("rev"))
    sizes = metadata.get("sizes")
    if not revision or not isinstance(sizes, dict):
        return None
    resolution = "400" if "400" in sizes else "full" if "full" in sizes else None
    if resolution is None:
        return None
    folder = _product_image_folder(source_barcode)
    filename = f"{image_type}_{language}.{revision}.{resolution}.jpg"
    return f"{IMAGE_BASE_URL}/{folder}/{filename}"


def _url_from_selected_images(record: dict[str, Any]) -> str | None:
    selected_images = record.get("selected_images")
    if not isinstance(selected_images, dict):
        return None
    front = selected_images.get("front")
    if not isinstance(front, dict):
        return None
    for size_name in ("display", "small", "thumb"):
        urls = front.get(size_name)
        if isinstance(urls, dict):
            for language in _preferred_languages(record, urls):
                url = clean_text(urls.get(language))
                if url and url.startswith(("https://", "http://")):
                    return url
    return None


def _url_from_images_metadata(record: dict[str, Any], source_barcode: str) -> str | None:
    images = record.get("images")
    if not isinstance(images, dict):
        return None

    selected = images.get("selected")
    if isinstance(selected, dict):
        front = selected.get("front")
        if isinstance(front, dict):
            for language in _preferred_languages(record, front):
                url = _selected_metadata_url(
                    source_barcode, "front", language, front.get(language)
                )
                if url:
                    return url

    legacy_front = {
        key.removeprefix("front_"): value
        for key, value in images.items()
        if isinstance(key, str) and key.startswith("front_")
    }
    for language in _preferred_languages(record, legacy_front):
        url = _selected_metadata_url(
            source_barcode, "front", language, legacy_front.get(language)
        )
        if url:
            return url

    uploaded = images.get("uploaded")
    if not isinstance(uploaded, dict):
        uploaded = {
            key: value
            for key, value in images.items()
            if str(key).isdigit() and isinstance(value, dict)
        }
    if isinstance(uploaded, dict) and uploaded:
        usable = [
            (str(image_id), metadata)
            for image_id, metadata in uploaded.items()
            if str(image_id).isdigit() and isinstance(metadata, dict)
        ]
        usable.sort(
            key=lambda item: (
                int(item[1].get("uploaded_t") or 0),
                int(item[0]),
            ),
            reverse=True,
        )
        for image_id, metadata in usable:
            sizes = metadata.get("sizes")
            if not isinstance(sizes, dict):
                continue
            folder = _product_image_folder(source_barcode)
            if "400" in sizes:
                return f"{IMAGE_BASE_URL}/{folder}/{image_id}.400.jpg"
            if "full" in sizes:
                return f"{IMAGE_BASE_URL}/{folder}/{image_id}.jpg"
    return None


def select_image_url(record: dict[str, Any], source_barcode: str) -> str | None:
    """Select a representative OFF image without making a live API request."""
    for field in ("image_front_url", "image_url"):
        direct_url = clean_text(record.get(field))
        if direct_url and direct_url.startswith(("https://", "http://")):
            return direct_url
    return _url_from_selected_images(record) or _url_from_images_metadata(
        record, source_barcode
    )


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
        image_url=select_image_url(record, source_barcode),
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


def _apply_batch_once(
    session: Session, products: list[Product]
) -> tuple[int, int]:
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
    inserted = 0
    updated = 0
    for canonical, product in incoming.items():
        existing = existing_by_canonical.get(canonical)
        if existing is None:
            session.add(product)
            inserted += 1
            continue
        for field in fields:
            setattr(existing, field, getattr(product, field))
        if existing.barcode != canonical and session.get(Product, canonical) is None:
            existing.barcode = canonical
        updated += 1
    session.commit()
    return inserted, updated


def _data_error_summary(exc: DataError, product: Product) -> str:
    diagnostic = getattr(exc.orig, "diag", None)
    column = getattr(diagnostic, "column_name", None) or "unknown"
    field_lengths = {
        field: len(value)
        for field in (
            "name",
            "brand",
            "quantity",
            "image_url",
            "ingredients",
            "source",
            "source_id",
        )
        if isinstance((value := getattr(product, field)), str)
    }
    database_message = str(exc.orig).splitlines()[0][:300]
    return (
        f"Skipping barcode={product.barcode} source_id={product.source_id!r} "
        f"after database data error; column={column}; "
        f"field_lengths={field_lengths}; database_message={database_message!r}"
    )


def _apply_batch(session: Session, products: list[Product], stats: ImportStats) -> None:
    # Last record wins for duplicate source rows in the same batch.
    unique_products = list({product.barcode: product for product in products}.values())
    try:
        inserted, updated = _apply_batch_once(session, unique_products)
    except DataError as exc:
        session.rollback()
        if len(unique_products) == 1:
            stats.errors += 1
            stats.skipped += 1
            logger.warning(_data_error_summary(exc, unique_products[0]))
            return
        midpoint = len(unique_products) // 2
        _apply_batch(session, unique_products[:midpoint], stats)
        _apply_batch(session, unique_products[midpoint:], stats)
    else:
        stats.inserted += inserted
        stats.updated += updated


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


@dataclass(slots=True)
class ImageUpdateStats:
    processed: int = 0
    images_found: int = 0
    without_image: int = 0
    invalid_barcodes: int = 0
    errors: int = 0
    updated: int = 0
    elapsed_seconds: float = 0.0

    @property
    def rate(self) -> float:
        return self.processed / self.elapsed_seconds if self.elapsed_seconds else 0.0

    def report(self) -> str:
        return "\n".join(
            [
                f"Processed: {self.processed:,}",
                f"Records with usable image: {self.images_found:,}",
                f"Records without usable image: {self.without_image:,}",
                f"Invalid/unsupported barcode: {self.invalid_barcodes:,}",
                f"Source record errors: {self.errors:,}",
                f"Database rows updated: {self.updated:,}",
                f"Time: {self.elapsed_seconds:.2f} s",
                f"Rate: {self.rate:.1f} records/s",
            ]
        )


def _update_image_batch(session: Session, image_updates: dict[str, str]) -> int:
    if not image_updates:
        return 0
    statement = (
        update(Product.__table__)
        .where(Product.barcode == bindparam("target_barcode"))
        .where(Product.image_url.is_distinct_from(bindparam("new_image_url")))
        .values(
            image_url=bindparam("new_image_url"),
            updated_at=datetime.now(timezone.utc),
        )
    )
    result = session.execute(
        statement,
        [
            {"target_barcode": barcode, "new_image_url": image_url}
            for barcode, image_url in image_updates.items()
        ],
    )
    session.commit()
    return max(result.rowcount or 0, 0)


def update_images_only(
    source: ProductSource,
    session_factory: sessionmaker[Session],
    *,
    limit: int | None = None,
    batch_size: int = 2_000,
    progress_every: int = 100_000,
) -> ImageUpdateStats:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    stats = ImageUpdateStats()
    source_errors_before = getattr(source, "errors", 0)
    updates: dict[str, str] = {}
    started = time.perf_counter()
    with session_factory() as session:
        for record in source.records():
            if limit is not None and stats.processed >= limit:
                break
            stats.processed += 1
            source_barcode = clean_text(record.get("code") or record.get("_id"))
            try:
                parsed = parse_barcode(source_barcode or "")
            except BarcodeError:
                stats.invalid_barcodes += 1
            else:
                image_url = select_image_url(record, source_barcode)
                if image_url is None:
                    stats.without_image += 1
                else:
                    stats.images_found += 1
                    updates[parsed.gtin14] = image_url
                    if len(updates) >= batch_size:
                        stats.updated += _update_image_batch(session, updates)
                        updates.clear()
            if progress_every and stats.processed % progress_every == 0:
                elapsed = time.perf_counter() - started
                rate = stats.processed / elapsed if elapsed else 0
                print(
                    f"Processed {stats.processed:,} image records "
                    f"({rate:.1f} records/s); updated {stats.updated:,}",
                    flush=True,
                )
        if updates:
            stats.updated += _update_image_batch(session, updates)
    stats.errors = getattr(source, "errors", 0) - source_errors_before
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
