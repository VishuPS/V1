from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.auth import AuthContext, current_period
from app.config import Settings
from app.models import ApiKey, ServiceUsage, Subscription, new_uuid

CATEGORIES = ("generation", "labels", "enrichment", "scans")


def account_scope(context: AuthContext) -> str:
    owner = context.api_key.owner_user_id or context.client.owner_user_id
    return f"user:{owner}" if owner else f"client:{context.client.id}"


def limits_for(session: Session, context: AuthContext, settings: Settings):
    owner = context.api_key.owner_user_id or context.client.owner_user_id
    plan = context.client.plan.upper()
    if owner:
        subscription = session.scalar(
            select(Subscription).where(Subscription.user_id == owner)
            .order_by(Subscription.created_at.desc()).limit(1)
        )
        if subscription:
            plan = subscription.plan_code.upper()
    limits = settings.service_limits.get(plan)
    if limits is None:
        raise HTTPException(503, detail={"code": "service_plan_unavailable", "message": "Tool allowances are not configured for this plan"})
    return limits


def quota_headers(category: str, limit: int, used: int, period) -> dict[str, str]:
    return {
        "X-Service-Category": category,
        "X-Service-Quota-Limit": str(limit),
        "X-Service-Quota-Remaining": str(max(0, limit - used)),
        "X-Service-Quota-Period": period.isoformat(),
    }


def consume_service_usage(session: Session, context: AuthContext, settings: Settings,
                          category: str, units: int, *, now: datetime | None = None) -> dict[str, str]:
    if category not in CATEGORIES or units < 1:
        raise ValueError("Invalid service usage")
    timestamp = now or datetime.now(timezone.utc)
    period = current_period(timestamp)
    limit = getattr(limits_for(session, context, settings), category)
    scope = account_scope(context)
    if limit == 0:
        raise HTTPException(403, detail={
            "code": "service_not_included", "category": category,
            "message": f"This plan provides no {category} units. Choose a plan with this service.",
        }, headers=quota_headers(category, limit, 0, period))
    error = HTTPException(429, detail={
        "code": "service_quota_exceeded", "category": category,
        "message": f"The monthly {category} allowance is exhausted or too small for this request",
    }, headers=quota_headers(category, limit, limit, period))
    if units > limit:
        used = session.scalar(select(ServiceUsage.units).where(
            ServiceUsage.account_scope == scope, ServiceUsage.category == category,
            ServiceUsage.period_start == period,
        )) or 0
        error.headers = quota_headers(category, limit, used, period)
        raise error
    dialect = session.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("Unsupported usage database")
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert
    statement = insert(ServiceUsage).values(
        id=new_uuid(), account_scope=scope, category=category,
        period_start=period, units=units, updated_at=timestamp,
    )
    updated = session.execute(statement.on_conflict_do_update(
        index_elements=[ServiceUsage.account_scope, ServiceUsage.category, ServiceUsage.period_start],
        set_={"units": ServiceUsage.units + units, "updated_at": timestamp},
        where=ServiceUsage.units + units <= limit,
    ).returning(ServiceUsage.units)).scalar_one_or_none()
    if updated is None:
        session.rollback()
        raise error
    session.execute(update(ApiKey).where(ApiKey.id == context.api_key.id).values(last_used_at=timestamp))
    session.commit()
    return quota_headers(category, limit, updated, period)


def service_balances(session: Session, context: AuthContext, settings: Settings) -> dict:
    period = current_period()
    limits = limits_for(session, context, settings)
    used = dict(session.execute(select(ServiceUsage.category, ServiceUsage.units).where(
        ServiceUsage.account_scope == account_scope(context), ServiceUsage.period_start == period,
    )).all())
    return {
        "batch_generation_max": limits.batch_generation_max,
        "period_start": period, "reset": "First day of each month at 00:00 UTC",
        "categories": {category: {"limit": getattr(limits, category), "used": used.get(category, 0),
                                  "remaining": max(0, getattr(limits, category) - used.get(category, 0))}
                       for category in CATEGORIES},
    }
