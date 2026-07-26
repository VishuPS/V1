from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db import get_db
from app.main import app


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_known_product_lookup(client: TestClient) -> None:
    response = client.get("/v1/products/3017620422003")
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["found"] is True
    assert body["barcode"] == "3017620422003"
    assert body["barcode_type"] == "EAN-13"
    assert body["canonical_gtin"] == "03017620422003"
    assert body["product"]["name"] == "Nutella"
    assert body["source"] == {
        "name": "Open Food Facts",
        "source_id": "3017620422003",
    }


def test_equivalent_ean_representation_finds_upc(client: TestClient) -> None:
    response = client.get("/v1/products/0012345678905")
    assert response.status_code == 200
    assert response.json()["product"]["name"] == "Example UPC Product"


def test_upc_and_gtin14_have_same_canonical_identity(client: TestClient) -> None:
    upc = client.get("/v1/products/012345678905")
    gtin14 = client.get("/v1/products/00012345678905")
    assert upc.status_code == gtin14.status_code == 200
    assert upc.json()["barcode"] == "012345678905"
    assert upc.json()["barcode_type"] == "UPC-A"
    assert gtin14.json()["barcode"] == "00012345678905"
    assert gtin14.json()["barcode_type"] == "GTIN-14"
    assert upc.json()["canonical_gtin"] == gtin14.json()["canonical_gtin"]


def test_valid_unknown_product(client: TestClient) -> None:
    response = client.get("/v1/products/4006381333931")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "product_not_found"


def test_malformed_barcode(client: TestClient) -> None:
    response = client.get("/v1/products/not-a-barcode")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_barcode"


def test_batch_lookup(client: TestClient) -> None:
    response = client.post(
        "/v1/products/batch",
        json={"barcodes": ["3017620422003", "5449000000996"]},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["found"] is True
    assert results[1]["valid"] is True
    assert results[1]["found"] is False


def test_mixed_batch(client: TestClient) -> None:
    response = client.post(
        "/v1/products/batch",
        json={"barcodes": ["3017620422003", "4006381333931", "123"]},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert [(r["valid"], r["found"]) for r in results] == [
        (True, True),
        (True, False),
        (False, False),
    ]
    assert results[2]["error"]


def test_batch_limit(client: TestClient) -> None:
    response = client.post(
        "/v1/products/batch",
        json={"barcodes": ["3017620422003"] * 101},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "batch_limit_exceeded"


def test_empty_batch(client: TestClient) -> None:
    response = client.post("/v1/products/batch", json={"barcodes": []})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_batch"


def test_request_validation_is_structured(client: TestClient) -> None:
    response = client.post("/v1/products/batch", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_error"


def test_database_failure_returns_structured_500(client: TestClient) -> None:
    def broken_db():
        raise OperationalError("SELECT", {}, Exception("database unavailable"))
        yield

    app.dependency_overrides[get_db] = broken_db
    response = client.get("/v1/products/3017620422003")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "database_error"
