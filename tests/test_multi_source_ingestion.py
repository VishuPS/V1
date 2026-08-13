import csv
import gzip
import io
import json
import zipfile
from pathlib import Path

from sqlalchemy import func, select

from app.identifiers import normalize_isbn
from app.ingestion.multi_source import MappedSourceProduct, import_mapped_records
from app.ingestion.open_facts_adapters import OPEN_FACTS_SOURCES, OpenFactsAdapter
from app.ingestion.usda import USDAFoodDataCentralAdapter, validate_archive
from app.models import Product, ProductSourceRecord, ProductSourceSync
from app.services import lookup_product
from app.tools.product_sources import source_coverage


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


def open_facts_fixture(path: Path, records: list[dict]) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record) + "\n")
    return path


def mapped_open_facts(tmp_path: Path, key: str, record: dict) -> tuple[OpenFactsAdapter, list[MappedSourceProduct]]:
    adapter = OpenFactsAdapter(
        open_facts_fixture(tmp_path / f"{key}.jsonl.gz", [record]),
        OPEN_FACTS_SOURCES[key],
    )
    return adapter, list(adapter.records())


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


def test_open_beauty_facts_mapping_preserves_metadata_without_canonical_image(tmp_path: Path) -> None:
    adapter, records = mapped_open_facts(tmp_path, "beauty", {
        "code": "012345678905",
        "product_name": "Gentle cleanser",
        "generic_name": "Face wash",
        "brands": "Example Beauty",
        "categories_tags": ["en:facial-cleansers"],
        "quantity": "200 ml",
        "ingredients_text": "Aqua, glycerin",
        "labels_tags": ["en:vegan"],
        "packaging_text": "Recycled bottle",
        "countries_tags": ["en:france"],
        "image_front_url": "https://images.openbeautyfacts.org/example.jpg",
        "nutriments": {"energy-kcal_100g": 20},
        "last_modified_t": 1700000000,
    })
    assert adapter.records_seen == 1
    assert len(records) == 1
    record = records[0]
    assert (record.canonical_gtin, record.source, record.license) == (
        "00012345678905", "OPEN_BEAUTY_FACTS", "ODbL-1.0"
    )
    assert record.name == "Gentle cleanser"
    assert record.image_url is None
    assert record.nutrition == {}
    assert record.source_metadata["generic_name"] == "Face wash"
    assert record.source_metadata["labels"] == ["en:vegan"]
    assert record.source_metadata["source_image_url"].startswith("https://")


def test_open_pet_food_facts_mapping_includes_supported_nutrition(tmp_path: Path) -> None:
    _, records = mapped_open_facts(tmp_path, "pet", {
        "_id": "012345678905",
        "generic_name": "Complete dog food",
        "brands": "Example Pet",
        "ingredients_text": "Chicken, rice",
        "nutriments": {"proteins_100g": 24.5},
        "countries": "United States, Canada",
    })
    record = records[0]
    assert record.source == "OPEN_PET_FOOD_FACTS"
    assert record.name == "Complete dog food"
    assert record.categories == ["Pet food"]
    assert record.nutrition == {"proteins_100g": 24.5}
    assert record.countries == ["United States", "Canada"]


def test_open_products_facts_sparse_record_and_invalid_gtin(tmp_path: Path) -> None:
    path = open_facts_fixture(tmp_path / "products.jsonl.gz", [
        {"code": "012345678905", "brands": "Useful Brand", "labels_tags": "en:reusable"},
        {"code": "123", "product_name": "Invalid barcode"},
        {"code": "4006381333931"},
    ])
    adapter = OpenFactsAdapter(path, OPEN_FACTS_SOURCES["products"])
    records = list(adapter.records())
    assert len(records) == 1
    assert records[0].name == "Useful Brand"
    assert records[0].categories == ["General products"]
    assert records[0].source_metadata["labels"] == ["en:reusable"]
    assert adapter.records_seen == 3
    assert adapter.invalid_barcodes == 1
    assert adapter.skipped == 1


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


def test_open_facts_sources_deduplicate_gtin_and_keep_provenance(tmp_path: Path, session_factory) -> None:
    for key, name in (("beauty", "Cleanser"), ("products", "General listing")):
        adapter, records = mapped_open_facts(
            tmp_path, key, {"code": "012345678905", "product_name": name}
        )
        import_mapped_records(
            records,
            session_factory,
            source=adapter.definition.source,
            dataset_url=None,
            dataset_fingerprint=f"{key}-fixture",
        )
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(Product).where(
                Product.barcode == "00012345678905"
            )
        ) == 1
        sources = set(session.scalars(
            select(ProductSourceRecord.source).where(
                ProductSourceRecord.product_barcode == "00012345678905"
            )
        ))
        assert sources == {"OPEN_BEAUTY_FACTS", "OPEN_PRODUCTS_FACTS"}


def test_open_facts_reimport_is_idempotent_and_resume_uses_checkpoint(tmp_path: Path, session_factory) -> None:
    definition = OPEN_FACTS_SOURCES["pet"]
    rows = [
        {"code": "012345678905", "product_name": "Pet one"},
        {"code": "4006381333931", "product_name": "Pet two"},
    ]
    path = open_facts_fixture(tmp_path / "pet-resume.jsonl.gz", rows)
    first_adapter = OpenFactsAdapter(path, definition)
    first = import_mapped_records(
        first_adapter.records(), session_factory, source=definition.source,
        dataset_url=None, dataset_fingerprint="pet-resume", limit=1, batch_size=1,
    )
    resumed_adapter = OpenFactsAdapter(path, definition)
    resumed = import_mapped_records(
        resumed_adapter.records(), session_factory, source=definition.source,
        dataset_url=None, dataset_fingerprint="pet-resume", resume=True, batch_size=1,
    )
    repeat_adapter = OpenFactsAdapter(path, definition)
    repeat = import_mapped_records(
        repeat_adapter.records(), session_factory, source=definition.source,
        dataset_url=None, dataset_fingerprint="pet-repeat", batch_size=1,
    )
    with session_factory() as session:
        imported_gtins = {"00012345678905", "04006381333931"}
        assert session.scalar(
            select(func.count()).select_from(Product).where(Product.barcode.in_(imported_gtins))
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(ProductSourceRecord).where(
                ProductSourceRecord.source == definition.source,
                ProductSourceRecord.product_barcode.in_(imported_gtins),
            )
        ) == 2
        sync = session.scalar(select(ProductSourceSync).where(ProductSourceSync.dataset_fingerprint == "pet-resume"))
        assert sync and sync.checkpoint_record == 2 and sync.status == "completed"
    assert first.processed == 1
    assert resumed.processed == 1
    assert repeat.inserted == 0
    assert repeat.provenance_updated == 2


def test_dry_run_does_not_write(session_factory) -> None:
    stats = import_mapped_records([mapped("USDA_FDC", "fdc-dry")], session_factory, source="USDA_FDC", dataset_url=None, dataset_fingerprint="dry", dry_run=True)
    with session_factory() as session:
        assert session.get(Product, "00012345678905") is None
    assert stats.processed == 1


def test_isbn_normalization_is_isolated_from_gtin_api() -> None:
    assert normalize_isbn("0-306-40615-2") == ("9780306406157", "09780306406157")
    assert normalize_isbn("9780306406157") == ("9780306406157", "09780306406157")


def test_coverage_lists_all_supported_sources(session_factory) -> None:
    with session_factory() as session:
        sources = {item["source"]: item for item in source_coverage(session)["sources"]}
    assert {
        "OPEN_FOOD_FACTS",
        "USDA_FDC",
        "OPEN_BEAUTY_FACTS",
        "OPEN_PET_FOOD_FACTS",
        "OPEN_PRODUCTS_FACTS",
    }.issubset(sources)
    assert sources["OPEN_BEAUTY_FACTS"]["source_records"] == 0
