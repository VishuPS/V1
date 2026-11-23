from datetime import datetime, timezone
from io import BytesIO

from PIL import Image
import pytest
from sqlalchemy import select

from app.auth import issue_api_key
from app.config import ServiceLimit, get_settings
from app.models import ApiClient, ApiKey, MonthlyUsage, ServiceUsage
from app.service_usage import consume_service_usage


@pytest.fixture
def tools_client(client):
    from app.main import app
    settings = get_settings().model_copy(update={
        "fallback_lookups_enabled": False,
        "service_limits": {
            "FREE": ServiceLimit(generation=2, labels=2, enrichment=3),
            "STARTER": ServiceLimit(generation=6, labels=2, enrichment=3, batch_generation_max=2),
            "GROWTH": ServiceLimit(generation=10, labels=3, enrichment=5, batch_generation_max=3),
        },
    })
    app.dependency_overrides[get_settings] = lambda: settings
    yield client


def test_generation_is_separate_from_labels_and_lookups(tools_client, session_factory):
    payload = {"value": "3017620422003", "symbology": "ean13"}
    assert tools_client.post("/v1/barcodes/render", json=payload).status_code == 200
    assert tools_client.post("/v1/barcodes/render", json=payload).status_code == 200
    assert tools_client.post("/v1/barcodes/render", json=payload).status_code == 429
    labels = tools_client.post("/v1/labels/render", json={"items": [payload]})
    assert labels.status_code == 200
    assert labels.content.startswith(b"%PDF")
    balances = tools_client.get("/v1/services/usage").json()["categories"]
    assert balances["generation"]["remaining"] == 0
    assert balances["labels"]["remaining"] == 1
    with session_factory() as session:
        assert session.scalar(select(MonthlyUsage)) is None


@pytest.mark.parametrize("symbology,value", [
    ("ean13", "3017620422003"), ("upca", "012345678905"),
    ("code128", "SKU-123"), ("qr", "https://barcodenest.com/"),
])
def test_png_is_a_real_image(tools_client, symbology, value):
    response = tools_client.post("/v1/barcodes/render", json={
        "symbology": symbology, "value": value, "format": "png",
    })
    assert response.status_code == 200, response.text
    image = Image.open(BytesIO(response.content))
    image.verify()


def test_invalid_gtin_is_rejected_without_charging(tools_client):
    assert tools_client.post("/v1/barcodes/render", json={"value": "3017620422004"}).status_code == 422
    assert tools_client.get("/v1/services/usage").json()["categories"]["generation"]["used"] == 0


def test_enrichment_preview_and_download(tools_client, session_factory):
    payload = {"csv_text": "barcode,sku\n3017620422003,=1+1\n0036000291452,missing\nbad,invalid\n"}
    preview = tools_client.post("/v1/products/enrichment-preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["units_required"] == 3
    assert preview.json()["invalid_rows"] == 1
    assert preview.json()["balance"]["used"] == 0
    response = tools_client.post("/v1/products/enrich-csv", json=payload)
    assert response.status_code == 200, response.text
    assert "Nutella" in response.text
    assert "not_found" in response.text
    assert "invalid" in response.text
    assert "'=1+1" in response.text
    assert "0036000291452" in response.text
    assert tools_client.post("/v1/products/enrich-csv", json=payload).status_code == 429
    assert tools_client.get("/v1/services/usage").json()["categories"]["generation"]["used"] == 0
    with session_factory() as session:
        assert session.scalar(select(MonthlyUsage)) is None


def test_multiple_api_keys_share_tool_balance(tools_client, session_factory):
    with session_factory() as session:
        original = session.scalar(select(ApiKey))
        _, key = issue_api_key(session, original.client, get_settings())
    payload = {"value": "3017620422003"}
    assert tools_client.post("/v1/barcodes/render", json=payload).status_code == 200
    assert tools_client.post("/v1/barcodes/render", json=payload, headers={"X-API-Key": key}).status_code == 200
    assert tools_client.post("/v1/barcodes/render", json=payload).status_code == 429


def test_label_copies_are_billed_and_rejected_atomically(tools_client):
    payload = {"items": [{"value": "3017620422003", "copies": 3}]}
    assert tools_client.post("/v1/labels/render", json=payload).status_code == 429
    assert tools_client.get("/v1/services/usage").json()["categories"]["labels"]["used"] == 0
    payload["items"][0]["copies"] = 2
    assert tools_client.post("/v1/labels/render", json=payload).status_code == 200


def test_tools_require_an_api_key(unauthenticated_client):
    assert unauthenticated_client.post("/v1/barcodes/render", json={"value": "3017620422003"}).status_code == 401


def test_csv_label_copies_use_the_label_allowance(tools_client):
    response = tools_client.post("/v1/labels/from-csv", json={
        "csv_text": "barcode,title,copies\n3017620422003,Sample label,2\n",
    })
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF")
    assert response.headers["X-Service-Quota-Remaining"] == "0"
    assert tools_client.get("/v1/services/usage").json()["categories"]["generation"]["used"] == 0


def test_concurrent_service_usage_cannot_exceed_account_limit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.auth import AuthContext, RateLimitResult
    from app.db import Base
    settings = get_settings().model_copy(update={
        "service_limits": {"FREE": ServiceLimit(generation=3, labels=2, enrichment=2)},
    })
    engine = create_engine("sqlite:///" + str(tmp_path / "tools.db"), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        client = ApiClient(identifier="concurrent-tools", plan="FREE")
        session.add(client)
        session.flush()
        key, _ = issue_api_key(session, client, settings)
        key_id = key.id
    def consume(_):
        with Session(engine) as session:
            key = session.get(ApiKey, key_id)
            context = AuthContext(key, key.client, settings.plan_limits["FREE"], RateLimitResult(30, 29, 0))
            try:
                consume_service_usage(session, context, settings, "generation", 1)
                return True
            except HTTPException as error:
                assert error.status_code == 429
                return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(consume, range(12))) == 3
    with Session(engine) as session:
        assert session.scalar(select(ServiceUsage.units)) == 3
    engine.dispose()


def test_quota_rolls_over_and_never_partially_charges(session_factory):
    from app.auth import AuthContext, RateLimitResult
    settings = get_settings().model_copy(update={"service_limits": {"FREE": ServiceLimit(generation=2, labels=2, enrichment=2)}})
    with session_factory() as session:
        client = ApiClient(identifier="rollover", plan="FREE")
        session.add(client)
        session.flush()
        key, _ = issue_api_key(session, client, settings)
        context = AuthContext(key, client, settings.plan_limits["FREE"], RateLimitResult(30, 29, 0))
        january = datetime(2026, 1, 31, tzinfo=timezone.utc)
        february = datetime(2026, 2, 1, tzinfo=timezone.utc)
        consume_service_usage(session, context, settings, "generation", 2, now=january)
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            consume_service_usage(session, context, settings, "generation", 1, now=january)
        consume_service_usage(session, context, settings, "generation", 1, now=february)
        assert sorted(session.scalars(select(ServiceUsage.units)).all()) == [1, 2]

def test_custom_service_limits_keep_one_time_allowances_typed():
    from app.config import Settings
    settings = Settings(service_limits={"free": {"generation": 7, "labels": 3, "enrichment": 9}})
    assert settings.service_limits["ONE_TIME"].generation == 7


def test_unsupported_label_text_is_rejected_without_charge(tools_client):
    response = tools_client.post("/v1/labels/render", json={
        "items": [{"value": "3017620422003", "title": "\u96f7\u66b4"}],
    })
    assert response.status_code == 422
    assert tools_client.get("/v1/services/usage").json()["categories"]["labels"]["used"] == 0


@pytest.mark.parametrize("path", [
    "/v1/account/barcodes/render", "/v1/account/barcodes/batch-render",
    "/v1/account/barcodes/validate",
])
def test_browser_tool_endpoints_require_login(tools_client, path):
    payload = {"items": [{"value": "3017620422003"}]} if "batch" in path else {"value": "3017620422003"}
    assert tools_client.post(path, json=payload).status_code == 401


def test_free_plan_cannot_generate_zip_batches(tools_client):
    response = tools_client.post("/v1/barcodes/batch-render", json={"items": [{"value": "3017620422003"}]})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "paid_plan_required"
    assert tools_client.get("/v1/services/usage").json()["categories"]["generation"]["used"] == 0


def test_paid_zip_counts_each_image_and_enforces_request_size(tools_client, session_factory):
    from zipfile import ZipFile
    with session_factory() as session:
        account = session.scalar(select(ApiClient))
        account.plan = "STARTER"
        session.commit()
    items = [{"value": "3017620422003"}, {"value": "SKU-002", "symbology": "code128", "format": "png"}]
    response = tools_client.post("/v1/barcodes/batch-render", json={"items": items})
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["barcode-001.svg", "barcode-002.png"]
        assert b"<svg" in archive.read("barcode-001.svg")
        assert archive.read("barcode-002.png").startswith(b"\x89PNG")
    assert tools_client.post("/v1/barcodes/batch-render", json={"items": items + items}).status_code == 422
    balance = tools_client.get("/v1/services/usage").json()
    assert balance["categories"]["generation"]["used"] == 2
    assert balance["batch_generation_max"] == 2


def test_invalid_batch_is_rejected_without_charging_any_images(tools_client, session_factory):
    with session_factory() as session:
        session.scalar(select(ApiClient)).plan = "GROWTH"
        session.commit()
    response = tools_client.post("/v1/barcodes/batch-render", json={"items": [
        {"value": "3017620422003"}, {"value": "3017620422004"},
    ]})
    assert response.status_code == 422
    assert tools_client.get("/v1/services/usage").json()["categories"]["generation"]["used"] == 0


def test_signed_in_generation_shares_api_key_allowance(tools_client, session_factory):
    from tests.test_user_auth import create_account, login
    user_id, raw_key = create_account(session_factory)
    logged_in = login(tools_client)
    assert logged_in.status_code == 200
    bearer = {"Authorization": "Bearer " + logged_in.json()["access_token"]}
    response = tools_client.post("/v1/account/barcodes/render", headers=bearer, json={"value": "3017620422003"})
    assert response.status_code == 200, response.text
    api_response = tools_client.post("/v1/barcodes/render", headers={"X-API-Key": raw_key}, json={"value": "3017620422003"})
    assert api_response.status_code == 200
    balances = tools_client.get("/v1/account/services/usage", headers=bearer).json()
    assert balances["categories"]["generation"]["used"] == 2
    assert tools_client.post("/v1/account/barcodes/render", headers=bearer, json={"value": "3017620422003"}).status_code == 429
    assert tools_client.post("/v1/account/barcodes/batch-render", headers=bearer, json={"items": [{"value": "3017620422003"}]}).status_code == 403
    validation = tools_client.post("/v1/account/barcodes/validate", headers=bearer, json={"value": "3017620422003"})
    assert validation.status_code == 200
    assert validation.json()["gtin14"] == "03017620422003"


def test_signed_in_tool_rejects_untrusted_origins(tools_client, session_factory):
    from tests.test_user_auth import create_account, login
    create_account(session_factory)
    logged_in = login(tools_client)
    response = tools_client.post("/v1/account/barcodes/render", headers={
        "Authorization": "Bearer " + logged_in.json()["access_token"],
        "Origin": "https://untrusted.example",
    }, json={"value": "3017620422003"})
    assert response.status_code == 403


def test_paid_account_session_enforces_subscription_status(tools_client, session_factory):
    from app.models import Subscription, SubscriptionPlan
    from tests.test_user_auth import create_account, login
    user_id, _ = create_account(session_factory)
    with session_factory() as session:
        session.add(SubscriptionPlan(code="STARTER", name="Starter", monthly_lookups=2000,
                                     requests_per_minute=300, price_cents=999, currency="USD"))
        session.flush()
        session.scalar(select(ApiClient).where(ApiClient.owner_user_id == user_id)).plan = "STARTER"
        session.scalar(select(Subscription).where(Subscription.user_id == user_id)).plan_code = "STARTER"
        session.commit()
    logged_in = login(tools_client)
    headers = {"Authorization": "Bearer " + logged_in.json()["access_token"]}
    body = {"items": [{"value": "3017620422003"}, {"value": "012345678905", "symbology": "upca"}]}
    assert tools_client.post("/v1/account/barcodes/batch-render", headers=headers, json=body).status_code == 200
    with session_factory() as session:
        session.scalar(select(Subscription).where(Subscription.user_id == user_id)).status = "canceled"
        session.commit()
    response = tools_client.post("/v1/account/barcodes/batch-render", headers=headers, json=body)
    assert response.status_code == 402
