from datetime import datetime, timezone
from io import BytesIO
from xml.etree import ElementTree

import pytest
from PIL import Image
from sqlalchemy import select
import zxingcpp

from app.barcode_tools import RenderRequest, render_symbol
from app.billing import StripeClient, process_stripe_event
from app.config import get_settings
from app.models import ApiClient, Subscription, SubscriptionPlan
from tests.test_user_auth import create_account, login


def creator_account(session_factory):
    user_id, key = create_account(session_factory, "creator@example.com")
    with session_factory() as session:
        session.add(SubscriptionPlan(code="CREATOR", name="Creator", monthly_lookups=0,
                                     requests_per_minute=120, price_cents=499, currency="USD"))
        session.flush()
        session.scalar(select(Subscription).where(Subscription.user_id == user_id)).plan_code = "CREATOR"
        session.scalar(select(Subscription).where(Subscription.user_id == user_id)).monthly_call_limit = 0
        session.scalar(select(ApiClient).where(ApiClient.owner_user_id == user_id)).plan = "CREATOR"
        session.commit()
    return user_id, key


def test_creator_has_only_creation_allowances(unauthenticated_client, session_factory):
    _, key = creator_account(session_factory)
    client = unauthenticated_client
    headers = {"X-API-Key": key}
    balances = client.get("/v1/services/usage", headers=headers).json()
    assert balances["categories"]["generation"]["limit"] == 1000
    assert balances["categories"]["labels"]["limit"] == 250
    assert balances["categories"]["enrichment"]["limit"] == 0
    assert balances["batch_generation_max"] == 25
    assert client.get("/v1/products/3017620422003", headers=headers).status_code == 403
    assert client.post("/v1/products/enrich-csv", headers=headers,
                       json={"csv_text": "barcode\n3017620422003\n"}).status_code == 403
    assert client.post("/v1/barcodes/render", headers=headers,
                       json={"value": "3017620422003"}).status_code == 200
    assert client.post("/v1/labels/render", headers=headers,
                       json={"items": [{"value": "3017620422003"}]}).status_code == 200


def test_creator_checkout_selects_monthly_price(monkeypatch):
    settings = get_settings().model_copy(update={"stripe_secret_key": "sk_test_fixture", "stripe_creator_price_id": "price_creator"})
    stripe = StripeClient(settings)
    captured = {}
    def capture(path, data):
        captured.update(dict(data))
        return {"url": "https://checkout.stripe.com/test"}
    monkeypatch.setattr(stripe, "_post", capture)
    assert stripe.checkout("user", "creator@example.com", "CREATOR", "month", None).endswith("/test")
    assert captured["line_items[0][price]"] == "price_creator"
    assert captured["metadata[plan]"] == "CREATOR"
    assert captured["mode"] == "subscription"


def test_creator_webhook_and_portal_price_changes(session_factory):
    user_id, _ = create_account(session_factory)
    with session_factory() as session:
        session.add(SubscriptionPlan(code="CREATOR", name="Creator", monthly_lookups=0,
                                     requests_per_minute=120, price_cents=499, currency="USD"))
        session.commit()
        now = int(datetime.now(timezone.utc).timestamp())
        obj = {"id": "sub_creator", "customer": "cus_creator", "status": "active",
               "metadata": {"user_id": user_id, "plan": "STARTER", "billing_interval": "year"},
               "current_period_start": now, "current_period_end": now + 30 * 86400,
               "items": {"data": [{"price": {"id": "price_creator", "unit_amount": 499,
                                              "currency": "usd", "recurring": {"interval": "month"}}}]}}
        event = {"id": "evt_creator", "type": "customer.subscription.created", "data": {"object": obj}}
        assert process_stripe_event(session, get_settings(), event)
        assert not process_stripe_event(session, get_settings(), event)
        subscription = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        assert subscription.plan_code == "CREATOR"
        assert subscription.monthly_call_limit == 0
        assert subscription.billing_interval == "month"
        assert session.scalar(select(ApiClient).where(ApiClient.owner_user_id == user_id)).plan == "CREATOR"
        event = {"id": "evt_creator_cancel", "type": "customer.subscription.deleted", "data": {"object": {"id": "sub_creator"}}}
        process_stripe_event(session, get_settings(), event)
        assert subscription.plan_code == "FREE"


def test_existing_subscriber_uses_portal_and_creator_has_no_annual_checkout(
    unauthenticated_client, session_factory, monkeypatch,
):
    user_id, _ = creator_account(session_factory)
    client = unauthenticated_client
    response = login(client, "creator@example.com")
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert client.post("/v1/billing/checkout", headers=headers, json={"plan": "CREATOR", "billing_interval": "year"}).status_code == 422
    with session_factory() as session:
        subscription = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        subscription.provider_subscription_id = "sub_existing"
        subscription.provider_customer_id = "cus_existing"
        session.commit()
    monkeypatch.setattr(StripeClient, "portal", lambda self, customer: "https://billing.stripe.com/test")
    settings = get_settings().model_copy(update={"stripe_secret_key": "sk_test_fixture"})
    from app.main import app
    app.dependency_overrides[get_settings] = lambda: settings
    result = client.post("/v1/billing/checkout", headers=headers, json={"plan": "STARTER"})
    assert result.status_code == 200
    assert result.json()["url"] == "https://billing.stripe.com/test"


def test_former_subscriber_can_buy_creator_again(unauthenticated_client, session_factory, monkeypatch):
    user_id, _ = create_account(session_factory)
    with session_factory() as session:
        subscription = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        subscription.provider_subscription_id = "sub_canceled"
        subscription.provider_customer_id = "cus_existing"
        session.commit()
    response = login(unauthenticated_client)
    headers = {"Authorization": "Bearer " + response.json()["access_token"]}
    settings = get_settings().model_copy(update={"stripe_secret_key": "sk_test_fixture"})
    from app.main import app
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(StripeClient, "checkout", lambda *args: "https://checkout.stripe.com/new")
    result = unauthenticated_client.post("/v1/billing/checkout", headers=headers, json={"plan": "CREATOR"})
    assert result.status_code == 200
    assert result.json()["url"] == "https://checkout.stripe.com/new"


@pytest.mark.parametrize("symbology,value,formats", [
    ("ean13", "3017620422003", zxingcpp.BarcodeFormat.EAN13),
    ("ean13", "0036000291452", zxingcpp.BarcodeFormat.EAN13),
    ("upca", "012345678905", zxingcpp.BarcodeFormat.UPCA),
    ("code128", "SKU-002", zxingcpp.BarcodeFormat.Code128),
    ("qr", "https://barcodenest.com/", zxingcpp.BarcodeFormat.QRCode),
])
def test_generated_png_decodes_independently(symbology, value, formats):
    image = Image.open(BytesIO(render_symbol(RenderRequest(value=value, symbology=symbology, format="png"))))
    assert image.info["dpi"][0] == pytest.approx(300, abs=.1)
    decoded = zxingcpp.read_barcode(image, formats=formats)
    assert decoded
    if symbology in {"upca", "ean13"}:
        from app.barcodes import parse_barcode
        assert parse_barcode(decoded.text).gtin14 == parse_barcode(value).gtin14
    else:
        assert decoded.text == value


@pytest.mark.parametrize("symbology,value", [("ean13", "3017620422003"), ("upca", "012345678905")])
def test_retail_svg_has_guard_extensions_and_complete_digits(symbology, value):
    root = ElementTree.fromstring(render_symbol(RenderRequest(value=value, symbology=symbology)))
    namespace = {"s": "http://www.w3.org/2000/svg"}
    bars = [item for item in root.findall("s:rect", namespace) if item.get("fill") == "black"]
    assert {round(float(item.get("height")), 2) for item in bars} == {22.85, 24.5}
    assert min(float(item.get("x")) for item in bars) == 6.5
    right = max(float(item.get("x")) + float(item.get("width")) for item in bars)
    assert round(float(root.get("width").removesuffix("mm")) - right, 3) == 6.5
    digits = root.findall("s:text", namespace)
    assert "".join(item.text for item in digits) == value
    if symbology == "upca":
        first_character = [item for item in bars if 6.5 + 3 * .33 <= float(item.get("x")) < 6.5 + 10 * .33]
        assert all(float(item.get("height")) == 24.5 for item in first_character)


def test_qr_svg_preserves_a_white_background():
    root = ElementTree.fromstring(render_symbol(RenderRequest(value="https://barcodenest.com/", symbology="qr")))
    assert any(item.get("fill") == "white" for item in root.iter() if item.tag.endswith("rect"))


def test_retail_decoder_preserves_identifiers_across_digit_patterns():
    import random
    from app.barcodes import calculate_check_digit, parse_barcode
    generator = random.Random(20261123)
    for symbology, length in [("ean13", 12), ("upca", 11)]:
        for _ in range(12):
            body = "".join(str(generator.randrange(10)) for _ in range(length))
            value = body + str(calculate_check_digit(body))
            image = Image.open(BytesIO(render_symbol(RenderRequest(value=value, symbology=symbology, format="png"))))
            decoded = zxingcpp.read_barcode(image)
            assert decoded and parse_barcode(decoded.text).gtin14 == parse_barcode(value).gtin14
