import gzip
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion.open_food_facts import (
    ImportStats,
    OpenFoodFactsSource,
    download_dataset,
    import_dataset,
    normalize_record,
    select_image_url,
    update_images_only,
)
from app.ingestion import open_food_facts as ingestion_module
from app.models import Product
from app.tools.data_quality import calculate_quality


SAMPLE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "sample_openfoodfacts.json"
)


def test_sample_data_is_streamed_and_normalized() -> None:
    records = list(OpenFoodFactsSource(SAMPLE_PATH).records())
    product = normalize_record(records[0], ImportStats())
    assert len(records) == 2
    assert product is not None
    assert product.barcode == "03017620422003"
    assert product.name == "Nutella"
    assert product.source_id == "3017620422003"
    assert product.source_updated_at is not None


def test_field_normalization_and_missing_values() -> None:
    stats = ImportStats()
    product = normalize_record(
        {
            "code": "3017620422003",
            "generic_name": "  Hazelnut   spread ",
            "brands": " Ferrero ",
            "categories_tags": ["en:spreads", "en:spreads", None, " "],
            "countries": "France, France, Portugal",
            "allergens_tags": None,
            "nutriments": {"fat": 12.5, "unknown": None, "nested": {"x": 1}},
            "last_modified_t": "not-a-date",
        },
        stats,
    )
    assert product is not None
    assert product.name == "Hazelnut spread"
    assert product.categories == ["en:spreads"]
    assert product.countries == ["France", "Portugal"]
    assert product.allergens == []
    assert product.nutrition == {"fat": 12.5}
    assert product.source_updated_at is None


def test_long_real_world_fields_are_preserved_without_truncation(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    long_name = "Imported multilingual product name " * 40
    long_brand = "Brand and manufacturer portfolio " * 40
    long_quantity = "12 packs × 6 units, promotional description; " * 20
    long_url = "https://images.openfoodfacts.org/" + "nested-path/" * 400 + "front.jpg"
    long_ingredients = "Sugar, flour, cocoa butter, emulsifier, natural flavouring. " * 500
    record = {
        "code": "4006381333931",
        "product_name": long_name,
        "brands": long_brand,
        "quantity": long_quantity,
        "image_url": long_url,
        "ingredients_text": long_ingredients,
    }
    path = tmp_path / "long-fields.json"
    path.write_text(json.dumps([record]), encoding="utf-8")

    first = import_dataset(
        OpenFoodFactsSource(path),
        session_factory,
        batch_size=1,
        progress_every=0,
    )
    second = import_dataset(
        OpenFoodFactsSource(path),
        session_factory,
        batch_size=1,
        progress_every=0,
    )
    assert first.inserted == 1
    assert second.updated == 1
    with session_factory() as session:
        product = session.get(Product, "04006381333931")
        assert product is not None
        assert product.name == " ".join(long_name.split())
        assert product.brand == " ".join(long_brand.split())
        assert product.quantity == " ".join(long_quantity.split())
        assert product.image_url == long_url
        assert product.ingredients == " ".join(long_ingredients.split())
        assert session.scalar(select(func.count()).select_from(Product)) == 3


def nutella_image_record() -> dict:
    return {
        "_id": "3017620422003",
        "code": "3017620422003",
        "lang": "fr",
        "product_name": "Nutella",
        "images": {
            "selected": {
                "front": {
                    "en": {
                        "imgid": 180,
                        "rev": 879,
                        "sizes": {
                            "100": {"w": 100, "h": 92},
                            "400": {"w": 400, "h": 367},
                            "full": {"w": 1308, "h": 1200},
                        },
                    },
                    "fr": {
                        "imgid": 150,
                        "rev": 911,
                        "sizes": {
                            "100": {"w": 66, "h": 100},
                            "400": {"w": 263, "h": 400},
                            "full": {"w": 872, "h": 1328},
                        },
                    },
                }
            },
            "uploaded": {
                "180": {
                    "uploaded_t": 1773681757,
                    "sizes": {
                        "100": {"w": 100, "h": 100},
                        "400": {"w": 400, "h": 400},
                        "full": {"w": 1500, "h": 1500},
                    },
                }
            },
        },
    }


def test_current_off_selected_front_image_prefers_product_language() -> None:
    record = nutella_image_record()
    expected = (
        "https://images.openfoodfacts.org/images/products/"
        "301/762/042/2003/front_fr.911.400.jpg"
    )
    assert select_image_url(record, "3017620422003") == expected
    product = normalize_record(record, ImportStats())
    assert product is not None
    assert product.image_url == expected


def test_selected_images_url_and_no_image_handling() -> None:
    direct_selected = {
        "code": "3017620422003",
        "lang": "fr",
        "selected_images": {
            "front": {
                "display": {
                    "en": "https://images.openfoodfacts.org/front-en.jpg",
                    "fr": "https://images.openfoodfacts.org/front-fr.jpg",
                }
            }
        },
    }
    assert (
        select_image_url(direct_selected, "3017620422003")
        == "https://images.openfoodfacts.org/front-fr.jpg"
    )
    assert select_image_url({"code": "3017620422003"}, "3017620422003") is None


def test_uploaded_image_is_fallback_when_no_front_is_selected() -> None:
    record = {
        "code": "3017620422003",
        "images": {
            "uploaded": {
                "1": {
                    "uploaded_t": 100,
                    "sizes": {"full": {"w": 1000, "h": 1000}},
                },
                "2": {
                    "uploaded_t": 200,
                    "sizes": {"400": {"w": 400, "h": 300}},
                },
            }
        },
    }
    assert select_image_url(record, "3017620422003") == (
        "https://images.openfoodfacts.org/images/products/"
        "301/762/042/2003/2.400.jpg"
    )


def test_images_only_backfill_updates_existing_product_and_api(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    client: TestClient,
) -> None:
    with session_factory() as session:
        product = session.get(Product, "3017620422003")
        assert product is not None
        product.barcode = "03017620422003"
        original_name = product.name
        session.commit()
    path = tmp_path / "images.json"
    path.write_text(json.dumps([nutella_image_record()]), encoding="utf-8")

    first = update_images_only(
        OpenFoodFactsSource(path),
        session_factory,
        batch_size=1,
        progress_every=0,
    )
    second = update_images_only(
        OpenFoodFactsSource(path),
        session_factory,
        batch_size=1,
        progress_every=0,
    )
    assert first.images_found == 1
    assert first.updated == 1
    assert second.updated == 0
    with session_factory() as session:
        product = session.get(Product, "03017620422003")
        assert product is not None
        assert product.name == original_name
        assert product.image_url.endswith("/front_fr.911.400.jpg")
    quality = calculate_quality(session_factory=session_factory)
    assert quality.completeness()["image"] == 50.0
    response = client.get("/v1/products/3017620422003")
    assert response.status_code == 200
    assert response.json()["product"]["image_url"].endswith(
        "/front_fr.911.400.jpg"
    )


def test_invalid_unsupported_and_missing_records_are_skipped() -> None:
    stats = ImportStats()
    assert normalize_record({"code": "3017620422004", "product_name": "Bad"}, stats) is None
    assert normalize_record({"code": "123", "product_name": "Short"}, stats) is None
    assert normalize_record({"code": "3017620422003"}, stats) is None
    assert stats.invalid_barcodes == 1
    assert stats.unsupported_barcodes == 1
    assert stats.missing_product_data == 1
    assert stats.skipped == 3


def test_gzip_jsonl_streaming(tmp_path: Path) -> None:
    path = tmp_path / "products.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as output:
        output.write(json.dumps({"code": "3017620422003", "product_name": "A"}) + "\n")
        output.write(json.dumps({"code": "5449000000996", "product_name": "B"}) + "\n")
    assert [row["product_name"] for row in OpenFoodFactsSource(path).records()] == [
        "A",
        "B",
    ]


def test_malformed_jsonl_record_is_counted_and_streaming_continues(
    tmp_path: Path, session_factory: sessionmaker[Session]
) -> None:
    path = tmp_path / "products.jsonl"
    path.write_text(
        '{"code":"5449000000996","product_name":"Cola"}\n'
        "not-json\n"
        '{"code":"4006381333931","product_name":"Example"}\n',
        encoding="utf-8",
    )
    stats = import_dataset(
        OpenFoodFactsSource(path),
        session_factory,
        batch_size=10,
        progress_every=0,
    )
    assert stats.processed == 3
    assert stats.errors == 1
    assert stats.skipped == 1
    assert stats.inserted == 2


def test_limited_import_is_idempotent_and_updates(
    session_factory: sessionmaker[Session],
) -> None:
    first = import_dataset(
        OpenFoodFactsSource(SAMPLE_PATH),
        session_factory,
        limit=1,
        batch_size=1,
        progress_every=0,
    )
    assert first.processed == 1
    assert first.updated == 1
    assert first.inserted == 0

    second = import_dataset(
        OpenFoodFactsSource(SAMPLE_PATH),
        session_factory,
        limit=1,
        batch_size=10,
        progress_every=0,
    )
    assert second.updated == 1
    with session_factory() as session:
        nutella = session.get(Product, "03017620422003")
        assert nutella is not None
        assert nutella.quantity == "400 g"
        assert session.scalar(select(func.count()).select_from(Product)) == 2


def test_api_and_batch_lookup_after_import(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    stats = import_dataset(
        OpenFoodFactsSource(SAMPLE_PATH),
        session_factory,
        batch_size=2,
        progress_every=0,
    )
    assert stats.processed == 2
    single = client.get("/v1/products/5449000000996")
    assert single.status_code == 200
    assert single.json()["product"]["name"] == "Coca-Cola"
    batch = client.post(
        "/v1/products/batch",
        json={"barcodes": ["3017620422003", "5449000000996"]},
    )
    assert [item["found"] for item in batch.json()["results"]] == [True, True]


class FakeDownloadResponse:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size: int) -> bytes:
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_downloader_streams_and_keeps_valid_existing_file(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / "dataset.jsonl.gz"
    calls = 0

    def fake_urlopen(_, **__):
        nonlocal calls
        calls += 1
        return FakeDownloadResponse(gzip.compress(b'{"code":"012345678905"}\n'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    download_dataset("https://example.test/data.gz", destination, progress_bytes=0)
    assert destination.read_bytes().startswith(b"\x1f\x8b")
    download_dataset("https://example.test/data.gz", destination, progress_bytes=0)
    assert calls == 1


def test_downloader_preserves_partial_file_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / "dataset.jsonl.gz"
    partial = tmp_path / "dataset.jsonl.gz.part"

    class BrokenResponse(FakeDownloadResponse):
        def read(self, size: int) -> bytes:
            if self.offset:
                raise OSError("connection lost")
            return super().read(2)

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda _, **__: BrokenResponse(gzip.compress(b"remaining")),
    )
    with pytest.raises(OSError):
        download_dataset("https://example.test/data.gz", destination, progress_bytes=0)
    assert partial.read_bytes() == b"\x1f\x8b"


def test_data_error_isolated_to_one_product_with_concise_diagnostics(
    monkeypatch, caplog
) -> None:
    products = [
        Product(
            barcode=f"0000000000000{index}"[-14:],
            barcode_type="GTIN-14",
            name=f"Product {index}",
            quantity="problem" if index == 2 else "1 kg",
            categories=[],
            allergens=[],
            nutrition={},
            countries=[],
            source="Open Food Facts",
            source_id=str(index),
        )
        for index in range(1, 4)
    ]

    def fake_apply(_session, batch):
        if any(product.quantity == "problem" for product in batch):
            raise DataError("INSERT", {}, Exception("value too long"))
        return len(batch), 0

    class FakeSession:
        rollback_count = 0

        def rollback(self):
            self.rollback_count += 1

    monkeypatch.setattr(ingestion_module, "_apply_batch_once", fake_apply)
    stats = ImportStats()
    session = FakeSession()
    with caplog.at_level("WARNING"):
        ingestion_module._apply_batch(session, products, stats)
    assert stats.inserted == 2
    assert stats.errors == 1
    assert stats.skipped == 1
    assert session.rollback_count >= 1
    assert "barcode=00000000000002" in caplog.text
    assert "field_lengths=" in caplog.text


def test_non_data_database_errors_are_not_hidden(monkeypatch) -> None:
    product = Product(
        barcode="04006381333931",
        barcode_type="EAN-13",
        name="Product",
        categories=[],
        allergens=[],
        nutrition={},
        countries=[],
        source="Open Food Facts",
        source_id="4006381333931",
    )

    def fail_systemically(_session, _batch):
        raise OperationalError("INSERT", {}, Exception("connection lost"))

    monkeypatch.setattr(ingestion_module, "_apply_batch_once", fail_systemically)
    with pytest.raises(OperationalError):
        ingestion_module._apply_batch(object(), [product], ImportStats())
    download_dataset,
