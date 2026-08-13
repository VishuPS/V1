import csv
import io
import zipfile
from pathlib import Path

from sqlalchemy import func, select

from app.identifiers import normalize_isbn
from app.ingestion.multi_source import MappedSourceProduct, import_mapped_records
from app.ingestion.usda import USDAFoodDataCentralAdapter, validate_archive
from app.models import Product, ProductSourceRecord
from app.services import lookup_product


def mapped(source: str, source_id: str, *, brand: str | None = None, ingredients: str | None = None):
    return MappedSourceProduct(
        canonical_gtin="00012345678905", barcode_type="UPC-A",
        name="Original product", brand=brand, ingredients=ingredients,
        categories=["Food"], source=source, source_product_id=source_id,
        source_gtin="012345678905", source_url=f"https://example.test/{source_id}",
        license="CC0-1.0" if source == "USDA_FDC" else "ODbL-1.0",
    )


def usda_fixture(path: Path) -> Path:
    headers = [
        "fdc_id", "brand_owner", "brand_name", "subbrand_name", "gtin_upc",
        "ingredients", "serving_size", "serving_size_unit",
        "household_serving_fulltext", "branded_food_category", "data_source",
        "package_weight", "modified_date", "available_date", "market_country",
        "short_description",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerow({
        "fdc_id": "12345", "brand_owner": "Example Foods", "brand_name": "Example",
        "gtin_upc": "012345678905", "ingredients": "Water, oats",
        "serving_size": "30", "serving_size_unit": "g",
        "branded_food_category": "Cereal", "data_source": "LI",
        "package_weight": "300 g", "modified_date": "2026-04-01",
        "market_country": "United States", "short_description": "Oat cereal",
    })
    writer.writerow({"fdc_id": "bad", "gtin_upc": "123", "short_description": "Bad"})
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("FoodData_Central/branded_food.csv", output.getvalue())
        archive.writestr("FoodData_Central/food.csv", "fdc_id,data_type,description,food_category_id,publication_date\n12345,branded_food,Oat cereal,1,2026-04-01\n")
        archive.writestr("FoodData_Central/food_category.csv", "id,code,description\n1,1,Cereal\n")
        archive.writestr("padding.bin", bytes(range(256)) * 8)
    return path


def test_usda_mapping_and_invalid_barcode_rejection(tmp_path: Path) -> None:
    archive = usda_fixture(tmp_path / "usda.zip")
    validate_archive(archive)
    adapter = USDAFoodDataCentralAdapter(archive)
    records = list(adapter.records())
    assert len(records) == 1
    record = records[0]
    assert (record.canonical_gtin, record.source, record.license) == (
        "00012345678905", "USDA_FDC", "CC0-1.0"
    )
    assert (record.name, record.brand, record.quantity) == ("Oat cereal", "Example", "300 g")
    assert record.ingredients == "Water, oats"
    assert adapter.invalid_barcodes == 1


def test_two_sources_share_one_product_and_import_is_idempotent(session_factory) -> None:
    records = [mapped("OPEN_FOOD_FACTS", "off-1", brand="Original Brand")]
    import_mapped_records(records, session_factory, source="OPEN_FOOD_FACTS", dataset_url=None, dataset_fingerprint="off-fixture")
    usda = [mapped("USDA_FDC", "fdc-1", brand="Different Brand", ingredients="Water")]
    first = import_mapped_records(usda, session_factory, source="USDA_FDC", dataset_url=None, dataset_fingerprint="usda-fixture")
    second = import_mapped_records(usda, session_factory, source="USDA_FDC", dataset_url=None, dataset_fingerprint="usda-fixture")
    with session_factory() as session:
        product = session.get(Product, "00012345678905")
        assert session.scalar(select(func.count()).select_from(Product).where(Product.barcode == product.barcode)) == 1
        assert session.scalar(select(func.count()).select_from(ProductSourceRecord).where(ProductSourceRecord.product_barcode == product.barcode)) == 2
        assert product.brand == "Original Brand"
        assert product.ingredients == "Water"
        assert lookup_product(session, "012345678905").found is True
    assert first.enriched == 1
    assert second.inserted == 0
    assert second.provenance_updated == 1


def test_dry_run_does_not_write(session_factory) -> None:
    stats = import_mapped_records([mapped("USDA_FDC", "fdc-dry")], session_factory, source="USDA_FDC", dataset_url=None, dataset_fingerprint="dry", dry_run=True)
    with session_factory() as session:
        assert session.get(Product, "00012345678905") is None
    assert stats.processed == 1


def test_isbn_normalization_is_isolated_from_gtin_api() -> None:
    assert normalize_isbn("0-306-40615-2") == ("9780306406157", "09780306406157")
    assert normalize_isbn("9780306406157") == ("9780306406157", "09780306406157")
