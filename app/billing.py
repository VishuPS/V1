import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ApiClient, StripeWebhookEvent, Subscription, current_month_end


def payment_link_checkout(settings: Settings, user_id: str, email: str, plan: str) -> str | None:
    link = settings.stripe_starter_payment_link if plan == "STARTER" else settings.stripe_growth_payment_link
    if not link:
        return None
    signature = hmac.new(settings.api_key_hash_secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()
    query = urlencode({"client_reference_id": f"{user_id}_{signature}", "prefilled_email": email})
    return f"{link}{'&' if '?' in link else '?'}{query}"


def verified_reference(value: str | None, settings: Settings) -> str | None:
    if not value or "_" not in value:
        return None
    user_id, signature = value.rsplit("_", 1)
    expected = hmac.new(settings.api_key_hash_secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()
    return user_id if hmac.compare_digest(expected, signature) else None


class StripeClient:
    base_url = "https://api.stripe.com/v1"

    def __init__(self, settings: Settings) -> None:
        if not settings.stripe_secret_key:
            raise HTTPException(503, detail={"code":"billing_unavailable","message":"Billing is not configured"})
        self.settings = settings
        self.headers = {"Authorization": f"Bearer {settings.stripe_secret_key}"}

    def _post(self, path: str, data: list[tuple[str, str]]) -> dict:
        response = httpx.post(f"{self.base_url}{path}", headers=self.headers, data=data, timeout=20)
        if response.status_code >= 400:
            raise HTTPException(502, detail={"code":"billing_provider_error","message":"Stripe could not complete the request"})
        return response.json()

    def _get(self, path: str) -> dict:
        response = httpx.get(f"{self.base_url}{path}", headers=self.headers, timeout=20)
        if response.status_code >= 400:
            raise HTTPException(502, detail={"code":"billing_provider_error","message":"Stripe could not complete the request"})
        return response.json()

    def checkout(self, user_id: str, email: str, plan: str, customer_id: str | None) -> str:
        price = self.settings.stripe_starter_price_id if plan == "STARTER" else self.settings.stripe_growth_price_id
        if not price:
            raise HTTPException(503, detail={"code":"billing_unavailable","message":"The selected Stripe price is not configured"})
        data = [("mode","subscription"),("line_items[0][price]",price),("line_items[0][quantity]","1"),("success_url",f"{self.settings.website_url.rstrip('/')}/billing/?checkout=success"),("cancel_url",f"{self.settings.website_url.rstrip('/')}/billing/?checkout=cancelled"),("client_reference_id",user_id),("metadata[user_id]",user_id),("metadata[plan]",plan),("subscription_data[metadata][user_id]",user_id),("subscription_data[metadata][plan]",plan)]
        data.append(("customer", customer_id) if customer_id else ("customer_email", email))
        return self._post("/checkout/sessions", data)["url"]

    def portal(self, customer_id: str) -> str:
        return self._post("/billing_portal/sessions", [("customer",customer_id),("return_url",f"{self.settings.website_url.rstrip('/')}/billing/")])["url"]

    def subscription(self, subscription_id: str) -> dict:
        return self._get(f"/subscriptions/{subscription_id}")

    def create_product(self, name: str, plan: str) -> str:
        return self._post("/products", [("name", name), ("metadata[service]", "barcodenest"), ("metadata[plan]", plan)])["id"]

    def create_monthly_price(self, product_id: str, amount_cents: int, plan: str) -> str:
        return self._post("/prices", [("product", product_id), ("unit_amount", str(amount_cents)), ("currency", "usd"), ("recurring[interval]", "month"), ("metadata[plan]", plan)])["id"]


def verify_stripe_event(payload: bytes, signature: str | None, secret: str | None, tolerance: int = 300) -> dict:
    if not signature or not secret:
        raise HTTPException(400, detail={"code":"invalid_webhook_signature","message":"Stripe webhook signature is invalid"})
    parts = [part.split("=", 1) for part in signature.split(",") if "=" in part]
    timestamp = next((value for key, value in parts if key == "t"), None)
    signatures = [value for key, value in parts if key == "v1"]
    if not timestamp or not signatures or abs(time.time() - int(timestamp)) > tolerance:
        raise HTTPException(400, detail={"code":"invalid_webhook_signature","message":"Stripe webhook signature is invalid"})
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise HTTPException(400, detail={"code":"invalid_webhook_signature","message":"Stripe webhook signature is invalid"})
    return json.loads(payload)


def _timestamp(value) -> datetime | None:
    return datetime.fromtimestamp(value, timezone.utc) if value else None


def _subscription_id(obj: dict) -> str | None:
    value = obj.get("subscription")
    if isinstance(value, str): return value
    return (obj.get("parent") or {}).get("subscription_details", {}).get("subscription")


def _apply_subscription(session: Session, settings: Settings, obj: dict, fallback_user_id: str | None = None) -> None:
    metadata = obj.get("metadata") or {}
    user_id = metadata.get("user_id") or fallback_user_id
    if not user_id: return
    first_item = ((obj.get("items") or {}).get("data") or [{}])[0]
    price_id = (first_item.get("price") or {}).get("id") or obj.get("price_id")
    price = (first_item.get("price") or {})
    plan = metadata.get("plan") or ("STARTER" if price_id == settings.stripe_starter_price_id else "GROWTH" if price_id == settings.stripe_growth_price_id else None)
    if not plan and price.get("currency") == "usd":
        plan = "STARTER" if price.get("unit_amount") == 999 else "GROWTH" if price.get("unit_amount") == 1999 else None
    if not plan: return
    period_start = _timestamp(obj.get("current_period_start")) or _timestamp(first_item.get("current_period_start")) or datetime.now(timezone.utc)
    period_end = _timestamp(obj.get("current_period_end")) or _timestamp((((obj.get("items") or {}).get("data") or [{}])[0]).get("current_period_end"))
    if not period_end: return
    record = session.scalar(select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.created_at.desc()).with_for_update())
    if not record: return
    changed_period = record.usage_period_start != period_start
    record.plan_code = plan
    record.status = obj.get("status") or "active"
    record.provider = "stripe"
    record.provider_customer_id = obj.get("customer") or record.provider_customer_id
    record.provider_subscription_id = obj.get("id") or record.provider_subscription_id
    record.provider_price_id = price_id
    record.current_period_start = period_start
    record.current_period_end = period_end
    record.usage_period_start = period_start
    record.usage_period_end = period_end
    record.monthly_call_limit = 2_000 if plan == "STARTER" else 5_000
    record.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    if changed_period:
        record.monthly_calls_used = 0
        record.usage_warning_sent_at = None
    session.execute(update(ApiClient).where(ApiClient.owner_user_id == user_id).values(plan=plan))


def process_stripe_event(session: Session, settings: Settings, event: dict, stripe: StripeClient | None = None) -> bool:
    event_id, event_type = event.get("id"), event.get("type")
    if not event_id or not event_type:
        raise HTTPException(400, detail={"code":"invalid_webhook_event","message":"Stripe event is incomplete"})
    session.add(StripeWebhookEvent(event_id=event_id, event_type=event_type))
    try: session.flush()
    except IntegrityError:
        session.rollback(); return False
    obj = ((event.get("data") or {}).get("object") or {})
    if event_type == "checkout.session.completed":
        subscription_id = _subscription_id(obj)
        subscription_obj = stripe.subscription(subscription_id) if subscription_id and stripe else obj.get("subscription_object")
        reference_user = verified_reference(obj.get("client_reference_id"), settings)
        if subscription_obj: _apply_subscription(session, settings, subscription_obj, reference_user or (obj.get("metadata") or {}).get("user_id"))
    elif event_type == "customer.subscription.updated":
        _apply_subscription(session, settings, obj)
    elif event_type in {"invoice.paid", "invoice.payment_succeeded", "invoice.payment_failed"}:
        subscription_id = _subscription_id(obj)
        record = session.scalar(select(Subscription).where(Subscription.provider_subscription_id == subscription_id)) if subscription_id else None
        if record:
            record.status = "active" if event_type != "invoice.payment_failed" else "past_due"
            if event_type != "invoice.payment_failed":
                end = _timestamp(((obj.get("lines") or {}).get("data") or [{}])[0].get("period", {}).get("end"))
                start = _timestamp(((obj.get("lines") or {}).get("data") or [{}])[0].get("period", {}).get("start"))
                if start and end and record.usage_period_start != start:
                    record.usage_period_start, record.usage_period_end = start, end
                    record.monthly_calls_used, record.usage_warning_sent_at = 0, None
    elif event_type == "customer.subscription.deleted":
        record = session.scalar(select(Subscription).where(Subscription.provider_subscription_id == obj.get("id")))
        if record:
            now = datetime.now(timezone.utc)
            record.plan_code, record.status, record.cancel_at_period_end = "FREE", "active", False
            record.monthly_call_limit, record.monthly_calls_used = 250, 0
            record.usage_period_start = now
            record.usage_period_end = current_month_end()
            record.usage_warning_sent_at = None
            session.execute(update(ApiClient).where(ApiClient.owner_user_id == record.user_id).values(plan="FREE"))
    session.commit()
    return True
