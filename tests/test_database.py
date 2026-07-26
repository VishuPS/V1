from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.config import get_settings
from app.models import Product
from app.tools.db_check import inspect_database


def test_product_model_compiles_for_postgresql() -> None:
    ddl = str(CreateTable(Product.__table__).compile(dialect=postgresql.dialect()))
    assert "PRIMARY KEY (barcode)" in ddl
    assert "JSON" in ddl
    assert "TIMESTAMP WITH TIME ZONE" in ddl


def test_initial_migration_builds_current_schema(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    inspector = inspect(create_engine(f"sqlite:///{database.as_posix()}"))
    assert "products" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("products")}
    assert {"barcode", "source", "source_id", "created_at", "updated_at"} <= columns
    indexes = {index["name"] for index in inspector.get_indexes("products")}
    assert "ix_products_barcode_type" in indexes
    assert "ix_products_source_identity" in indexes
    get_settings.cache_clear()


def test_database_diagnostic(session_factory) -> None:
    with session_factory() as session:
        result = inspect_database(session)
    assert result["engine"] == "sqlite"
    assert result["connected"] is True
    assert result["product_count"] == 2
    assert result["barcode_primary_key"] is True
    assert result["missing_indexes"] == []
