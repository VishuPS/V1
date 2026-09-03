import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.billing import payment_link_checkout, process_stripe_event, verified_reference, verify_stripe_event
from app.config import get_settings
from app.email_service import ResendEmailProvider, UsageLimitReached, UsageWarning
from app.models import ApiClient, ApiKey, StripeWebhookEvent, Subscription
from tests.test_user_auth import create_account, login


def account(session_factory, plan="FREE", limit=250, used=0):
    user_id, raw_key = create_account(session_factory, f"{plan.lower()}@example.com")
    with session_factory() as session:
        subscription = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        subscription.plan_code = plan
        subscription.monthly_call_limit = limit
        subscription.monthly_calls_used = used
        session.scalar(select(ApiClient).where(ApiClient.owner_user_id == user_id)).plan = plan
        session.commit()
    return user_id, raw_key


@pytest.mark.parametrize("plan,limit", [("FREE", 250), ("STARTER", 2_000), ("GROWTH", 5_000)])
def test_plan_limits_block_at_exact_boundary(unauthenticated_client, session_factory, plan, limit):
    _, key = account(session_factory, plan, limit, limit - 1)
    first = unauthenticated_client.get("/v1/products/3017620422003", headers={"X-API-Key": key})
    blocked = unauthenticated_client.get("/v1/products/3017620422003", headers={"X-API-Key": key})
    assert first.status_code == 200
    assert blocked.status_code == 429
    error = blocked.json()["error"]
    assert (error["plan"], error["used"], error["limit"]) == (plan.lower(), limit, limit)


def test_warning_sent_once_at_ninety_percent(unauthenticated_client, session_factory, monkeypatch):
    _, key = account(session_factory, "FREE", 250, 224)
    sent: list[UsageWarning] = []
    monkeypatch.setattr("app.api.routes.send_usage_warning_safely", lambda _settings, warning: sent.append(warning) if warning else None)
    assert unauthenticated_client.get("/v1/products/3017620422003", headers={"X-API-Key": key}).status_code == 200
    assert unauthenticated_client.get("/v1/products/3017620422003", headers={"X-API-Key": key}).status_code == 200
    assert len(sent) == 1
    assert (sent[0].used, sent[0].limit) == (225, 250)


def test_limit_reached_email_sent_once_at_exact_boundary(
    unauthenticated_client, session_factory, monkeypatch
):
    user_id, key = account(session_factory, "FREE", 250, 249)
    sent: list[UsageLimitReached] = []
    monkeypatch.setattr(
        "app.api.routes.send_usage_limit_reached_safely",
        lambda _settings, notice: sent.append(notice) if notice else None,
    )

    final_call = unauthenticated_client.get(
        "/v1/products/3017620422003", headers={"X-API-Key": key}
    )
    blocked_call = unauthenticated_client.get(
        "/v1/products/3017620422003", headers={"X-API-Key": key}
    )

    assert final_call.status_code == 200
    assert final_call.headers["X-Monthly-Quota-Remaining"] == "0"
    assert blocked_call.status_code == 429
    assert len(sent) == 1
    assert (sent[0].plan, sent[0].used, sent[0].limit) == ("FREE", 250, 250)
    with session_factory() as session:
        marker = session.scalar(
            select(Subscription.usage_limit_email_sent_at).where(
                Subscription.user_id == user_id
            )
        )
        assert marker is not None


def test_limit_reached_email_contains_upgrade_link(monkeypatch):
    request: dict = {}

    class Response:
        def raise_for_status(self):
            return None

    def post(url, **kwargs):
        request.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("app.email_service.httpx.post", post)
    settings = get_settings().model_copy(
        update={
            "resend_api_key": "re_test",
            "website_url": "https://barcodenest.com",
        }
    )
    notice = UsageLimitReached(
        email="owner@example.com",
        plan="FREE",
        used=250,
        limit=250,
        period_end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )

    ResendEmailProvider(settings).send_usage_limit_reached(notice)

    assert request["url"] == "https://api.resend.com/emails"
    assert request["json"]["to"] == ["owner@example.com"]
    assert request["json"]["subject"] == "You have reached your BarcodeNest API limit"
    assert "https://barcodenest.com/pricing/" in request["json"]["html"]


def test_failed_and_account_requests_do_not_consume_usage(unauthenticated_client, session_factory):
    user_id, key = account(session_factory)
    assert unauthenticated_client.get("/v1/products/4006381333931", headers={"X-API-Key": key}).status_code == 404
    tokens = login(unauthenticated_client, "free@example.com").json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert unauthenticated_client.get("/v1/account/usage", headers=headers).status_code == 200
    assert unauthenticated_client.get("/v1/account/subscription", headers=headers).status_code == 200
    with session_factory() as session:
        assert session.scalar(select(Subscription.monthly_calls_used).where(Subscription.user_id == user_id)) == 0


def test_free_usage_resets_after_period_end(unauthenticated_client, session_factory):
    user_id, key = account(session_factory, "FREE", 250, 250)
    with session_factory() as session:
        record = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        record.usage_period_end = datetime.now(timezone.utc) - timedelta(seconds=1)
        record.usage_warning_sent_at = datetime.now(timezone.utc)
        record.usage_limit_email_sent_at = datetime.now(timezone.utc)
        session.commit()
    assert unauthenticated_client.get("/v1/products/3017620422003", headers={"X-API-Key": key}).status_code == 200
    with session_factory() as session:
        record = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        assert record.monthly_calls_used == 1
        assert record.usage_warning_sent_at is None
        assert record.usage_limit_email_sent_at is None


def stripe_subscription(user_id, plan, status="active", cancel=False, interval="month"):
    now = int(time.time())
    duration = 31_536_000 if interval == "year" else 2_592_000
    suffix = "_annual" if interval == "year" else ""
    return {"id": "sub_123", "customer": "cus_123", "status": status, "cancel_at_period_end": cancel,
            "current_period_start": now, "current_period_end": now + duration,
            "metadata": {"user_id": user_id, "plan": plan, "billing_interval": interval},
            "items": {"data": [{"price": {"id": f"price_{plan.lower()}{suffix}", "recurring": {"interval": interval}}}]}}


def test_stripe_upgrade_cancellation_and_duplicate_event(session_factory):
    user_id, _ = account(session_factory)
    settings = get_settings().model_copy(update={"stripe_starter_price_id": "price_starter", "stripe_growth_price_id": "price_growth"})
    with session_factory() as session:
        event = {"id": "evt_upgrade", "type": "customer.subscription.updated", "data": {"object": stripe_subscription(user_id, "STARTER")}}
        assert process_stripe_event(session, settings, event) is True
        assert process_stripe_event(session, settings, event) is False
        record = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        assert (record.plan_code, record.monthly_call_limit) == ("STARTER", 2_000)
        cancelled = {"id": "evt_cancel", "type": "customer.subscription.deleted", "data": {"object": {"id": "sub_123"}}}
        assert process_stripe_event(session, settings, cancelled) is True
        assert (record.plan_code, record.monthly_call_limit) == ("FREE", 250)
        assert session.scalar(select(StripeWebhookEvent).where(StripeWebhookEvent.event_id == "evt_upgrade"))


def test_stripe_signature_verification():
    secret = "whsec_test"
    payload = json.dumps({"id": "evt_1", "type": "test"}).encode()
    timestamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    assert verify_stripe_event(payload, f"t={timestamp},v1={digest}", secret)["id"] == "evt_1"
    with pytest.raises(HTTPException):
        verify_stripe_event(payload, f"t={timestamp},v1=invalid", secret)


def test_payment_link_contains_verified_account_reference():
    settings = get_settings().model_copy(update={
        "stripe_starter_payment_link": "https://buy.stripe.com/starter",
        "stripe_growth_payment_link": "https://buy.stripe.com/growth",
    })
    url = payment_link_checkout(settings, "user-123", "owner@example.com", "STARTER")
    assert url.startswith("https://buy.stripe.com/starter?")
    reference = url.split("client_reference_id=", 1)[1].split("&", 1)[0]
    assert verified_reference(reference, settings) == "user-123"
    assert verified_reference(reference.replace("user-123", "user-456"), settings) is None


def test_annual_checkout_does_not_fall_back_to_monthly_payment_link():
    settings = get_settings().model_copy(update={
        "stripe_starter_payment_link": "https://buy.stripe.com/starter-monthly",
        "stripe_starter_annual_payment_link": "https://buy.stripe.com/starter-annual",
    })
    monthly = payment_link_checkout(settings, "user-123", "owner@example.com", "STARTER", "month")
    annual = payment_link_checkout(settings, "user-123", "owner@example.com", "STARTER", "year")
    assert monthly.startswith("https://buy.stripe.com/starter-monthly?")
    assert annual.startswith("https://buy.stripe.com/starter-annual?")


def test_annual_subscription_keeps_monthly_usage_window(session_factory):
    user_id, _ = account(session_factory)
    settings = get_settings().model_copy(update={"stripe_starter_annual_price_id": "price_starter_annual"})
    with session_factory() as session:
        event = {"id": "evt_annual", "type": "customer.subscription.updated", "data": {"object": stripe_subscription(user_id, "STARTER", interval="year")}}
        assert process_stripe_event(session, settings, event) is True
        record = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        assert record.billing_interval == "year"
        assert record.current_period_end - record.current_period_start > timedelta(days=360)
        assert timedelta(days=27) < record.usage_period_end - record.usage_period_start < timedelta(days=32)


def test_payment_link_webhook_maps_price_to_account(session_factory):
    user_id, _ = account(session_factory)
    settings = get_settings()
    link = payment_link_checkout(
        settings.model_copy(update={"stripe_growth_payment_link": "https://buy.stripe.com/growth"}),
        user_id,
        "growth@example.com",
        "GROWTH",
    )
    reference = link.split("client_reference_id=", 1)[1].split("&", 1)[0]
    subscription = stripe_subscription(user_id, "GROWTH")
    subscription["metadata"] = {}
    subscription["items"]["data"][0]["price"].update({"unit_amount": 1999, "currency": "usd"})

    class StripeStub:
        def subscription(self, _subscription_id):
            return subscription

    event = {"id": "evt_payment_link", "type": "checkout.session.completed", "data": {"object": {
        "subscription": "sub_123", "client_reference_id": reference,
    }}}
    with session_factory() as session:
        assert process_stripe_event(session, settings, event, StripeStub()) is True
        record = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        assert (record.plan_code, record.monthly_call_limit) == ("GROWTH", 5_000)
