from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.admin_schemas import (
    AdminActivity,
    AdminApiKey,
    AdminDashboardSummary,
    LookupAnalyticsSummary,
    LookupMissItem,
    LookupMissList,
    AdminRegeneratedKey,
    AdminSubscription,
    AdminUserDetails,
    AdminUserList,
    AdminUserListItem,
)
from app.auth import current_period, issue_api_key
from app.config import Settings
from app.models import (
    ApiClient,
    ApiKey,
    AuthSession,
    DailyUsage,
    MonthlyUsage,
    LookupAnalytics,
    Subscription,
    User,
)


def lookup_analytics_summary(session: Session, *, days: int) -> LookupAnalyticsSummary:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    valid, found, unique_gtins, unique_misses, single, batch = session.execute(
        select(
            func.count(LookupAnalytics.id),
            func.count(LookupAnalytics.id).filter(LookupAnalytics.found.is_(True)),
            func.count(func.distinct(LookupAnalytics.canonical_gtin)),
            func.count(func.distinct(LookupAnalytics.canonical_gtin)).filter(LookupAnalytics.found.is_(False)),
            func.count(LookupAnalytics.id).filter(LookupAnalytics.endpoint_type == "single"),
            func.count(LookupAnalytics.id).filter(LookupAnalytics.endpoint_type == "batch"),
        ).where(LookupAnalytics.occurred_at >= since)
    ).one()
    valid = int(valid or 0)
    found = int(found or 0)
    return LookupAnalyticsSummary(
        period_days=days,
        valid_lookups=valid,
        found_lookups=found,
        missed_lookups=valid - found,
        hit_rate=round(found / valid * 100, 1) if valid else None,
        unique_gtins=int(unique_gtins or 0),
        unique_missed_gtins=int(unique_misses or 0),
        single_lookups=int(single or 0),
        batch_lookups=int(batch or 0),
    )


def lookup_misses(session: Session, *, days: int, limit: int) -> LookupMissList:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.execute(
        select(
            LookupAnalytics.canonical_gtin,
            LookupAnalytics.barcode_type,
            func.count(LookupAnalytics.id).label("request_count"),
            func.count(func.distinct(LookupAnalytics.owner_user_id)).label("unique_accounts"),
            func.min(LookupAnalytics.occurred_at).label("first_seen_at"),
            func.max(LookupAnalytics.occurred_at).label("last_seen_at"),
        )
        .where(LookupAnalytics.occurred_at >= since, LookupAnalytics.found.is_(False))
        .group_by(LookupAnalytics.canonical_gtin, LookupAnalytics.barcode_type)
        .order_by(func.count(LookupAnalytics.id).desc(), func.max(LookupAnalytics.occurred_at).desc())
        .limit(limit)
    ).all()
    return LookupMissList(
        period_days=days,
        items=[LookupMissItem(**row._mapping) for row in rows],
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "user_not_found", "message": "The user was not found"},
    )


def _start_of_today() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def dashboard_summary(session: Session) -> AdminDashboardSummary:
    today = _start_of_today()
    month = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
    usage_month = current_period(today)
    return AdminDashboardSummary(
        total_users=session.scalar(select(func.count()).select_from(User)) or 0,
        new_users_today=session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= today)
        ) or 0,
        new_users_this_month=session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= month)
        ) or 0,
        total_api_keys=session.scalar(select(func.count()).select_from(ApiKey)) or 0,
        active_api_keys=session.scalar(
            select(func.count()).select_from(ApiKey).where(ApiKey.active.is_(True))
        ) or 0,
        total_requests_today=session.scalar(
            select(func.coalesce(func.sum(DailyUsage.request_count), 0)).where(
                DailyUsage.usage_date == today.date()
            )
        ) or 0,
        total_requests_this_month=session.scalar(
            select(func.coalesce(func.sum(MonthlyUsage.request_count), 0)).where(
                MonthlyUsage.period_start == usage_month
            )
        ) or 0,
        active_subscriptions=session.scalar(
            select(func.count()).select_from(Subscription).where(
                Subscription.plan_code.in_(["STARTER", "GROWTH"]),
                Subscription.status.in_(["active", "trialing"]),
            )
        ) or 0,
        subscriptions_connected=True,
    )


def list_users(
    session: Session, *, search: str | None, limit: int, offset: int
) -> AdminUserList:
    filters = []
    if search and (term := search.strip()):
        pattern = f"%{term}%"
        filters.append(
            or_(
                User.display_name.ilike(pattern),
                User.email.ilike(pattern),
                User.organization.ilike(pattern),
            )
        )
    where = and_(*filters) if filters else None
    total_query = select(func.count()).select_from(User)
    if where is not None:
        total_query = total_query.where(where)
    total = session.scalar(total_query) or 0
    query = (
        select(User, ApiClient, Subscription)
        .outerjoin(ApiClient, ApiClient.owner_user_id == User.id)
        .outerjoin(Subscription, Subscription.user_id == User.id)
        .order_by(User.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if where is not None:
        query = query.where(where)
    rows = session.execute(query).unique().all()
    items = []
    for user, client, subscription in rows:
        active_keys = session.scalar(
            select(func.count()).select_from(ApiKey).where(
                ApiKey.owner_user_id == user.id, ApiKey.active.is_(True)
            )
        ) or 0
        total_keys = session.scalar(
            select(func.count()).select_from(ApiKey).where(ApiKey.owner_user_id == user.id)
        ) or 0
        key_status = "active" if active_keys else ("revoked" if total_keys else "none")
        items.append(
            AdminUserListItem(
                id=user.id,
                display_name=user.display_name,
                email=user.email,
                organization=user.organization,
                created_at=user.created_at,
                plan=client.plan if client else "—",
                api_key_status=key_status,
                is_admin=user.is_admin,
                active=user.active,
                usage=subscription.monthly_calls_used if subscription else 0,
                usage_limit=subscription.monthly_call_limit if subscription else 0,
                usage_percentage=(round(subscription.monthly_calls_used / subscription.monthly_call_limit * 100, 1) if subscription and subscription.monthly_call_limit else 0),
                subscription_status=subscription.status if subscription else "none",
                usage_period_start=subscription.usage_period_start if subscription else None,
                usage_period_end=subscription.usage_period_end if subscription else None,
            )
        )
    return AdminUserList(items=items, total=total, limit=limit, offset=offset)


def user_details(session: Session, user_id: str) -> AdminUserDetails:
    user = session.get(User, user_id)
    if user is None:
        raise _not_found()
    client = session.scalar(select(ApiClient).where(ApiClient.owner_user_id == user.id))
    keys = session.scalars(
        select(ApiKey)
        .where(ApiKey.owner_user_id == user.id)
        .order_by(ApiKey.created_at.desc())
    ).all()
    today = _start_of_today().date()
    period = current_period()
    key_ids = [key.id for key in keys]
    request_today = request_month = lookup_month = 0
    if key_ids:
        request_today = session.scalar(
            select(func.coalesce(func.sum(DailyUsage.request_count), 0)).where(
                DailyUsage.api_key_id.in_(key_ids), DailyUsage.usage_date == today
            )
        ) or 0
        request_month, lookup_month = session.execute(
            select(
                func.coalesce(func.sum(MonthlyUsage.request_count), 0),
                func.coalesce(func.sum(MonthlyUsage.lookup_count), 0),
            ).where(
                MonthlyUsage.api_key_id.in_(key_ids),
                MonthlyUsage.period_start == period,
            )
        ).one()
    subscription = session.scalar(
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.created_at.desc())
    )
    activity = [AdminActivity(kind="registered", label="Account registered", occurred_at=user.created_at)]
    if user.last_login_at:
        activity.append(AdminActivity(kind="login", label="Last signed in", occurred_at=user.last_login_at))
    for key in keys:
        activity.append(AdminActivity(kind="api_key", label=f"API key {key.key_prefix} created", occurred_at=key.created_at))
        if key.last_used_at:
            activity.append(AdminActivity(kind="request", label=f"API key {key.key_prefix} used", occurred_at=key.last_used_at))
    activity.sort(key=lambda item: item.occurred_at, reverse=True)
    return AdminUserDetails(
        id=user.id,
        display_name=user.display_name,
        email=user.email,
        organization=user.organization,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        active=user.active,
        is_admin=user.is_admin,
        plan=client.plan if client else "—",
        api_keys=[AdminApiKey.model_validate(key, from_attributes=True) for key in keys],
        request_count_today=request_today,
        request_count_month=request_month,
        lookup_count_month=lookup_month,
        usage_period=period,
        subscription=(
            AdminSubscription(
                plan=subscription.plan_code,
                status=subscription.status,
                provider=subscription.provider,
                current_period_end=subscription.current_period_end,
                monthly_calls_used=subscription.monthly_calls_used,
                monthly_call_limit=subscription.monthly_call_limit,
                usage_percentage=(round(subscription.monthly_calls_used / subscription.monthly_call_limit * 100, 1) if subscription.monthly_call_limit else 0),
                usage_period_start=subscription.usage_period_start,
                usage_period_end=subscription.usage_period_end,
            )
            if subscription else None
        ),
        recent_activity=activity[:12],
    )


def set_account_active(
    session: Session, *, actor_id: str, user_id: str, active: bool
) -> AdminUserDetails:
    user = session.get(User, user_id)
    if user is None:
        raise _not_found()
    if user.id == actor_id and not active:
        raise HTTPException(
            status_code=409,
            detail={"code": "self_disable_forbidden", "message": "Administrators cannot disable their own account"},
        )
    user.active = active
    session.execute(
        update(ApiClient).where(ApiClient.owner_user_id == user.id).values(active=active)
    )
    if not active:
        now = datetime.now(timezone.utc)
        session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    session.commit()
    return user_details(session, user.id)


def set_admin_role(
    session: Session, *, actor_id: str, user_id: str, is_admin: bool
) -> AdminUserDetails:
    user = session.get(User, user_id)
    if user is None:
        raise _not_found()
    if user.id == actor_id and not is_admin:
        raise HTTPException(
            status_code=409,
            detail={"code": "self_demotion_forbidden", "message": "Administrators cannot demote themselves"},
        )
    if user.is_admin and not is_admin:
        remaining = session.scalar(
            select(func.count()).select_from(User).where(
                User.is_admin.is_(True), User.active.is_(True), User.id != user.id
            )
        ) or 0
        if remaining == 0:
            raise HTTPException(
                status_code=409,
                detail={"code": "last_admin_required", "message": "At least one active administrator is required"},
            )
    user.is_admin = is_admin
    session.commit()
    return user_details(session, user.id)


def regenerate_user_key(
    session: Session,
    settings: Settings,
    *,
    user_id: str,
    key_id: str | None,
    name: str,
) -> AdminRegeneratedKey:
    user = session.get(User, user_id)
    if user is None:
        raise _not_found()
    client = session.scalar(select(ApiClient).where(ApiClient.owner_user_id == user.id))
    if client is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "account_not_provisioned", "message": "The user has no API client"},
        )
    old_key = None
    if key_id:
        old_key = session.scalar(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.owner_user_id == user.id).with_for_update()
        )
        if old_key is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "api_key_not_found", "message": "The API key was not found"},
            )
    else:
        old_key = session.scalar(
            select(ApiKey)
            .where(ApiKey.owner_user_id == user.id, ApiKey.active.is_(True))
            .order_by(ApiKey.created_at.desc())
            .with_for_update()
        )
    if old_key:
        old_key.active = False
    key, raw_key = issue_api_key(
        session, client, settings, name=(old_key.name if old_key and old_key.name else name), commit=False
    )
    key.owner_user_id = user.id
    session.commit()
    session.refresh(key)
    return AdminRegeneratedKey(
        id=key.id, key_prefix=key.key_prefix, api_key=raw_key, created_at=key.created_at
    )
