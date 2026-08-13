from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import time
import urllib.request
import zipfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from app.barcodes import BarcodeError, parse_barcode
from app.ingestion.multi_source import MappedSourceProduct


SOURCE = "USDA_FDC"
LICENSE = "CC0-1.0"
DOWNLOADS_PAGE = "https://fdc.nal.usda.gov/download-datasets/"
FDC_PRODUCT_URL = "https://fdc.nal.usda.gov/food-details/{fdc_id}/nutrients"
USER_AGENT = "BarcodeNest/1.0 (support@barcodenest.com)"


def _request(url: str, *, headers: dict[str, str] | None = None):
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    request = urllib.request.Request(url, headers=merged)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            return urllib.request.urlopen(request, timeout=60)
        except Exception as exc:
            last_error = exc
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    raise last_error or RuntimeError("USDA request failed")


def discover_latest_branded_csv_url(page_url: str = DOWNLOADS_PAGE) -> str:
    with _request(page_url) as response:
        html = response.read().decode("utf-8", errors="replace")
    candidates = re.findall(
        r'href=["\']([^"\']*FoodData_Central_branded_food_csv_[^"\']+\.zip)["\']',
        html,
        flags=re.IGNORECASE,
    )
    if not candidates:
        raise RuntimeError("USDA download page did not expose a branded-food CSV archive")
    return urljoin(page_url, candidates[0])


def download_usda_archive(url: str, cache_dir: Path, *, force: bool = False) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(urlparse(url).path).name or "usda-branded-food.zip"
    destination = cache_dir / filename
    if destination.exists() and not force:
        validate_archive(destination)
        return destination
    partial = destination.with_suffix(destination.suffix + ".part")
    if force:
        partial.unlink(missing_ok=True)
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with _request(url, headers=headers) as response:
        resumed = offset > 0 and getattr(response, "status", None) == 206
        mode = "ab" if resumed else "wb"
        with partial.open(mode) as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    partial.replace(destination)
    validate_archive(destination)
    return destination


def validate_archive(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 1_000:
        raise ValueError(f"USDA archive is missing or implausibly small: {path}")
    if not zipfile.is_zipfile(path):
        raise ValueError(f"USDA archive is not a valid ZIP file: {path}")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError(f"USDA archive failed its ZIP integrity check: {path}")
        if not any(name.lower().endswith("branded_food.csv") for name in archive.namelist()):
            raise ValueError("USDA archive does not contain branded_food.csv")


def dataset_fingerprint(path: Path) -> str:
    stat = path.stat()
    value = f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    return hashlib.sha256(value).hexdigest()


def _member(archive: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in archive.namelist() if name.lower().endswith(suffix.lower())]
    if not matches:
        raise ValueError(f"USDA archive is missing {suffix}")
    return matches[0]


def _optional_member(archive: zipfile.ZipFile, suffix: str) -> str | None:
    return next((name for name in archive.namelist() if name.lower().endswith(suffix.lower())), None)


def _csv_rows(archive: zipfile.ZipFile, suffix: str) -> Iterator[dict[str, str]]:
    with archive.open(_member(archive, suffix)) as binary:
        with io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as text:
            yield from csv.DictReader(text)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _clean(value: str | None) -> str | None:
    result = " ".join((value or "").split())
    return result or None


class USDAFoodDataCentralAdapter:
    """Stream USDA branded foods from the official normalized CSV archive."""

    def __init__(self, archive_path: Path, *, include_nutrition: bool = False) -> None:
        self.archive_path = archive_path
        self.include_nutrition = include_nutrition
        self.invalid_barcodes = 0
        self.skipped = 0

    def records(self) -> Iterator[MappedSourceProduct]:
        validate_archive(self.archive_path)
        support = USDAArchiveSupport(self.archive_path)
        support.ensure(include_nutrition=self.include_nutrition)
        with zipfile.ZipFile(self.archive_path) as archive:
            for row in _csv_rows(archive, "branded_food.csv"):
                raw_gtin = _clean(row.get("gtin_upc"))
                try:
                    barcode = parse_barcode(raw_gtin or "")
                except BarcodeError:
                    self.invalid_barcodes += 1
                    continue
                fdc_id = _clean(row.get("fdc_id"))
                if not fdc_id:
                    self.skipped += 1
                    continue
                support_data = support.lookup(fdc_id)
                name = _clean(row.get("short_description")) or support_data.get("description")
                if not name:
                    self.skipped += 1
                    continue
                serving_size = _clean(row.get("serving_size"))
                serving_unit = _clean(row.get("serving_size_unit"))
                quantity = _clean(row.get("package_weight")) or (
                    f"{serving_size} {serving_unit}" if serving_size and serving_unit else None
                )
                category = _clean(row.get("branded_food_category")) or support_data.get("category")
                yield MappedSourceProduct(
                    canonical_gtin=barcode.gtin14,
                    barcode_type=barcode.barcode_type,
                    name=name,
                    brand=_clean(row.get("brand_name")) or _clean(row.get("brand_owner")),
                    categories=[category] if category else [],
                    quantity=quantity,
                    ingredients=_clean(row.get("ingredients")),
                    nutrition=support_data.get("nutrition", {}),
                    countries=[value] if (value := _clean(row.get("market_country"))) else [],
                    source=SOURCE,
                    source_product_id=fdc_id,
                    source_gtin=raw_gtin or barcode.value,
                    source_url=FDC_PRODUCT_URL.format(fdc_id=fdc_id),
                    license=LICENSE,
                    priority=150,
                    source_updated_at=_parse_date(row.get("modified_date") or row.get("available_date")),
                    source_metadata={
                        key: value for key, value in {
                            "brand_owner": _clean(row.get("brand_owner")),
                            "subbrand_name": _clean(row.get("subbrand_name")),
                            "household_serving_fulltext": _clean(row.get("household_serving_fulltext")),
                            "data_source": _clean(row.get("data_source")),
                            "publication_date": support_data.get("publication_date"),
                        }.items() if value is not None
                    },
                )


class USDAArchiveSupport:
    """Disk-backed join for normalized USDA CSV tables; bounded application RAM."""

    NUTRIENTS = {
        "Energy": "energy_kcal", "Protein": "protein_g",
        "Total lipid (fat)": "fat_g", "Carbohydrate, by difference": "carbohydrates_g",
        "Sugars, total including NLEA": "sugars_g", "Fiber, total dietary": "fiber_g",
        "Sodium, Na": "sodium_mg", "Fatty acids, total saturated": "saturated_fat_g",
    }

    def __init__(self, archive_path: Path) -> None:
        self.archive_path = archive_path
        self.path = archive_path.with_suffix(".support.sqlite")

    def ensure(self, *, include_nutrition: bool) -> None:
        fingerprint = f"{dataset_fingerprint(self.archive_path)}:{'nutrition' if include_nutrition else 'basic'}"
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            current = db.execute("SELECT value FROM metadata WHERE key='fingerprint'").fetchone()
            if current and current[0] == fingerprint:
                return
            db.executescript("DROP TABLE IF EXISTS foods; DROP TABLE IF EXISTS nutrition; CREATE TABLE foods (fdc_id TEXT PRIMARY KEY, description TEXT, category TEXT, publication_date TEXT); CREATE TABLE nutrition (fdc_id TEXT PRIMARY KEY, value TEXT NOT NULL);")
            self._build(db, include_nutrition=include_nutrition)
            db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('fingerprint',?)", (fingerprint,))
            db.commit()

    def _build(self, db: sqlite3.Connection, *, include_nutrition: bool) -> None:
        with zipfile.ZipFile(self.archive_path) as archive:
            category_member = _optional_member(archive, "food_category.csv")
            categories = {}
            if category_member:
                categories = {row.get("id", ""): row.get("description", "") for row in _csv_rows(archive, "food_category.csv")}
            food_batch = []
            for row in _csv_rows(archive, "food.csv"):
                food_batch.append((row.get("fdc_id"), row.get("description"), categories.get(row.get("food_category_id", "")), row.get("publication_date")))
                if len(food_batch) >= 10_000:
                    db.executemany("INSERT OR REPLACE INTO foods VALUES(?,?,?,?)", food_batch); food_batch.clear()
            if food_batch:
                db.executemany("INSERT OR REPLACE INTO foods VALUES(?,?,?,?)", food_batch)
            if not include_nutrition:
                db.commit()
                return
            nutrient_map = {}
            for row in _csv_rows(archive, "nutrient.csv"):
                if row.get("name") in self.NUTRIENTS:
                    nutrient_map[row.get("id")] = (self.NUTRIENTS[row["name"]], row.get("unit_name"))
            current_id = None
            values: dict[str, dict[str, str]] = {}
            nutrition_batch = []
            for row in _csv_rows(archive, "food_nutrient.csv"):
                mapped = nutrient_map.get(row.get("nutrient_id"))
                if not mapped or not row.get("amount"):
                    continue
                fdc_id = row.get("fdc_id")
                if current_id is not None and fdc_id != current_id:
                    nutrition_batch.append((current_id, json.dumps(values, separators=(",", ":"))))
                    values = {}
                    if len(nutrition_batch) >= 10_000:
                        db.executemany("INSERT OR REPLACE INTO nutrition VALUES(?,?)", nutrition_batch); nutrition_batch.clear()
                current_id = fdc_id
                key, unit = mapped
                values[key] = {"value": row["amount"], "unit": unit or ""}
            if current_id is not None:
                nutrition_batch.append((current_id, json.dumps(values, separators=(",", ":"))))
            if nutrition_batch:
                db.executemany("INSERT OR REPLACE INTO nutrition VALUES(?,?)", nutrition_batch)
            db.commit()

    def lookup(self, fdc_id: str) -> dict:
        with sqlite3.connect(self.path) as db:
            food = db.execute("SELECT description,category,publication_date FROM foods WHERE fdc_id=?", (fdc_id,)).fetchone()
            nutrition = db.execute("SELECT value FROM nutrition WHERE fdc_id=?", (fdc_id,)).fetchone()
        return {
            "description": food[0] if food else None,
            "category": food[1] if food else None,
            "publication_date": food[2] if food else None,
            "nutrition": json.loads(nutrition[0]) if nutrition else {},
        }
