from __future__ import annotations

import argparse
import json

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Product, ProductSourceRecord
from app.tools.db_check import database_size_bytes, format_size


def source_coverage(session: Session) -> dict:
    total_products = session.scalar(select(func.count()).select_from(Product)) or 0
    total_gtins = session.scalar(select(func.count(distinct(Product.barcode)))) or 0
    rows = session.execute(
        select(
            ProductSourceRecord.source,
            func.count(ProductSourceRecord.id),
            func.count(distinct(ProductSourceRecord.product_barcode)),
        ).group_by(ProductSourceRecord.source).order_by(ProductSourceRecord.source)
    ).all()
    product_source_counts = (
        select(ProductSourceRecord.product_barcode, func.count(distinct(ProductSourceRecord.source)).label("source_count"))
        .group_by(ProductSourceRecord.product_barcode).subquery()
    )
    overlap = session.scalar(select(func.count()).select_from(product_source_counts).where(product_source_counts.c.source_count > 1)) or 0
    by_source = []
    for source, records, gtins in rows:
        unique = session.scalar(
            select(func.count(distinct(ProductSourceRecord.product_barcode))).select_from(product_source_counts.join(
                ProductSourceRecord,
                ProductSourceRecord.product_barcode == product_source_counts.c.product_barcode,
            )).where(ProductSourceRecord.source == source, product_source_counts.c.source_count == 1)
        ) or 0
        by_source.append({"source": source, "source_records": records, "gtins": gtins, "unique_gtins": unique})
    return {
        "total_canonical_products": total_products,
        "total_gtins": total_gtins,
        "gtins_with_multiple_sources": overlap,
        "database_size_bytes": database_size_bytes(session),
        "sources": by_source,
    }


def source_coverage_report(session: Session) -> str:
    result = source_coverage(session)
    lines = [
        "Product source coverage",
        f"Total canonical products: {result['total_canonical_products']:,}",
        f"Total GTINs: {result['total_gtins']:,}",
        f"GTINs with multiple sources: {result['gtins_with_multiple_sources']:,}",
        f"Approximate database size: {format_size(result['database_size_bytes'])}",
    ]
    for item in result["sources"]:
        lines.append(
            f"{item['source']}: {item['source_records']:,} source records; "
            f"{item['gtins']:,} GTINs; {item['unique_gtins']:,} unique GTINs"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Report BarcodeNest canonical product coverage by source")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as session:
        result = source_coverage(session)
        print(json.dumps(result, indent=2, default=str) if args.json else source_coverage_report(session))


if __name__ == "__main__":
    main()
