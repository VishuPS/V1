import gzip
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ingestion.open_food_facts import (
    ImportStats,
    OpenFoodFactsSource,
    download_dataset,
    import_dataset,
    normalize_record,
)
from app.models import Product


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

    def fake_urlopen(_):
        nonlocal calls
        calls += 1
        return FakeDownloadResponse(b"\x1f\x8bfixture-content")

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
        lambda _: BrokenResponse(b"\x1f\x8bremaining"),
    )
    with pytest.raises(OSError):
        download_dataset("https://example.test/data.gz", destination, progress_bytes=0)
    assert partial.read_bytes() == b"\x1f\x8b"
    download_dataset,
