import argparse
import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.barcodes import BarcodeError, parse_barcode
from app.db import SessionLocal
from app.models import Product
from app.services import lookup_product


@dataclass(frozen=True, slots=True)
class BenchmarkRow:
    barcode: str
    expected_product_name: str = ""
    expected_brand: str = ""
    country: str = ""
    category: str = ""
    retailer_or_source: str = ""
    notes: str = ""


@dataclass(slots=True)
class BenchmarkInputStats:
    input_rows: int = 0
    accepted_rows: int = 0
    duplicate_rows: int = 0
    malformed_rows: int = 0
    missing_optional_metadata: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class BenchmarkLoadResult:
    rows: list[BenchmarkRow]
    stats: BenchmarkInputStats


@dataclass(slots=True)
class Breakdown:
    tested: int = 0
    found: int = 0

    @property
    def hit_rate(self) -> float:
        return percent(self.found, self.tested)


COMPLETENESS_FIELDS = (
    "name",
    "brand",
    "category",
    "image",
    "quantity",
    "ingredients",
    "allergens",
    "nutrition",
    "country",
)


def percent(numerator: int, denominator: int) -> float:
    return round(numerator * 100 / denominator, 2) if denominator else 0.0


@dataclass(slots=True)
class CoverageStats:
    total: int = 0
    valid: int = 0
    found: int = 0
    unsupported: int = 0
    completeness_counts: dict[str, int] = field(
        default_factory=lambda: {key: 0 for key in COMPLETENESS_FIELDS}
    )
    breakdowns: dict[str, dict[str, Breakdown]] = field(
        default_factory=lambda: {
            "country": {},
            "category": {},
            "barcode_type": {},
        }
    )
    database_products: int = 0
    independent_benchmark: bool = False

    @property
    def invalid(self) -> int:
        return self.total - self.valid

    @property
    def not_found(self) -> int:
        return self.valid - self.found

    @staticmethod
    def percent(numerator: int, denominator: int) -> float:
        return percent(numerator, denominator)

    # Compatibility properties retained for callers of the original utility.
    @property
    def name_complete(self) -> int:
        return self.completeness_counts["name"]

    @property
    def brand_complete(self) -> int:
        return self.completeness_counts["brand"]

    @property
    def image_complete(self) -> int:
        return self.completeness_counts["image"]

    @property
    def category_complete(self) -> int:
        return self.completeness_counts["category"]

    @property
    def nutrition_complete(self) -> int:
        return self.completeness_counts["nutrition"]

    def completeness(self) -> dict[str, float]:
        return {
            key: percent(value, self.found)
            for key, value in self.completeness_counts.items()
        }

    def summary(self) -> dict[str, Any]:
        return {
            "database_products": self.database_products,
            "benchmark_products": self.total,
            "valid_barcodes": self.valid,
            "invalid_barcodes": self.invalid,
            "unsupported_barcodes": self.unsupported,
            "found": self.found,
            "not_found": self.not_found,
            "hit_rate": percent(self.found, self.valid),
            "readiness_signal": self.readiness_signal(),
            "completeness": self.completeness(),
            "breakdowns": {
                dimension: {
                    label: {
                        "tested": result.tested,
                        "found": result.found,
                        "hit_rate": result.hit_rate,
                    }
                    for label, result in sorted(values.items())
                }
                for dimension, values in self.breakdowns.items()
            },
        }

    def readiness_signal(self) -> str:
        if not self.independent_benchmark:
            return "commercial coverage cannot yet be classified"
        hit_rate = percent(self.found, self.valid)
        if hit_rate >= 80:
            return "strong signal to proceed; review field completeness"
        if hit_rate >= 60:
            return "potentially viable; investigate missing coverage and fields"
        return "coverage needs improvement before commercial launch"

    def report(self) -> str:
        lines = [
            f"Database products: {self.database_products:,}",
            f"Total barcodes: {self.total:,}",
            f"Valid barcodes: {self.valid:,}",
            f"Invalid barcodes: {self.invalid:,}",
            f"Unsupported barcode formats: {self.unsupported:,}",
            f"Products found: {self.found:,}",
            f"Products not found: {self.not_found:,}",
            f"Overall hit rate: {percent(self.found, self.valid):.2f}%",
            f"Readiness signal: {self.readiness_signal()}",
            "",
            "Field completeness among found products:",
        ]
        for key, value in self.completeness().items():
            lines.append(f"  {key}: {value:.2f}%")
        for dimension, values in self.breakdowns.items():
            if values:
                lines.extend(["", f"Hit rate by {dimension}:"])
                for label, result in sorted(values.items()):
                    lines.append(
                        f"  {label}: {result.found}/{result.tested} "
                        f"({result.hit_rate:.2f}%)"
                    )
        return "\n".join(lines)


def load_benchmark(path: Path) -> BenchmarkLoadResult:
    optional_fields = (
        "expected_product_name",
        "expected_brand",
        "country",
        "category",
        "retailer_or_source",
        "notes",
    )
    stats = BenchmarkInputStats(
        missing_optional_metadata={name: 0 for name in optional_fields}
    )
    with path.open(encoding="utf-8-sig", newline="") as source:
        sample = source.read(4096)
        source.seek(0)
        first_line = sample.splitlines()[0] if sample.splitlines() else ""
        is_csv = "," in first_line
        if not is_csv:
            raw_rows = [
                {"barcode": line.strip()} for line in source if line.strip()
            ]
        else:
            reader = csv.DictReader(source)
            normalized_headers = {
                name.strip().casefold() for name in (reader.fieldnames or []) if name
            }
            if "barcode" not in normalized_headers:
                source.seek(0)
                raw_rows = [
                    {"barcode": row[0].strip()}
                    for row in csv.reader(source)
                    if row and row[0].strip()
                ]
            else:
                raw_rows = list(reader)
    rows: list[BenchmarkRow] = []
    seen: set[str] = set()
    for raw in raw_rows:
        stats.input_rows += 1
        if None in raw or not isinstance(raw, dict):
            stats.malformed_rows += 1
            continue
        normalized = {
            (key or "").strip().casefold(): (value or "").strip()
            for key, value in raw.items()
        }
        barcode = normalized.get("barcode", "")
        if not barcode:
            stats.malformed_rows += 1
            continue
        try:
            identity = parse_barcode(barcode).gtin14
        except BarcodeError:
            identity = f"raw:{barcode}"
        if identity in seen:
            stats.duplicate_rows += 1
            continue
        seen.add(identity)
        row = BenchmarkRow(
            barcode=barcode,
            expected_product_name=normalized.get("expected_product_name", ""),
            expected_brand=normalized.get("expected_brand", ""),
            country=normalized.get("country", ""),
            category=normalized.get("category", ""),
            retailer_or_source=normalized.get(
                "retailer_or_source", normalized.get("source_of_barcode", "")
            ),
            notes=normalized.get("notes", ""),
        )
        rows.append(row)
        for name in optional_fields:
            if not getattr(row, name):
                stats.missing_optional_metadata[name] += 1
    stats.accepted_rows = len(rows)
    return BenchmarkLoadResult(rows=rows, stats=stats)


def read_benchmark(path: Path) -> list[BenchmarkRow]:
    return load_benchmark(path).rows


def read_barcodes(path: Path) -> list[str]:
    return [row.barcode for row in read_benchmark(path)]


def _record_breakdown(
    stats: CoverageStats, dimension: str, label: str, found: bool
) -> None:
    if not label:
        return
    result = stats.breakdowns[dimension].setdefault(label, Breakdown())
    result.tested += 1
    result.found += found


def calculate_coverage(
    session: Session,
    inputs: list[str] | list[BenchmarkRow],
    *,
    independent_benchmark: bool = False,
) -> tuple[CoverageStats, list[dict[str, str]]]:
    benchmark = [
        item if isinstance(item, BenchmarkRow) else BenchmarkRow(barcode=item)
        for item in inputs
    ]
    stats = CoverageStats(
        total=len(benchmark),
        database_products=session.scalar(select(func.count()).select_from(Product)) or 0,
        independent_benchmark=independent_benchmark,
    )
    rows: list[dict[str, str]] = []
    for entry in benchmark:
        try:
            parsed = parse_barcode(entry.barcode)
        except BarcodeError as exc:
            if "8, 12, 13, or 14" in str(exc):
                stats.unsupported += 1
            rows.append(
                {
                    **asdict(entry),
                    "barcode_type": "",
                    "canonical_gtin": "",
                    "valid": "false",
                    "found": "false",
                    "actual_product_name": "",
                    "actual_brand": "",
                    "error": str(exc),
                }
            )
            continue
        stats.valid += 1
        result = lookup_product(session, parsed.value)
        product = result.product
        is_found = result.found and product is not None
        if is_found and product is not None:
            stats.found += 1
            present = {
                "name": bool(product.name.strip()),
                "brand": bool(product.brand and product.brand.strip()),
                "category": bool(product.categories),
                "image": bool(product.image_url),
                "quantity": bool(product.quantity),
                "ingredients": bool(product.ingredients),
                "allergens": bool(product.allergens),
                "nutrition": bool(product.nutrition),
                "country": bool(product.countries),
            }
            for field_name, has_value in present.items():
                stats.completeness_counts[field_name] += has_value
        _record_breakdown(stats, "country", entry.country, is_found)
        _record_breakdown(stats, "category", entry.category, is_found)
        _record_breakdown(stats, "barcode_type", parsed.barcode_type, is_found)
        rows.append(
            {
                **asdict(entry),
                "barcode_type": parsed.barcode_type,
                "canonical_gtin": parsed.gtin14,
                "valid": "true",
                "found": str(is_found).lower(),
                "actual_product_name": product.name if product else "",
                "actual_brand": product.brand or "" if product else "",
                "error": "",
            }
        )
    return stats, rows


def write_report(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else [
        "barcode",
        "valid",
        "found",
        "actual_product_name",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json_summary(path: Path, stats: CoverageStats) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(stats.summary(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure independent real-world barcode coverage"
    )
    parser.add_argument("benchmark_file", type=Path)
    parser.add_argument("--output", type=Path, help="Optional per-barcode CSV report")
    parser.add_argument("--json-output", type=Path, help="Optional JSON run summary")
    parser.add_argument(
        "--independent",
        action="store_true",
        help="Confirm rows were collected independently of this product database",
    )
    args = parser.parse_args()
    loaded = load_benchmark(args.benchmark_file)
    benchmark = loaded.rows
    print(f"Benchmark input rows: {loaded.stats.input_rows:,}")
    print(f"Accepted unique rows: {loaded.stats.accepted_rows:,}")
    print(f"Duplicate rows ignored: {loaded.stats.duplicate_rows:,}")
    print(f"Malformed rows ignored: {loaded.stats.malformed_rows:,}")
    missing = ", ".join(
        f"{name}={count:,}"
        for name, count in loaded.stats.missing_optional_metadata.items()
    )
    print(f"Missing optional metadata: {missing}")
    with SessionLocal() as session:
        stats, rows = calculate_coverage(
            session,
            benchmark,
            independent_benchmark=args.independent,
        )
    print(stats.report())
    if args.output:
        write_report(args.output, rows)
        print(f"CSV report: {args.output}")
    if args.json_output:
        write_json_summary(args.json_output, stats)
        print(f"JSON summary: {args.json_output}")


if __name__ == "__main__":
    main()
