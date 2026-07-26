import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal
from app.models import Product
from app.tools.coverage import percent


QUALITY_FIELDS = (
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


@dataclass(slots=True)
class QualityStats:
    total_products: int = 0
    complete: Counter[str] = field(default_factory=Counter)
    barcode_types: Counter[str] = field(default_factory=Counter)
    countries: Counter[str] = field(default_factory=Counter)
    categories: Counter[str] = field(default_factory=Counter)

    def completeness(self) -> dict[str, float]:
        return {
            name: percent(self.complete[name], self.total_products)
            for name in QUALITY_FIELDS
        }

    def summary(self, top: int = 20) -> dict[str, Any]:
        return {
            "total_products": self.total_products,
            "completeness": self.completeness(),
            "barcode_types": dict(self.barcode_types.most_common()),
            "top_countries": dict(self.countries.most_common(top)),
            "top_categories": dict(self.categories.most_common(top)),
        }

    def report(self, top: int = 20) -> str:
        lines = [f"Total products: {self.total_products:,}", "", "Completeness:"]
        for name, value in self.completeness().items():
            lines.append(f"  With {name}: {value:.2f}%")
        lines.extend(["", "Products by source barcode type:"])
        for name, count in self.barcode_types.most_common():
            lines.append(f"  {name}: {count:,}")
        if self.countries:
            lines.extend(["", f"Top {top} country/market tags:"])
            lines.extend(
                f"  {name}: {count:,}" for name, count in self.countries.most_common(top)
            )
        if self.categories:
            lines.extend(["", f"Top {top} category tags:"])
            lines.extend(
                f"  {name}: {count:,}" for name, count in self.categories.most_common(top)
            )
        return "\n".join(lines)


def calculate_quality(
    batch_size: int = 1_000,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> QualityStats:
    stats = QualityStats()
    with session_factory() as session:
        stats.total_products = (
            session.scalar(select(func.count()).select_from(Product)) or 0
        )
        products = session.scalars(
            select(Product).execution_options(yield_per=batch_size)
        )
        for product in products:
            values = {
                "name": bool(product.name and product.name.strip()),
                "brand": bool(product.brand and product.brand.strip()),
                "category": bool(product.categories),
                "image": bool(product.image_url),
                "quantity": bool(product.quantity),
                "ingredients": bool(product.ingredients),
                "allergens": bool(product.allergens),
                "nutrition": bool(product.nutrition),
                "country": bool(product.countries),
            }
            stats.complete.update(name for name, present in values.items() if present)
            stats.barcode_types[product.barcode_type] += 1
            stats.countries.update(product.countries)
            stats.categories.update(product.categories)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Report imported database quality")
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    stats = calculate_quality(args.batch_size)
    print(stats.report(args.top))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(stats.summary(args.top), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"JSON summary: {args.json_output}")


if __name__ == "__main__":
    main()
