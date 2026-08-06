import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.billing import process_stripe_event, verify_stripe_event
from app.config import get_settings
from app.email_service import UsageWarning
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
        session.commit()
    assert unauthenticated_client.get("/v1/products/3017620422003", headers={"X-API-Key": key}).status_code == 200
    with session_factory() as session:
        record = session.scalar(select(Subscription).where(Subscription.user_id == user_id))
        assert record.monthly_calls_used == 1
        assert record.usage_warning_sent_at is None


def stripe_subscription(user_id, plan, status="active", cancel=False):
    now = int(time.time())
    return {"id": "sub_123", "customer": "cus_123", "status": status, "cancel_at_period_end": cancel,
            "current_period_start": now, "current_period_end": now + 2_592_000,
            "metadata": {"user_id": user_id, "plan": plan},
            "items": {"data": [{"price": {"id": f"price_{plan.lower()}"}}]}}


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
