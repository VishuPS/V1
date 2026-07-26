import argparse
from pathlib import Path

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Product


REQUIRED_INDEXES = {
    "ix_products_barcode_type",
    "ix_products_source_identity",
}


def database_size_bytes(session: Session) -> int | None:
    if session.bind is None:
        return None
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        return int(
            session.scalar(text("SELECT pg_database_size(current_database())")) or 0
        )
    if dialect == "sqlite":
        path = session.bind.url.database
        if path and path != ":memory:":
            file_path = Path(path)
            if file_path.exists():
                return file_path.stat().st_size
    return None


def inspect_database(session: Session) -> dict[str, object]:
    if session.bind is None:
        raise RuntimeError("Session is not bound to an engine")
    session.execute(text("SELECT 1"))
    inspector = inspect(session.bind)
    tables = set(inspector.get_table_names())
    indexes = (
        {item["name"] for item in inspector.get_indexes("products")}
        if "products" in tables
        else set()
    )
    primary_key = (
        inspector.get_pk_constraint("products").get("constrained_columns", [])
        if "products" in tables
        else []
    )
    version: str | None = None
    if "alembic_version" in tables:
        version = session.scalar(text("SELECT version_num FROM alembic_version"))
    product_count = (
        session.scalar(select(func.count()).select_from(Product))
        if "products" in tables
        else 0
    )
    noncanonical = (
        session.scalar(
            select(func.count())
            .select_from(Product)
            .where(func.length(Product.barcode) != 14)
        )
        if "products" in tables
        else 0
    )
    return {
        "engine": session.bind.dialect.name,
        "connected": True,
        "schema_version": version,
        "product_count": product_count or 0,
        "database_size_bytes": database_size_bytes(session),
        "barcode_primary_key": primary_key == ["barcode"],
        "canonical_gtin_rows": (product_count or 0) - (noncanonical or 0),
        "noncanonical_gtin_rows": noncanonical or 0,
        "indexes": sorted(indexes),
        "missing_indexes": sorted(REQUIRED_INDEXES - indexes),
    }


def format_size(size: int | None) -> str:
    if size is None:
        return "unavailable"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "unavailable"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check database connectivity, schema, indexes, and product count"
    )
    parser.parse_args()
    try:
        with SessionLocal() as session:
            result = inspect_database(session)
    except SQLAlchemyError as exc:
        raise SystemExit(f"Database check failed: {type(exc).__name__}") from exc
    print(f"Engine: {result['engine']}")
    print("Connectivity: ok")
    print(f"Schema version: {result['schema_version'] or 'not stamped'}")
    print(f"Products: {result['product_count']:,}")
    print(f"Database size: {format_size(result['database_size_bytes'])}")
    print(f"Barcode primary key: {'ok' if result['barcode_primary_key'] else 'missing'}")
    print(f"Canonical GTIN rows: {result['canonical_gtin_rows']:,}")
    print(f"Noncanonical GTIN rows: {result['noncanonical_gtin_rows']:,}")
    print(f"Indexes: {', '.join(result['indexes']) or 'none'}")
    print(f"Missing required indexes: {', '.join(result['missing_indexes']) or 'none'}")


if __name__ == "__main__":
    main()
