"""Independent, non-renewing 500-call purchases, consumed by earliest expiry."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from fastapi import HTTPException
from app.models import CallPack, Subscription, ApiClient

def utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

def active_packs(session, user_id, now=None, lock=False):
    now = now or datetime.now(timezone.utc)
    query = select(CallPack).where(CallPack.user_id == user_id, CallPack.expires_at > now, CallPack.used < CallPack.calls).order_by(CallPack.expires_at, CallPack.id)
    if lock: query = query.with_for_update()
    return list(session.scalars(query))

def sync(session, record, now=None):
    if record is None or record.plan_code not in {"FREE", "ONE_TIME"}: return []
    packs = active_packs(session, record.user_id, now)
    if not packs and record.plan_code == "FREE": return []
    if packs:
        record.plan_code = "ONE_TIME"
        record.monthly_call_limit = sum(p.calls for p in packs)
        record.monthly_calls_used = sum(p.used for p in packs)
        record.usage_period_start = min(utc(p.activated_at) for p in packs)
        record.usage_period_end = min(utc(p.expires_at) for p in packs)
        record.billing_interval = "one_time"
        record.status = "active"
    elif record.plan_code == "ONE_TIME":
        record.plan_code = "FREE"
        record.monthly_call_limit = 250
        record.billing_interval = "month"
    session.execute(update(ApiClient).where(ApiClient.owner_user_id == record.user_id).values(plan=record.plan_code))
    session.flush()
    return packs

def activate(session, obj):
    if obj.get('payment_status') != 'paid': return
    if obj.get('amount_total') != 499 or obj.get('currency') != 'usd':
        raise HTTPException(400, detail='Unexpected one-time payment amount')
    uid = (obj.get('metadata') or {}).get('user_id')
    record = session.scalar(select(Subscription).where(Subscription.user_id == uid).order_by(Subscription.created_at.desc()).with_for_update())
    if not record: raise HTTPException(400, detail='Pack account not found')
    if session.scalar(select(CallPack.id).where(CallPack.checkout_session_id == obj['id'])): return
    # Activation is when this verified payment is first granted, not checkout creation.
    now = datetime.now(timezone.utc)
    session.add(CallPack(user_id=uid, checkout_session_id=obj['id'], calls=500, used=0, activated_at=now, expires_at=now+timedelta(days=30)))
    session.flush()
    if record.plan_code in {"FREE", "ONE_TIME"}:
        record.provider_customer_id = obj.get('customer') or record.provider_customer_id
    sync(session, record, now)

def details(session, user_id):
    return [{"id":p.id, "calls":p.calls, "used":p.used, "remaining":p.calls-p.used, "activated_at":utc(p.activated_at).isoformat(), "expires_at":utc(p.expires_at).isoformat()} for p in active_packs(session,user_id)]

def consume(session, record, units, now):
    packs = active_packs(session, record.user_id, now, lock=True)
    total = sum(p.calls for p in packs)
    used = sum(p.used for p in packs)
    if units > total-used:
        raise HTTPException(429, detail={"code":"usage_limit_reached", "message":"Your one-time call pack has insufficient calls. Buy another pack to continue.", "allowance_type":"one_time", "plan":"one_time", "used":used, "limit":total, "upgrade_url":"https://barcodenest.com/billing/"})
    remaining = units
    for pack in packs:
        take = min(remaining, pack.calls-pack.used)
        pack.used += take
        remaining -= take
        if remaining == 0: break
    record.monthly_calls_used = used+units
    record.monthly_call_limit = total
    session.flush()
    return used+units, total
